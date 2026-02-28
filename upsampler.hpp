#pragma once

#include "constants.hpp"
#include <cmath>
#include <cstdio>
#include <vector>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/**
 * Upsampling 48 kHz -> 912 kHz (19×) con Filtro FIR Polifase.
 * Qualità Broadcast: taglia tutto sopra i 15-16 kHz per proteggere il pilota a 19 kHz.
 */
class PolyphaseUpsampler {
private:
    static constexpr int F = 19;             // Fattore di interpolazione
    static constexpr int TAPS_PER_PHASE = 128; // Precisione del filtro
    
    // Matrice dei coefficienti (pre-calcolati per efficienza)
    float coeffs[F][TAPS_PER_PHASE];
    float history[TAPS_PER_PHASE];
    int head;

public:
    PolyphaseUpsampler() : head(0) {
        static constexpr int TOTAL_TAPS = F * TAPS_PER_PHASE;
        for (int i = 0; i < TAPS_PER_PHASE; i++) history[i] = 0.0f;

        const double fc = 15500.0 / 912000.0; 
        const double center = (TOTAL_TAPS - 1) / 2.0;

        for (int p = 0; p < F; p++) {
            double phase_sum = 0.0;
            // Primo passaggio: calcolo sinc e finestra
            for (int t = 0; t < TAPS_PER_PHASE; t++) {
                double n = (double)(t * F + p) - center;
                double val;

                if (std::abs(n) < 1e-11) {
                    val = 2.0 * fc;
                } else {
                    double angle = 2.0 * M_PI * fc * n;
                    val = std::sin(angle) / (M_PI * n);
                }

                // Finestra di Blackman-Harris (più precisa)
                double arg = (double)(t * F + p) / (double)(TOTAL_TAPS - 1);
                double window = 0.35875 
                              - 0.48829 * std::cos(2.0 * M_PI * arg)
                              + 0.14128 * std::cos(4.0 * M_PI * arg)
                              - 0.01168 * std::cos(6.0 * M_PI * arg);
                
                coeffs[p][t] = (float)(val * window);
                phase_sum += (double)coeffs[p][t];
            }

            // NORMALIZZAZIONE CRUCIALE: ogni fase deve sommare a 1.0
            float phase_scale = (float)(1.0 / phase_sum);
            for (int t = 0; t < TAPS_PER_PHASE; t++) {
                coeffs[p][t] *= phase_scale;
            }
        }
    }    

    /** Risposta in frequenza (fase 0) a 48 kHz: stampa |H(f)| in dB per alcune frequenze. */
    void debug_freq_response() const {
        const float fs_in = 48000.0f;
        const int nf = 5;
        const float freq_Hz[nf] = { 0.0f, 1000.0f, 10000.0f, 15500.0f, 19000.0f };
        std::fprintf(stderr, "[upsampler] Risposta in frequenza (fase 0, input 48 kHz):\n");
        for (int k = 0; k < nf; k++) {
            float omega = 2.0f * static_cast<float>(M_PI) * freq_Hz[k] / fs_in;
            float re = 0.0f, im = 0.0f;
            for (int t = 0; t < TAPS_PER_PHASE; t++) {
                re += coeffs[0][t] * std::cos(omega * static_cast<float>(t));
                im -= coeffs[0][t] * std::sin(omega * static_cast<float>(t));
            }
            float mag = std::sqrt(re * re + im * im);
            float db = (mag > 1e-12f) ? (20.0f * std::log10(mag)) : -100.0f;
            std::fprintf(stderr, "  %6.0f Hz  ->  %+.2f dB  (|H| = %.4f)\n", freq_Hz[k], db, mag);
        }
        float sum0 = 0.0f;
        for (int t = 0; t < TAPS_PER_PHASE; t++) sum0 += coeffs[0][t];
        std::fprintf(stderr, "  somma fase 0 = %.6f\n", sum0);
        float sum_all = 0.0f;
        for (int p = 0; p < F; p++)
            for (int t = 0; t < TAPS_PER_PHASE; t++) sum_all += coeffs[p][t];
        std::fprintf(stderr, "  somma tutti i %d coeff = %.6f (atteso %d)\n", F * TAPS_PER_PHASE, sum_all, F);
    }

    /**
     * Prende un blocco a 48kHz e produce un blocco a 912kHz.
     * Molto più veloce di un FIR standard perché calcola solo i punti necessari.
     */
    inline void process_block(const float* in_48k, int num_in, float* out_912k) {
        for (int i = 0; i < num_in; i++) {
            // Aggiorna buffer circolare della storia
            history[head] = in_48k[i];
            
            // Per ogni campione in ingresso, genera 19 fasi (campioni in uscita)
            for (int p = 0; p < F; p++) {
                float sum = 0.0f;
                for (int t = 0; t < TAPS_PER_PHASE; t++) {
                    int hist_idx = (head - t + TAPS_PER_PHASE) % TAPS_PER_PHASE;
                    sum += history[hist_idx] * coeffs[p][t];
                }
                out_912k[i * F + p] = sum;
            }
            
            head = (head + 1) % TAPS_PER_PHASE;
        }
    }
};