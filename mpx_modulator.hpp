#pragma once

#include "constants.hpp"
#include "globals.hpp"
#include "rds_manager.hpp"
#include <cmath>
#include <cstddef>

/**
 * Modulatore MPX a 912 kHz.
 * Solo LUT nei loop di processing (no sin/cos) - ottimizzato per CM4.
 * Catena: Mono (L+R) + Pilot 19 kHz + Stereo DSB-SC 38 kHz + RDS 57 kHz.
 */
class MpxModulator {
public:
    MpxModulator() { init_luts(); }

    /**
     * Genera un blocco MPX a 912 kHz.
     * mono_912k e stereo_912k (L-R) devono essere già a 912 kHz; RDS generato internamente.
     * num_samples deve essere <= max_chunk_samples() (cache-friendly).
     * vol_pilot=0 → nessun pilot (mono puro per il ricevitore).
     * vol_stereo=0 → nessuna sottoportante DSB-SC 38 kHz (mono puro).
     * vol_rds=0   → nessuna sottoportante RDS 57 kHz (get_rds_samples non chiamata).
     * Tutti e tre supportano zero: il valore è accettato dal parser UDP (VOL_*=0).
     */
    void process(float* mpx_out, const float* mono_912k, const float* stereo_912k,
                 int num_samples, RDSManager& rds, GlobalSettings& settings) {
        if (num_samples <= 0 || num_samples > RDS_BUF_MAX) return;
        float vol_pilot = settings.vol_pilot.load(std::memory_order_relaxed);
        float vol_mono = settings.vol_mono.load(std::memory_order_relaxed);
        float vol_stereo = settings.vol_stereo.load(std::memory_order_relaxed);
        float vol_rds = settings.vol_rds.load(std::memory_order_relaxed);

        // Genera campioni RDS solo se il volume è non-zero (evita lavoro inutile e
        // mantiene la macchina a stati RDS in pausa quando il sottoportante è spento)
        const bool rds_on    = (vol_rds    > 0.f);
        const bool stereo_on = (vol_stereo > 0.f);
        const bool pilot_on  = (vol_pilot  > 0.f);

        if (rds_on)
            rds.get_rds_samples(rds_buf_, num_samples);

        const float* sin_lut = sin_lut_;
        for (int i = 0; i < num_samples; i++) {
            // mono e stereo stesso indice i → allineati (stesso numero di passaggi in pipeline)
            float s = vol_mono * mono_912k[i];
            if (pilot_on)
                s += vol_pilot * sin_lut[pilot_idx_];
            if (stereo_on)
                s += vol_stereo * sin_lut[stereo_idx_] * stereo_912k[i];
            if (rds_on)
                s += vol_rds * rds_buf_[i];
            pilot_idx_ = (pilot_idx_ + 1) % mpx::PILOT_LUT_SIZE;
            stereo_idx_ = (stereo_idx_ + 2) % mpx::PILOT_LUT_SIZE;  // stride 2 (38 kHz)
            // Soft limit (tanh): evita clipping e armoniche; scala così che tanh(1)=1
            constexpr float inv_tanh1 = 1.3130352854993312f;  // 1/tanh(1)
            mpx_out[i] = std::tanh(s) * inv_tanh1;
            // Per eliminare il soft limiter: commenta le 3 righe sopra e usa:
            //mpx_out[i] = s;
        }
    }

    static constexpr int upsample_factor() { return mpx::UPSAMPLE_FACTOR; }
    static constexpr int max_chunk_samples() { return RDS_BUF_MAX; }

private:
    static constexpr int RDS_BUF_MAX = 9120;  // >= CHUNK_912K (480*19)

    const float* sin_lut_;   /* LUT condivisa 48 campioni (librds): pilot stride 1, stereo 2 */
    float rds_buf_[RDS_BUF_MAX];
    int pilot_idx_ = 0;
    int stereo_idx_ = 0;

    void init_luts() {
        sin_lut_ = get_mpx_sin_lut();
    }
};
