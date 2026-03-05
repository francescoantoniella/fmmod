#pragma once

#include "constants.hpp"
#include "globals.hpp"
#include "rds_manager.hpp"
#include <cmath>
#include <cstddef>

#ifdef __ARM_NEON
#include <arm_neon.h>
#endif

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/**
 * Modulatore MPX a 912 kHz.
 * LUT per pilot/stereo/RDS (no sin nei loop critici).
 * Indici LUT in int (la LUT ha 48 campioni esatti → nessun drift di fase possibile).
 * Loop di mix accelerato con NEON su ARM.
 */
class MpxModulator {
public:
    MpxModulator() { init_luts(); }

    void process(float* mpx_out, const float* mono_912k, const float* stereo_912k,
                 int num_samples, RDSManager& rds, GlobalSettings& settings) {
        if (num_samples <= 0 || num_samples > RDS_BUF_MAX) return;

        const float vol_pilot  = settings.vol_pilot.load(std::memory_order_relaxed);
        const float vol_mono   = settings.vol_mono.load(std::memory_order_relaxed);
        const float vol_stereo = settings.vol_stereo.load(std::memory_order_relaxed);
        const float vol_rds    = settings.vol_rds.load(std::memory_order_relaxed);

        const bool pilot_on  = (vol_pilot  > 0.f);
        const bool stereo_on = (vol_stereo > 0.f);
        const bool rds_on    = (vol_rds    > 0.f);

        if (rds_on)
            rds.get_rds_samples(rds_buf_, num_samples);

        const float* lut = sin_lut_;

#ifdef __ARM_NEON
        // ── Loop NEON: processa 4 campioni alla volta ─────────────────────────
        // Mix mono+pilot+stereo+rds → hard clip ±1 in uscita
        const float32x4_t v_vmono   = vdupq_n_f32(vol_mono);
        const float32x4_t v_vpilot  = vdupq_n_f32(vol_pilot);
        const float32x4_t v_vstereo = vdupq_n_f32(vol_stereo);
        const float32x4_t v_vrds    = vdupq_n_f32(vol_rds);

        const unsigned n = static_cast<unsigned>(num_samples);
        unsigned i = 0;
        for (; i + 4 <= n; i += 4) {
            // Carica mono e stereo
            float32x4_t s = vmulq_f32(vld1q_f32(&mono_912k[i]), v_vmono);

            if (pilot_on) {
                // 4 campioni LUT pilot consecutivi
                float p0 = lut[pilot_idx_];                      pilot_idx_ = (pilot_idx_+1) % mpx::PILOT_LUT_SIZE;
                float p1 = lut[pilot_idx_];                      pilot_idx_ = (pilot_idx_+1) % mpx::PILOT_LUT_SIZE;
                float p2 = lut[pilot_idx_];                      pilot_idx_ = (pilot_idx_+1) % mpx::PILOT_LUT_SIZE;
                float p3 = lut[pilot_idx_];                      pilot_idx_ = (pilot_idx_+1) % mpx::PILOT_LUT_SIZE;
                float32x4_t vp = { p0, p1, p2, p3 };
                s = vfmaq_f32(s, vp, v_vpilot);
            }

            if (stereo_on) {
                float c0 = lut[stereo_idx_]; stereo_idx_ = (stereo_idx_+2) % mpx::PILOT_LUT_SIZE;
                float c1 = lut[stereo_idx_]; stereo_idx_ = (stereo_idx_+2) % mpx::PILOT_LUT_SIZE;
                float c2 = lut[stereo_idx_]; stereo_idx_ = (stereo_idx_+2) % mpx::PILOT_LUT_SIZE;
                float c3 = lut[stereo_idx_]; stereo_idx_ = (stereo_idx_+2) % mpx::PILOT_LUT_SIZE;
                float32x4_t vc = { c0, c1, c2, c3 };
                float32x4_t vs = vld1q_f32(&stereo_912k[i]);
                s = vfmaq_f32(s, vmulq_f32(vc, vs), v_vstereo);
            }

            if (rds_on)
                s = vfmaq_f32(s, vld1q_f32(&rds_buf_[i]), v_vrds);

            // Hard clip ±1 (protezione emergenza, lineare nel range normale)
            const float32x4_t v_one  = vdupq_n_f32( 1.f);
            const float32x4_t v_mone = vdupq_n_f32(-1.f);
            vst1q_f32(&mpx_out[i], vminq_f32(v_one, vmaxq_f32(v_mone, s)));
        }
        // Residuo scalare (0–3 campioni)
        for (; i < n; i++) {
            float s = vol_mono * mono_912k[i];
            if (pilot_on)  { s += vol_pilot  * lut[pilot_idx_];  pilot_idx_  = (pilot_idx_ +1) % mpx::PILOT_LUT_SIZE; }
            if (stereo_on) { s += vol_stereo * lut[stereo_idx_] * stereo_912k[i]; stereo_idx_ = (stereo_idx_+2) % mpx::PILOT_LUT_SIZE; }
            if (rds_on)    { s += vol_rds * rds_buf_[i]; }
            // Hard clip ±1
            mpx_out[i] = s >  1.f ?  1.f : s < -1.f ? -1.f : s;
        }

#else
        // ── Loop scalare (non-ARM o NEON non disponibile) ─────────────────────
        for (int i = 0; i < num_samples; i++) {
            float s = vol_mono * mono_912k[i];
            if (pilot_on)  { s += vol_pilot  * lut[pilot_idx_];                pilot_idx_  = (pilot_idx_ +1) % mpx::PILOT_LUT_SIZE; }
            if (stereo_on) { s += vol_stereo * lut[stereo_idx_] * stereo_912k[i]; stereo_idx_ = (stereo_idx_+2) % mpx::PILOT_LUT_SIZE; }
            if (rds_on)    { s += vol_rds    * rds_buf_[i]; }
            // Hard clip ±1
            mpx_out[i] = s >  1.f ?  1.f : s < -1.f ? -1.f : s;
        }
#endif
    }

    static constexpr int upsample_factor()    { return mpx::UPSAMPLE_FACTOR; }
    static constexpr int max_chunk_samples()  { return RDS_BUF_MAX; }

private:
    static constexpr int RDS_BUF_MAX = 9120;

    const float* sin_lut_;
    float rds_buf_[RDS_BUF_MAX];
    int pilot_idx_  = 0;
    int stereo_idx_ = 0;

    void init_luts() { sin_lut_ = get_mpx_sin_lut(); }
};
