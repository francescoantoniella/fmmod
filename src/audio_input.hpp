#pragma once

#include "config.hpp"
#include <cstddef>

/**
 * Lettura PCM stereo 48 kHz, s16le (L,R,L,R...).
 * STDIN: blocco continuo; UDP: pacchetti (es. 480 campioni * 4 byte = 1920 byte per pacchetto).
 */
class AudioInput {
public:
    explicit AudioInput(const Config& cfg);
    ~AudioInput();

    /// Legge esattamente num_samples per canale, converte in float [-1,1]. Ritorna false su EOF/errore.
    bool read(float* left, float* right, int num_samples);

private:
    Config config_;
    int udp_socket_ = -1;
    static constexpr int BYTES_PER_SAMPLE = 2;
};
