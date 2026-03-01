#include "audio_pipeline.hpp"
#include "audio_input.hpp"
#include "compressor.hpp"
#include "constants.hpp"
#include "mpx_modulator.hpp"
#include "pluto_output.hpp"
#include "upsampler.hpp"
#include <cmath>
#include <cstdio>
#include <thread>
#include <vector>
#ifdef __linux__
#include <unistd.h>
#endif

namespace {

constexpr int CHUNK_48K   = 480;
constexpr int CHUNK_912K  = CHUNK_48K * mpx::UPSAMPLE_FACTOR;  // 9120
constexpr int SAMPLE_RATE_48K = 48000;

constexpr float TEST_TONE_AMP = 0.5f;
constexpr int   TEST_PHASE_DURATION_SAMPLES = 5 * SAMPLE_RATE_48K;

constexpr float SEPARATION_TONE_AMP    = 0.5f;
constexpr int   SEPARATION_TONE_HZ     = 1000;
constexpr int   SEPARATION_L_ONLY_SAMPLES  = 10 * SAMPLE_RATE_48K;
constexpr int   SEPARATION_SILENCE_SAMPLES =  2 * SAMPLE_RATE_48K;
constexpr int   SEPARATION_R_ONLY_SAMPLES  = 10 * SAMPLE_RATE_48K;

// ── Input gain ───────────────────────────────────────────────────────────────
inline void apply_input_gain(float* L, float* R, int n, float gain_db) {
    if (gain_db == 0.f) return;
    const float factor = std::pow(10.f, gain_db / 20.f);
    for (int i = 0; i < n; i++) { L[i] *= factor; R[i] *= factor; }
}

// ── Hard limiter (protezione picchi prima del compressore) ───────────────────
class AudioLimiter {
public:
    void process(float* L, float* R, int n,
                 float threshold = 0.99f, float release_ms = 100.f) {
        const float release_coeff =
            std::exp(-1.f / (SAMPLE_RATE_48K * release_ms * 1e-3f));
        for (int i = 0; i < n; i++) {
            float peak = std::max(std::abs(L[i]), std::abs(R[i]));
            if (peak * gain_ > threshold) {
                gain_ = threshold / peak;
            } else {
                gain_ = 1.f - (1.f - gain_) * release_coeff;
                if (gain_ > 1.f) gain_ = 1.f;
            }
            L[i] *= gain_;
            R[i] *= gain_;
        }
    }
    float current_gain_db() const {
        return 20.f * std::log10(gain_ > 1e-6f ? gain_ : 1e-6f);
    }
private:
    float gain_ = 1.f;
};

// ── De-enfasi ────────────────────────────────────────────────────────────────
inline void apply_deemphasis(float* L, float* R, int n, float tau_us,
                              float& stL, float& stR) {
    if (tau_us <= 0.f) return;
    const float alpha = std::exp(-1e6f / (static_cast<float>(SAMPLE_RATE_48K) * tau_us));
    const float a1 = 1.f - alpha;
    for (int i = 0; i < n; i++) {
        stL = alpha * stL + a1 * L[i]; L[i] = stL;
        stR = alpha * stR + a1 * R[i]; R[i] = stR;
    }
}

// ── Pre-enfasi ───────────────────────────────────────────────────────────────
inline void apply_pre_emphasis(float* L, float* R, int n, float alpha,
                                float& stL, float& stR) {
    if (alpha <= 0.f) return;
    for (int i = 0; i < n; i++) {
        float l = L[i], r = R[i];
        L[i] = l - alpha * stL;
        R[i] = r - alpha * stR;
        stL = l; stR = r;
    }
}

// ── Statistiche debug ────────────────────────────────────────────────────────
void stats_48k(const float* L, const float* R, int n,
               float& min_out, float& max_out, float& rms_out) {
    float minv = 0, maxv = 0, sum2 = 0;
    for (int i = 0; i < n; i++) {
        float m = L[i] + R[i];
        if (m < minv) minv = m;
        if (m > maxv) maxv = m;
        sum2 += m * m;
    }
    min_out = minv; max_out = maxv;
    rms_out = std::sqrt(sum2 / n);
}

void stats_mpx(const float* mpx, int n,
               float& min_out, float& max_out, float& rms_out) {
    float minv = mpx[0], maxv = mpx[0], sum2 = 0;
    for (int i = 0; i < n; i++) {
        float v = mpx[i];
        if (v < minv) minv = v;
        if (v > maxv) maxv = v;
        sum2 += v * v;
    }
    min_out = minv; max_out = maxv;
    rms_out = std::sqrt(sum2 / n);
}

// ── Aggiorna CompressorParams da GlobalSettings ──────────────────────────────
inline CompressorParams load_comp_params(const GlobalSettings& s) {
    CompressorParams p;
    p.enabled      = s.comp_enabled.load(std::memory_order_relaxed);
    p.threshold_db = s.comp_threshold_db.load(std::memory_order_relaxed);
    p.ratio        = s.comp_ratio.load(std::memory_order_relaxed);
    p.knee_db      = s.comp_knee_db.load(std::memory_order_relaxed);
    p.attack_ms    = s.comp_attack_ms.load(std::memory_order_relaxed);
    p.release_ms   = s.comp_release_ms.load(std::memory_order_relaxed);
    p.makeup_db    = s.comp_makeup_db.load(std::memory_order_relaxed);
    p.limit        = s.comp_limit.load(std::memory_order_relaxed);
    return p;
}

// ── Scrivi metering compressore su GlobalSettings ────────────────────────────
inline void store_comp_metering(GlobalSettings& s, const SingleBandCompressor& c) {
    s.comp_gr_db.store(c.current_gr_db(),       std::memory_order_relaxed);
    s.comp_input_rms_db.store(c.current_rms_db(),std::memory_order_relaxed);
    s.comp_output_peak_db.store(c.current_output_peak(), std::memory_order_relaxed);
}

// ── Scrivi metering MPX su GlobalSettings ────────────────────────────────────
inline void store_mpx_metering(GlobalSettings& s, const float* mpx, int n) {
    float peak = 0.f, sum2 = 0.f;
    for (int i = 0; i < n; i++) {
        float v = std::abs(mpx[i]);
        if (v > peak) peak = v;
        sum2 += mpx[i] * mpx[i];
    }
    s.mpx_peak.store(peak, std::memory_order_relaxed);
    s.mpx_rms.store(std::sqrt(sum2 / n), std::memory_order_relaxed);
}

} // namespace

