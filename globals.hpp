#pragma once
#include <atomic>
#include <cstdint>
#include <string>
#include <mutex>

struct GlobalSettings {
    // ── Gain ingresso ────────────────────────────────────────────────────────
    std::atomic<float> input_gain_db{0.f};
    std::atomic<bool>  mute{false};

    // ── Volumi MPX ───────────────────────────────────────────────────────────
    std::atomic<float> vol_pilot{0.09f};
    std::atomic<float> vol_rds{0.03f};
    std::atomic<float> vol_mono{0.44f};
    std::atomic<float> vol_stereo{0.44f};

    // ── Enfasi ───────────────────────────────────────────────────────────────
    std::atomic<float> deemphasis_us{0.f};   // 0=off; 50=EU; 75=US
    std::atomic<float> preemphasis_us{0.f};  // 0=off; 50=EU; 75=US

    // ── Debug ────────────────────────────────────────────────────────────────
    std::atomic<bool> debug{false};

    // ── PlutoSDR ─────────────────────────────────────────────────────────────
    std::atomic<float> tx_frequency_mhz{100.f};
    std::atomic<float> tx_gain_db{-17.f};

    // ── Compressore — parametri (scritti da UDP, letti dal thread audio) ─────
    std::atomic<bool>  comp_enabled{true};
    std::atomic<float> comp_threshold_db{-18.f};
    std::atomic<float> comp_ratio{4.f};
    std::atomic<float> comp_knee_db{6.f};
    std::atomic<float> comp_attack_ms{5.f};
    std::atomic<float> comp_release_ms{150.f};
    std::atomic<float> comp_makeup_db{0.f};
    std::atomic<float> comp_limit{0.99f};    // 0 = hard limiter finale off

    // ── Compressore — metering (scritti dal thread audio, letti da UDP GET) ──
    std::atomic<float> comp_gr_db{0.f};          // gain reduction corrente (<=0)
    std::atomic<float> comp_input_rms_db{-60.f}; // RMS ingresso compressore
    std::atomic<float> comp_output_peak_db{-60.f};

    // ── MPX metering (scritti dal thread audio) ───────────────────────────────
    std::atomic<float> mpx_peak{0.f};
    std::atomic<float> mpx_rms{0.f};

    // ── RDS (protetti da mutex) ───────────────────────────────────────────────
    std::mutex rds_mutex;
    std::string ps_name    = "MY_RADIO";
    std::string rt_text    = "Benvenuti su Cursor Radio";
    uint16_t    rds_pi_code = 0x5253;
    int         rds_af1    = 0;
    int         rds_af2    = 0;
    uint8_t     rds_pty    = 2;
    int         rds_ta     = 0;
    int         rds_tp     = 0;   // Traffic Programme: 0=no, 1=yes
    int         rds_ms     = 1;   // Music/Speech: 1=Music, 0=Speech
    bool        rds_dirty  = true;
};
