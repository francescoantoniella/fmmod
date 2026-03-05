#pragma once
#include "constants.hpp"
#include <algorithm>
#include <cmath>
#include <cstddef>

/**
 * FmModulator — modulazione FM del segnale MPX baseband.
 *
 * Fix rispetto alla versione originale:
 *  1. LUT[LUT_SIZE+1]: ultimo elemento = 1.0f esatto → cos(0) = 1.0 senza errore di bordo.
 *  2. Phase wrap con fmodf → robusto anche per salti > 2π (es. transitorio all'avvio).
 *  3. FM_GAIN usa la costante two_pi completa (6.283185307f) anziché π hardcoded×2.
 *  4. Interpolazione lineare sulla LUT → errore dimezzato a costo trascurabile.
 *
 * Integrazione di fase:
 *   φ[n] = φ[n-1] + 2π · fd · mpx[n] / fs
 * Uscita IQ interleaved (I,Q,I,Q,...):
 *   I = scale · cos(φ),  Q = scale · sin(φ)
 */
class FmModulator {
public:
    FmModulator() { init_lut(); }

    /**
     * Produce num_samples campioni IQ interleaved in iq_out (dimensione: 2*num_samples float).
     * @param mpx        segnale MPX normalizzato ([-1, 1])
     * @param iq_out     buffer di output, deve avere almeno 2*num_samples elementi
     * @param num_samples numero di campioni da produrre
     */
    void process(const float* mpx, float* iq_out, int num_samples) {
        for (int i = 0; i < num_samples; i++) {
            // FIX #2: accumulo + wrap universale con fmodf
            phase_ += FM_GAIN * mpx[i];
            phase_ = std::fmod(phase_, TWO_PI);
            if (phase_ < 0.0f) phase_ += TWO_PI;

            float c, s;
            lookup(phase_, c, s);
            iq_out[i * 2]     = IQ_SCALE * c;
            iq_out[i * 2 + 1] = IQ_SCALE * s;
        }
    }

    void reset_phase() { phase_ = 0.0f; }

private:
    // -----------------------------------------------------------------------
    // Costanti
    // -----------------------------------------------------------------------
    static constexpr float TWO_PI  = 6.283185307179586f;
    static constexpr float HALF_PI = 1.5707963267948966f;   // π/2

    static constexpr float FM_DEV  = static_cast<float>(mpx::FM_DEVIATION_HZ);
    static constexpr float FM_FS   = static_cast<float>(mpx::SAMPLE_RATE_HZ);

    // FIX #3: usa TWO_PI coerente anziché 2.0f * 3.14159265f
    static constexpr float FM_GAIN = TWO_PI * FM_DEV / FM_FS;

    static constexpr float IQ_SCALE = 0.7f;

    // -----------------------------------------------------------------------
    // LUT
    // -----------------------------------------------------------------------
    // FIX #1: LUT_SIZE+1 elementi → sin_lut_[LUT_SIZE] = sin(π/2) = 1.0f esatto.
    // Questo elimina l'errore di bordo su cos(0) che nella versione originale
    // restituiva sin_lut_[LUT_SIZE-1] = sin((LUT_SIZE-1)/LUT_SIZE · π/2) < 1.
    static constexpr int   LUT_SIZE  = 8192;
    static constexpr float LUT_SCALE = static_cast<float>(LUT_SIZE) / HALF_PI;

    float phase_ = 0.0f;
    float sin_lut_[LUT_SIZE + 1];   // [0..LUT_SIZE] inclusive

    void init_lut() {
        for (int i = 0; i <= LUT_SIZE; i++)
            sin_lut_[i] = std::sin(static_cast<float>(i) * HALF_PI
                                   / static_cast<float>(LUT_SIZE));
        // sin_lut_[LUT_SIZE] == sin(π/2) == 1.0f  ✓
    }

    /**
     * Dato phase in [0, 2π), scrive cos(phase) e sin(phase) via LUT con
     * FIX #4: interpolazione lineare → errore max dimezzato.
     *
     * Struttura quadranti (q = floor(phase / π/2)):
     *   q=0: sin=sin(l),  cos=cos(l)=sin(π/2-l)
     *   q=1: sin=cos(l),  cos=-sin(l)
     *   q=2: sin=-sin(l), cos=-cos(l)
     *   q=3: sin=-cos(l), cos=sin(l)
     * dove l = phase - q·π/2  ∈ [0, π/2)
     */
    inline void lookup(float phase, float& cos_out, float& sin_out) const {
        const int   q     = static_cast<int>(phase / HALF_PI) & 3;
        const float local = phase - static_cast<float>(q) * HALF_PI;

        // FIX #4: indice frazionario per interpolazione lineare
        const float fidx  = local * LUT_SCALE;
        const int   idx   = static_cast<int>(fidx);
        const float frac  = fidx - static_cast<float>(idx);

        // idx è sicuramente in [0, LUT_SIZE-1] perché local < π/2
        // ma clamp difensivo per floating-point edge case
        const int idx_clamped = (idx < LUT_SIZE) ? idx : LUT_SIZE - 1;

        // sin(local) interpolato
        const float v = sin_lut_[idx_clamped]
                      + frac * (sin_lut_[idx_clamped + 1] - sin_lut_[idx_clamped]);

        // cos(local) = sin(π/2 - local): indice complementare nella LUT
        // Con LUT_SIZE+1 entry, sin_lut_[LUT_SIZE - idx] = sin(π/2 - idx/LUT_SIZE·π/2)
        const int   cidx  = LUT_SIZE - idx_clamped;
        // Per il complemento l'interpolazione va in senso inverso (frac negativa)
        const float w = sin_lut_[cidx]
                      - frac * (sin_lut_[cidx] - sin_lut_[cidx - 1]);

        switch (q) {
            case 0: sin_out =  v;  cos_out =  w;  break;
            case 1: sin_out =  w;  cos_out = -v;  break;
            case 2: sin_out = -v;  cos_out = -w;  break;
            default: sin_out = -w; cos_out =  v;  break;  // q == 3
        }
    }
};
