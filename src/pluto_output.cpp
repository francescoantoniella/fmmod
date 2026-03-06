#include "pluto_output.hpp"
#include "constants.hpp"
#include "globals.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <iostream>

// ============================================================================
//  Costruttore / Distruttore
// ============================================================================

PlutoOutput::PlutoOutput(const Config& cfg, GlobalSettings* gsettings) : config_(cfg), gsettings_(gsettings) {
    if (!config_.use_pluto) {
        // Modalità stdout: avvia thread writer con coda limitata (FIX #8)
        ok_ = true;
        writer_quit_.store(false);
        writer_thread_ = std::thread(&PlutoOutput::writer_loop, this);
        return;
    }

#ifdef USE_LIBIIO
    ok_ = init_pluto();
#else
    // Compilato senza libiio ma use_pluto==true: fallback trasparente
    std::cerr << "pluto_output: compilato senza USE_LIBIIO, uso stdout\n";
    ok_ = true;
#endif
}

PlutoOutput::~PlutoOutput() {
    if (!config_.use_pluto) {
        writer_quit_.store(true);
        cv_not_empty_.notify_all();
        if (writer_thread_.joinable()) writer_thread_.join();
    }
#ifdef USE_LIBIIO
    if (iio_tx_buf_) iio_buffer_destroy(iio_tx_buf_);
    if (iio_ctx_)    iio_context_destroy(iio_ctx_);
#endif
}

// ============================================================================
//  Inizializzazione Pluto (FIX #5, #6, #7, #9)
// ============================================================================

#ifdef USE_LIBIIO
bool PlutoOutput::init_pluto() {
    // --- Contesto IIO ---
    iio_ctx_ = iio_create_default_context();          // prova USB prima
    if (!iio_ctx_)
        iio_ctx_ = iio_create_network_context("192.168.2.1");
    if (!iio_ctx_) {
        std::cerr << "pluto_output: impossibile aprire contesto IIO (USB o 192.168.2.1)\n";
        return false;
    }

    // --- Device TX DDS ---
    iio_dev_ = iio_context_find_device(iio_ctx_, "cf-ad9361-dds-core-lpc");
    if (!iio_dev_)
        iio_dev_ = iio_context_find_device(iio_ctx_, "cf-ad9361-lpc");
    if (!iio_dev_) {
        std::cerr << "pluto_output: device TX non trovato\n";
        return false;
    }

    // --- Abilita canali I (voltage0) e Q (voltage1) ---
    iio_channel* ch_i = iio_device_find_channel(iio_dev_, "voltage0", true);
    iio_channel* ch_q = iio_device_find_channel(iio_dev_, "voltage1", true);
    if (!ch_i || !ch_q) {
        std::cerr << "pluto_output: canali TX non trovati\n";
        return false;
    }
    iio_channel_enable(ch_i);
    iio_channel_enable(ch_q);

    // --- Configurazione PHY (ad9361-phy) ---
    // FIX #5: aggiunge LO frequency, rf_bandwidth e hardwaregain
    // FIX #6: usa le macro IIO_CH_ATTR_WRITE_* per compatibilità v0/v1
    iio_device* phy = iio_context_find_device(iio_ctx_, "ad9361-phy");
    if (phy) {
        // Canale voltage0 TX: sample rate e bandwidth
        iio_channel* phy_tx = iio_device_find_channel(phy, "voltage0", true);
        if (phy_tx) {
            IIO_CH_ATTR_WRITE_LL(phy_tx, "sampling_frequency",
                                 static_cast<long long>(mpx::SAMPLE_RATE_HZ));

            // rf_bandwidth: tipicamente pari al sample rate o leggermente inferiore
            IIO_CH_ATTR_WRITE_LL(phy_tx, "rf_bandwidth",
                                 static_cast<long long>(mpx::SAMPLE_RATE_HZ));

            // Hardware gain (attenuazione in dBFS): negativo = attenuazione
            // Valore tipico per broadcast FM: regola in base alla potenza desiderata
            IIO_CH_ATTR_WRITE_DBL(phy_tx, "hardwaregain",
                                  static_cast<double>(config_.tx_gain_db));
        }

        // Canale altvoltage1: LO TX frequency in Hz
        iio_channel* lo_tx = iio_device_find_channel(phy, "altvoltage1", true);
        if (lo_tx) {
            IIO_CH_ATTR_WRITE_LL(lo_tx, "frequency",
                                 static_cast<long long>(config_.tx_frequency_hz));
        } else {
            std::cerr << "pluto_output: WARNING - altvoltage1 (LO TX) non trovato\n";
        }
    } else {
        std::cerr << "pluto_output: WARNING - ad9361-phy non trovato, "
                     "sample rate / LO non configurati\n";
    }

    // --- Buffer IIO ---
    // FIX #7: dimensione espressa tramite costante BUF_SAMPLES
    iio_tx_buf_ = iio_device_create_buffer(iio_dev_, BUF_SAMPLES, false);
    if (!iio_tx_buf_) {
        std::cerr << "pluto_output: create_buffer(" << BUF_SAMPLES << ") failed\n";
        return false;
    }

    // FIX #9: il buffer IIO contiene int16 interleaved, NON float
    buf_ptr_      = static_cast<int16_t*>(iio_buffer_start(iio_tx_buf_));
    buf_capacity_ = BUF_SAMPLES;

    last_freq_mhz_ = static_cast<float>(config_.tx_frequency_hz / 1e6);
    last_gain_db_  = config_.tx_gain_db;
    std::cerr << "pluto_output: inizializzato OK "
              << "(SR=" << mpx::SAMPLE_RATE_HZ << " Hz, "
              << "LO=" << config_.tx_frequency_hz << " Hz, "
              << "gain=" << config_.tx_gain_db << " dB)\n";
    return true;
}

