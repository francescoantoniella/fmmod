#pragma once

/**
 * Costanti per il modulatore FM MPX a 912 kHz.
 * Sample rate unico: tutto il processing a 912 kHz (cache-friendly, no resampling in loop).
 */

namespace mpx {

constexpr int SAMPLE_RATE_HZ       = 912000;   // Rate unico di lavoro
constexpr int AUDIO_IN_RATE_HZ     = 48000;   // PCM in ingresso (es. da FFmpeg)
constexpr int UPSAMPLE_FACTOR      = SAMPLE_RATE_HZ / AUDIO_IN_RATE_HZ;  // 19

// Sottoportanti MPX
constexpr int PILOT_HZ             = 19000;
constexpr int STEREO_SUBCARRIER_HZ = 38000;
constexpr int RDS_SUBCARRIER_HZ    = 57000;

// Dimensioni LUT (nessun sin/cos nei loop)
constexpr int PILOT_LUT_SIZE       = SAMPLE_RATE_HZ / PILOT_HZ;           // 48
constexpr int STEREO_LUT_SIZE      = SAMPLE_RATE_HZ / STEREO_SUBCARRIER_HZ; // 24
// RDS: 768 campioni/bit; portante = stessa LUT 48 campioni con stride 3 (57 kHz = 3×19 kHz)

// Modulazione FM in uscita (±75 kHz deviazione standard broadcast)
constexpr int FM_DEVIATION_HZ = 75000;

} // namespace mpx
