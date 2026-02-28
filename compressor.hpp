#pragma once

/**
 * SingleBandCompressor — compressore/limiter stereo-link a singola banda.
 *
 * Architettura:
 *   audio stereo 48 kHz
 *     → RMS detector (attack/release separati)
 *     → gain computer con soft knee
 *     → gain smoother (smoothing sul gain in dB)
 *     → apply gain stereo
 *     → hard limiter finale
 *
 * Parametri:
 *   threshold_db  soglia in dBFS         (default -18)
 *   ratio         rapporto di compressione (default 4.0; ∞ = limiter)
 *   knee_db       ampiezza soft knee      (default 6 dB)
 *   attack_ms     costante di tempo RMS attack  (default 5 ms)
 *   release_ms    costante di tempo RMS release (default 150 ms)
 *   makeup_db     guadagno di makeup post-compressione (default 0)
 *   limit         hard limiter finale: 0 = off, altrimenti soglia peak (default 0.99)
 *
 * Stereo-link: il gain viene calcolato su max(|L|, |R|) e applicato a entrambi i canali,
 * garantendo che l'immagine stereo non si sposti durante la compressione.
 *
 * Configurabile via UDP con gli stessi comandi del multibanda:
 *   COMP_THR=<dBFS>   COMP_RATIO=<x>   COMP_ATK=<ms>
 *   COMP_REL=<ms>     COMP_MU=<dB>     COMP_KNEE=<dB>
 *   COMP_LIM=<0-1>    COMP_EN=0|1
 */

#include <cmath>
#include <cstdio>
#include <algorithm>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

struct CompressorParams {
    float threshold_db = -18.f;
    float ratio        =   4.f;
    float knee_db      =   6.f;   // soft knee: 0 = hard knee
    float attack_ms    =   5.f;
    float release_ms   = 150.f;
    float makeup_db    =   0.f;
    float limit        =  0.99f;  // hard limiter finale (0 = off)
    bool  enabled      = true;
};

class SingleBandCompressor {
public:
    explicit SingleBandCompressor(float fs = 48000.f) : fs_(fs) {}

    // Processa un blocco stereo in-place
    void process(float* L, float* R, int n, const CompressorParams& p) {
        if (!p.enabled || n <= 0) return;

        const float alpha_a  = std::exp(-1.f / (fs_ * p.attack_ms  * 1e-3f));
        const float alpha_r  = std::exp(-1.f / (fs_ * p.release_ms * 1e-3f));
        const float makeup   = std::pow(10.f, p.makeup_db / 20.f);
        // Smooth sul gain in dB: costante di tempo = release/4 (evita discontinuità)
        const float alpha_gs = std::exp(-1.f / (fs_ * p.release_ms * 1e-3f * 0.25f));

        for (int i = 0; i < n; i++) {
            // ── Stereo-link sidechain ──────────────────────────────────────
            float peak  = std::max(std::abs(L[i]), std::abs(R[i]));
            float peak2 = peak * peak;

            // ── RMS detector con attack/release separati ───────────────────
            float alpha = (peak2 > rms_state_) ? alpha_a : alpha_r;
            rms_state_  = alpha * rms_state_ + (1.f - alpha) * peak2;
            float rms   = std::sqrt(rms_state_);

            // ── Gain computer con soft knee ────────────────────────────────
            float rms_db = (rms > 1e-6f) ? 20.f * std::log10(rms) : -120.f;
            float target_db = gain_computer(rms_db, p.threshold_db, p.ratio, p.knee_db);

            // ── Smooth sul gain (in dB) ────────────────────────────────────
            gain_db_ = alpha_gs * gain_db_ + (1.f - alpha_gs) * target_db;

            // ── Applica gain + makeup ──────────────────────────────────────
            float gain_lin = std::pow(10.f, gain_db_ / 20.f) * makeup;
            L[i] *= gain_lin;
            R[i] *= gain_lin;

            // ── Hard limiter finale ────────────────────────────────────────
            if (p.limit > 0.f) {
                if (L[i] >  p.limit) L[i] =  p.limit;
                if (L[i] < -p.limit) L[i] = -p.limit;
                if (R[i] >  p.limit) R[i] =  p.limit;
                if (R[i] < -p.limit) R[i] = -p.limit;
            }
        }

        // Aggiorna metering
        float peak_chunk = 0.f, rms_acc = 0.f;
        for (int i = 0; i < n; i++) {
            float a = std::max(std::abs(L[i]), std::abs(R[i]));
            if (a > peak_chunk) peak_chunk = a;
            rms_acc += a * a;
        }
        output_peak_db_ = (peak_chunk > 1e-6f)
            ? 20.f * std::log10(peak_chunk) : -60.f;
        output_rms_db_  = (rms_acc > 0.f)
            ? 20.f * std::log10(std::sqrt(rms_acc / n)) : -60.f;
    }

    // Metering
    float current_gr_db()       const { return gain_db_; }
    float current_output_peak() const { return output_peak_db_; }
    float current_output_rms()  const { return output_rms_db_; }
    float current_rms_db()      const {
        return (rms_state_ > 1e-12f) ? 10.f * std::log10(rms_state_) : -60.f;
    }

    void reset() { rms_state_ = 0.f; gain_db_ = 0.f; }

    void print_debug() const {
        std::fprintf(stderr, "[comp] GR:%.1fdB  out_peak:%.1fdBFS  out_rms:%.1fdBFS\n",
                     gain_db_, output_peak_db_, output_rms_db_);
    }

private:
    float fs_;
    float rms_state_     = 0.f;
    float gain_db_       = 0.f;   // gain reduction corrente in dB (<=0)
    float output_peak_db_ = -60.f;
    float output_rms_db_  = -60.f;

    /**
     * Gain computer con soft knee.
     *
     * Sotto la knee: nessuna compressione (gain = 0 dB).
     * Nella knee: interpolazione quadratica tra 0 e compressione piena.
     * Sopra la knee: compressione lineare con ratio dato.
     *
     * Ritorna il gain da applicare in dB (<=0).
     */
    static float gain_computer(float in_db, float thr, float ratio, float knee) {
        float half_knee = knee * 0.5f;
        float over      = in_db - thr;

        if (over < -half_knee) {
            // Sotto la soglia (con margine knee): nessuna compressione
            return 0.f;
        } else if (over <= half_knee) {
            // Nella zona soft knee: interpolazione quadratica
            float t = (over + half_knee) / knee;   // 0..1
            float compressed = t * t * (over + half_knee) / (2.f * ratio);
            float uncompressed = over + half_knee;
            // Gain = differenza tra segnale compresso e originale (nella zona)
            return (compressed - uncompressed) / 2.f;
        } else {
            // Sopra la soglia: compressione piena
            return (over) * (1.f / ratio - 1.f);
        }
    }
};