void PlutoOutput::apply_pluto_attrs(float freq_mhz, float gain_db) {
    if (!iio_ctx_) return;
    iio_device* phy = iio_context_find_device(iio_ctx_, "ad9361-phy");
    if (!phy) return;
    const long long freq_hz = static_cast<long long>(freq_mhz * 1e6);
    iio_channel* phy_tx = iio_device_find_channel(phy, "voltage0", true);
    if (phy_tx) {
        IIO_CH_ATTR_WRITE_DBL(phy_tx, "hardwaregain", static_cast<double>(gain_db));
    }
    iio_channel* lo_tx = iio_device_find_channel(phy, "altvoltage1", true);
    if (lo_tx) {
        IIO_CH_ATTR_WRITE_LL(lo_tx, "frequency", freq_hz);
    }
    std::cerr << "pluto_output: applicati LO=" << freq_mhz << " MHz, gain=" << gain_db << " dB\n";
}
#endif // USE_LIBIIO

// ============================================================================
//  Writer thread (stdout, FIX #8: backpressure)
// ============================================================================

void PlutoOutput::writer_loop() {
    while (true) {
        std::vector<uint8_t> buf;
        {
            std::unique_lock<std::mutex> lock(queue_mutex_);
            cv_not_empty_.wait(lock, [this] {
                return writer_quit_.load() || !stdout_queue_.empty();
            });
            if (writer_quit_.load() && stdout_queue_.empty())
                return;
            if (stdout_queue_.empty())
                continue;
            buf = std::move(stdout_queue_.front());
            stdout_queue_.pop_front();
        }
        size_t n = buf.size(), written = 0;
        while (written < n) {
            size_t ret = fwrite(buf.data() + written, 1, n - written, stdout);
            if (ret == 0) break;          // stdout chiuso
            written += ret;
        }
    }
}