// ─────────────────────────────────────────────────────────────────────────────
// Modalità MPX-stdin: legge float32 mono a 192kHz da stdin,
// ricampiona a 912kHz (ratio 19:4 esatto) e scrive sull'output FM.
// Bypass completo di encoder stereo, compressore, RDS locale.
// ─────────────────────────────────────────────────────────────────────────────
static void audio_mpx_stdin_loop(GlobalSettings& settings, const Config& config) {
    PlutoOutput output(config, &settings);

    // Ratio: 912000 / 192000 = 19/4  →  ogni 1920 campioni in → 9120 campioni out
    constexpr int IN_RATE   = 192000;
    constexpr int IN_CHUNK  = CHUNK_912K * 4 / 19;  // = 1920
    static_assert(IN_CHUNK * 19 == CHUNK_912K * 4, "ratio 19:4 non esatto");

    std::vector<float> in_buf(IN_CHUNK);
    std::vector<float> mpx_out(CHUNK_912K);

    float prev = 0.f;  // ultimo campione del chunk precedente per interpolazione

    while (true) {
        // Leggi IN_CHUNK float32 da stdin
        size_t got = 0;
        const size_t bytes = IN_CHUNK * sizeof(float);
        while (got < bytes) {
            ssize_t n = ::read(0,
                reinterpret_cast<uint8_t*>(in_buf.data()) + got,
                bytes - got);
            if (n <= 0) return;  // EOF o errore
            got += static_cast<size_t>(n);
        }

        // Ricampionamento lineare 192kHz → 912kHz (19:4)
        // Per ogni campione output k: fase frazionaria = k * 4/19
        // indice input = floor(fase), frac = fase - floor(fase)
        for (int k = 0; k < CHUNK_912K; k++) {
            // fase in input[0..IN_CHUNK-1], estesa con prev per k vicini a 0
            // fase_exact = k * (4.0/19.0)
            // Usiamo interi: num = k*4, den = 19
            int   idx_num = k * 4;
            int   idx     = idx_num / 19;
            float frac    = (idx_num % 19) * (1.f / 19.f);
            float a = (idx == 0) ? prev : in_buf[idx - 1];
            float b = (idx < IN_CHUNK) ? in_buf[idx] : in_buf[IN_CHUNK - 1];
            mpx_out[k] = a + frac * (b - a);
        }
        prev = in_buf[IN_CHUNK - 1];

        store_mpx_metering(settings, mpx_out.data(), CHUNK_912K);
        output.write(mpx_out.data(), CHUNK_912K);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
void audio_processing_thread(GlobalSettings& settings, const Config& config) {
    if (config.input_mode == InputMode::MpxStdin) {
        audio_mpx_stdin_loop(settings, config);
        return;
    }

    RDSManager        rds;
    MpxModulator      mpx_mod;
    AudioInput        input(config);
    PlutoOutput       output(config, &settings);
    SingleBandCompressor compressor;

    PolyphaseUpsampler up_L, up_R;
    if (config.debug)
        up_L.debug_freq_response();

    std::vector<float> in_L_48k(CHUNK_48K, 0.f);
    std::vector<float> in_R_48k(CHUNK_48K, 0.f);
    std::vector<float> L_912k(CHUNK_912K), R_912k(CHUNK_912K);
    std::vector<float> mono_912k(CHUNK_912K), stereo_912k(CHUNK_912K);
    std::vector<float> mpx_out(CHUNK_912K);

    AudioLimiter limiter;
    float de_state_L = 0.f, de_state_R = 0.f;
    float pre_state_L = 0.f, pre_state_R = 0.f;
    int   chunk_count = 0;
    const int debug_interval = 100;  // ~1 s a 10 ms/chunk

    // ── Helper: processa un chunk 48k → 912k → MPX → output ─────────────────
    // Usato sia dai loop di test che dal loop principale.
    auto process_chunk = [&](bool apply_comp) {
        rds.update(settings);

        if (apply_comp) {
            CompressorParams cp = load_comp_params(settings);
            compressor.process(in_L_48k.data(), in_R_48k.data(), CHUNK_48K, cp);
            store_comp_metering(settings, compressor);
        }

        float de_us = settings.deemphasis_us.load(std::memory_order_relaxed);
        apply_deemphasis(in_L_48k.data(), in_R_48k.data(), CHUNK_48K,
                         de_us, de_state_L, de_state_R);

        float pre_us  = settings.preemphasis_us.load(std::memory_order_relaxed);
        float pre_alpha = (pre_us <= 0.f) ? 0.f
                        : std::exp(-1e6f / (48000.f * pre_us));
        apply_pre_emphasis(in_L_48k.data(), in_R_48k.data(), CHUNK_48K,
                           pre_alpha, pre_state_L, pre_state_R);

        up_L.process_block(in_L_48k.data(), CHUNK_48K, L_912k.data());
        up_R.process_block(in_R_48k.data(), CHUNK_48K, R_912k.data());

        for (int i = 0; i < CHUNK_912K; i++) {
            mono_912k[i]   = L_912k[i] + R_912k[i];
            stereo_912k[i] = L_912k[i] - R_912k[i];
        }

        mpx_mod.process(mpx_out.data(), mono_912k.data(), stereo_912k.data(),
                        CHUNK_912K, rds, settings);
        store_mpx_metering(settings, mpx_out.data(), CHUNK_912K);
        output.write(mpx_out.data(), CHUNK_912K);
    };

    // ── Test separazione stereo ───────────────────────────────────────────────
    if (config.test_separation) {
        std::fprintf(stderr,
            "[test-separation] Tono 1 kHz @ -6 dB. "
            "Fasi: 10s solo L → 2s sil → 10s solo R → 2s sil (×2)\n");
        constexpr float two_pi = 6.283185307179586f;
        const int phase_duration[] = {
            SEPARATION_L_ONLY_SAMPLES, SEPARATION_SILENCE_SAMPLES,
            SEPARATION_R_ONLY_SAMPLES, SEPARATION_SILENCE_SAMPLES
        };
        const char* phase_name[] = {
            "solo L (1 kHz)", "silenzio", "solo R (1 kHz)", "silenzio"
        };
        int phase = 0, phase_sample = 0, cycle = 0;
        constexpr int total_cycles = 2;

        while (cycle < total_cycles) {
            if (phase_sample == 0)
                std::fprintf(stderr, "[test-separation] Ciclo %d/%d — %s\n",
                             cycle+1, total_cycles, phase_name[phase]);

            int to_fill = std::min(phase_duration[phase] - phase_sample, CHUNK_48K);
            float t0 = static_cast<float>(phase_sample) / SAMPLE_RATE_48K;

            for (int i = 0; i < CHUNK_48K; i++) {
                if (i < to_fill) {
                    float t    = t0 + static_cast<float>(i) / SAMPLE_RATE_48K;
                    float tone = SEPARATION_TONE_AMP * std::sin(two_pi * SEPARATION_TONE_HZ * t);
                    in_L_48k[i] = (phase == 0) ? tone : 0.f;
                    in_R_48k[i] = (phase == 2) ? tone : 0.f;
                } else {
                    in_L_48k[i] = in_R_48k[i] = 0.f;
                }
            }
            phase_sample += to_fill;
            if (phase_sample >= phase_duration[phase]) {
                phase_sample = 0;
                if (++phase >= 4) { phase = 0; ++cycle; }
            }
            process_chunk(false);  // test: no compressore
        }
        std::fprintf(stderr, "[test-separation] Fine.\n");
        return;
    }

    // ── Test stereo L/R ───────────────────────────────────────────────────────
    if (config.test_stereo) {
        std::fprintf(stderr,
            "[test-stereo] 0-5s: 1kHz solo L | 5-10s: 1kHz solo R | 10-15s: L=1k R=2k\n");
        constexpr float two_pi = 6.283185307179586f;
        int total = 0;
        const int duration = 3 * TEST_PHASE_DURATION_SAMPLES;
        while (total < duration) {
            for (int i = 0; i < CHUNK_48K; i++) {
                float t  = static_cast<float>(total + i) / SAMPLE_RATE_48K;
                float s1 = TEST_TONE_AMP * std::sin(two_pi * 1000.f * t);
                float s2 = TEST_TONE_AMP * std::sin(two_pi * 2000.f * t);
                int   s  = total + i;
                in_L_48k[i] = (s < TEST_PHASE_DURATION_SAMPLES)     ? s1
                            : (s < 2*TEST_PHASE_DURATION_SAMPLES)    ? 0.f : s1;
                in_R_48k[i] = (s < TEST_PHASE_DURATION_SAMPLES)     ? 0.f
                            : (s < 2*TEST_PHASE_DURATION_SAMPLES)    ? s1  : s2;
            }
            total += CHUNK_48K;
            process_chunk(false);
        }
        std::fprintf(stderr, "[test-stereo] Fine (15 s).\n");
        return;
    }

    // ── Loop principale ───────────────────────────────────────────────────────
    while (true) {
        rds.update(settings);

        bool got = input.read(in_L_48k.data(), in_R_48k.data(), CHUNK_48K);
        if (!got) break;

        // Gain ingresso (o mute)
        float gain_db = settings.mute.load(std::memory_order_relaxed)
            ? -1000.f
            : settings.input_gain_db.load(std::memory_order_relaxed);
        apply_input_gain(in_L_48k.data(), in_R_48k.data(), CHUNK_48K, gain_db);

        // Hard limiter (protezione picchi estremi, soglia 0.99)
        limiter.process(in_L_48k.data(), in_R_48k.data(), CHUNK_48K, 0.99f, 100.f);

        // Compressore/limiter monobanda
        {
            CompressorParams cp = load_comp_params(settings);
            compressor.process(in_L_48k.data(), in_R_48k.data(), CHUNK_48K, cp);
            store_comp_metering(settings, compressor);
        }

        // De-enfasi (opzionale)
        float de_us = settings.deemphasis_us.load(std::memory_order_relaxed);
        apply_deemphasis(in_L_48k.data(), in_R_48k.data(), CHUNK_48K,
                         de_us, de_state_L, de_state_R);

        // Pre-enfasi (opzionale)
        float pre_us    = settings.preemphasis_us.load(std::memory_order_relaxed);
        float pre_alpha = (pre_us <= 0.f) ? 0.f
                        : std::exp(-1e6f / (48000.f * pre_us));
        apply_pre_emphasis(in_L_48k.data(), in_R_48k.data(), CHUNK_48K,
                           pre_alpha, pre_state_L, pre_state_R);

        // Upsampling 48k → 912k
        up_L.process_block(in_L_48k.data(), CHUNK_48K, L_912k.data());
        up_R.process_block(in_R_48k.data(), CHUNK_48K, R_912k.data());

        // L+R / L−R
        for (int i = 0; i < CHUNK_912K; i++) {
            mono_912k[i]   = L_912k[i] + R_912k[i];
            stereo_912k[i] = L_912k[i] - R_912k[i];
        }

        // Modulatore MPX
        mpx_mod.process(mpx_out.data(), mono_912k.data(), stereo_912k.data(),
                        CHUNK_912K, rds, settings);

        // Metering MPX
        store_mpx_metering(settings, mpx_out.data(), CHUNK_912K);

        // Output (Pluto o stdout)
        output.write(mpx_out.data(), CHUNK_912K);

        // Debug ~1 s
        if (settings.debug.load(std::memory_order_relaxed) &&
            ++chunk_count >= debug_interval) {
            chunk_count = 0;
            float in_min, in_max, in_rms, mpx_min, mpx_max, mpx_rms;
            stats_48k(in_L_48k.data(), in_R_48k.data(), CHUNK_48K,
                      in_min, in_max, in_rms);
            stats_mpx(mpx_out.data(), CHUNK_912K, mpx_min, mpx_max, mpx_rms);
            std::fprintf(stderr,
                "[debug] IN: min=%.3f max=%.3f rms=%.3f | "
                "MPX: min=%.3f max=%.3f rms=%.3f | "
                "lim:%.1fdB comp_GR:%.1fdB\n",
                in_min, in_max, in_rms,
                mpx_min, mpx_max, mpx_rms,
                limiter.current_gain_db(),
                settings.comp_gr_db.load(std::memory_order_relaxed));
        }
    }
}
