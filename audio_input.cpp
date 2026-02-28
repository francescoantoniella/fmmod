#include "audio_input.hpp"
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <vector>

#ifdef __linux__
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace {

inline float s16_to_float(short s) {
    return s * (1.0f / 32768.0f);
}

} // namespace

AudioInput::AudioInput(const Config& cfg) : config_(cfg) {
#ifdef __linux__
    if (config_.input_mode == InputMode::Udp) {
        udp_socket_ = socket(AF_INET, SOCK_DGRAM, 0);
        if (udp_socket_ >= 0) {
            sockaddr_in addr{};
            addr.sin_family = AF_INET;
            addr.sin_addr.s_addr = INADDR_ANY;
            addr.sin_port = htons(static_cast<uint16_t>(config_.udp_audio_port));
            if (bind(udp_socket_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
                std::cerr << "audio_input: bind UDP " << config_.udp_audio_port << " failed\n";
                close(udp_socket_);
                udp_socket_ = -1;
            }
        } else {
            std::cerr << "audio_input: socket failed\n";
        }
    }
#endif
}

AudioInput::~AudioInput() {
#ifdef __linux__
    if (udp_socket_ >= 0) close(udp_socket_);
#endif
}

bool AudioInput::read(float* left, float* right, int num_samples) {
    const size_t num_bytes = static_cast<size_t>(num_samples) * 2 * BYTES_PER_SAMPLE;

#ifdef __linux__
    if (config_.input_mode == InputMode::Udp) {
        if (udp_socket_ < 0) return false;
        std::vector<uint8_t> raw(num_bytes);
        size_t got = 0;
        sockaddr_in from{};
        socklen_t fromlen = sizeof(from);
        while (got < num_bytes) {
            ssize_t n = recvfrom(udp_socket_, raw.data() + got, num_bytes - got, 0,
                                 reinterpret_cast<sockaddr*>(&from), &fromlen);
            if (n <= 0) return false;
            got += static_cast<size_t>(n);
        }
        const int16_t* s = reinterpret_cast<const int16_t*>(raw.data());
        for (int i = 0; i < num_samples; i++) {
            left[i]  = s16_to_float(s[i * 2]);
            right[i] = s16_to_float(s[i * 2 + 1]);
        }
        return true;
    }
#endif

    // STDIN: raw s16le stereo (L,R,L,R...)
    std::vector<uint8_t> raw(num_bytes);
    size_t got = 0;
    while (got < num_bytes) {
        ssize_t n = ::read(0, raw.data() + got, num_bytes - got);
        if (n <= 0) return false;
        got += static_cast<size_t>(n);
    }
    const int16_t* s = reinterpret_cast<const int16_t*>(raw.data());
    for (int i = 0; i < num_samples; i++) {
        left[i]  = s16_to_float(s[i * 2]);
        right[i] = s16_to_float(s[i * 2 + 1]);
    }
    // Diagnostica primo chunk da stdin (per verificare se arrivano zeri)
    static bool first_stdin_log = true;
    if (first_stdin_log) {
        first_stdin_log = false;
        float minv = left[0], maxv = left[0], sum2 = 0.f;
        for (int i = 0; i < num_samples; i++) {
            float L = left[i], R = right[i];
            if (L < minv) minv = L;
            if (L > maxv) maxv = L;
            if (R < minv) minv = R;
            if (R > maxv) maxv = R;
            sum2 += L * L + R * R;
        }
        float rms = std::sqrt(sum2 / (2 * num_samples));
        std::fprintf(stderr, "[audio_input] Primo chunk stdin: %d campioni, min=%.4f max=%.4f rms=%.4f\n",
                     num_samples, minv, maxv, rms);
        if (maxv == 0.f && minv == 0.f)
            std::fprintf(stderr, "[audio_input] ATTENZIONE: solo zeri da stdin. Verificare ffmpeg/stream.\n");
    }
    return true;
}