/** Accoda dati per stdout: se la coda è piena, scarta il frame più vecchio (mai blocca il DSP). */
void PlutoOutput::enqueue_stdout(const void* data, size_t bytes) {
    if (writer_quit_.load()) return;
    std::vector<uint8_t> buf(static_cast<const uint8_t*>(data),
                              static_cast<const uint8_t*>(data) + bytes);
    {
        std::unique_lock<std::mutex> lock(queue_mutex_);
        if (stdout_queue_.size() >= MAX_QUEUE_DEPTH) {
            stdout_queue_.pop_front();   // drop frame più vecchio
            long d = ++dropped_frames_;
            if (d == 1 || d % 100 == 0)
                std::fprintf(stderr, "[output] consumer lento: %ld frame scartati\n", d);
        }
        stdout_queue_.push_back(std::move(buf));
    }
    cv_not_empty_.notify_one();
}

// ============================================================================
//  write() — percorso principale
// ============================================================================

void PlutoOutput::write(const float* mpx, int num_samples) {
    if (!ok_) return;

    // ------------------------------------------------------------------
    // Percorso Pluto (libiio)
    // ------------------------------------------------------------------
#ifdef USE_LIBIIO
    if (config_.use_pluto && iio_tx_buf_ && buf_ptr_) {
        // Aggiorna LO e gain da GlobalSettings se modificati via UDP
        if (gsettings_) {
            const float fm = gsettings_->tx_frequency_mhz.load(std::memory_order_relaxed);
            const float gm = gsettings_->tx_gain_db.load(std::memory_order_relaxed);
            if (std::fabs(fm - last_freq_mhz_) > 0.0001f || std::fabs(gm - last_gain_db_) > 0.0001f) {
                apply_pluto_attrs(fm, gm);
                last_freq_mhz_ = fm;
                last_gain_db_  = gm;
            }
        }

        const int n = std::min(num_samples, buf_capacity_);

        // Assicura buffer float temporaneo
        if (static_cast<int>(iq_float_buf_.size()) < 2 * n)
            iq_float_buf_.resize(static_cast<size_t>(2 * n));

        // Modula FM → float IQ
        fm_modulator_.process(mpx, iq_float_buf_.data(), n);

        // FIX #9: converti float→int16 prima di scrivere nel buffer IIO
        // Il PlutoSDR si aspetta campioni int16 interleaved, NON float.
        for (int i = 0; i < 2 * n; i++) {
            const float v = iq_float_buf_[i] * 32767.0f;
            buf_ptr_[i] = static_cast<int16_t>(
                std::clamp(v, -32768.0f, 32767.0f));
        }

        const ssize_t ret = iio_buffer_push(iio_tx_buf_);
        if (ret < 0)
            std::cerr << "pluto_output: iio_buffer_push failed (" << ret << ")\n";
        if (n < num_samples)
            std::cerr << "pluto_output: scartati " << (num_samples - n)
                      << " campioni (buffer troppo piccolo)\n";
        return;
    }
#endif // USE_LIBIIO

    // ------------------------------------------------------------------
    // Percorso stdout
    // ------------------------------------------------------------------
    if (config_.output_fm_iq) {
        // Output IQ int16 interleaved (stesso formato del file IQ analizzato)
        const size_t n2 = static_cast<size_t>(2 * num_samples);

        if (iq_float_buf_.size() < n2) iq_float_buf_.resize(n2);
        if (iq_int16_buf_.size() < n2) iq_int16_buf_.resize(n2);

        fm_modulator_.process(mpx, iq_float_buf_.data(), num_samples);

        for (size_t i = 0; i < n2; i++) {
            const float v = iq_float_buf_[i] * 32767.0f;
            iq_int16_buf_[i] = static_cast<int16_t>(
                std::clamp(v, -32768.0f, 32767.0f));
        }

        enqueue_stdout(iq_int16_buf_.data(), n2 * sizeof(int16_t));
    } else {
        // Output MPX float32 grezzo
        enqueue_stdout(mpx, static_cast<size_t>(num_samples) * sizeof(float));
    }
}
