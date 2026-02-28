#pragma once
#include "config.hpp"
#include "constants.hpp"
#include "fm_modulator.hpp"

#include <atomic>
#include <cstddef>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <vector>

#ifdef USE_LIBIIO
#include "iio_compat.hpp"
#endif

/**
 * PlutoOutput — uscita verso PlutoSDR (via libiio) o stdout.
 *
 * Modalità:
 *  - config.use_pluto == true  → campioni IQ int16 inviati via libiio al PlutoSDR.
 *  - config.use_pluto == false && config.output_fm_iq == true  → stdout IQ int16 (come Pluto).
 *  - config.use_pluto == false && config.output_fm_iq == false → stdout MPX float32.
 *
 * Fix rispetto alla versione originale:
 *  5. Configurazione completa Pluto: LO frequency, rf_bandwidth, hardwaregain.
 *  6. Compatibilità libiio v0/v1 via iio_compat.hpp.
 *  7. Buffer size espresso come costante leggibile (BUF_MS).
 *  8. Coda stdout con backpressure (MAX_QUEUE_DEPTH).
 *  9. Conversione float→int16 prima della scrittura nel buffer IIO.
 */
struct GlobalSettings;

class PlutoOutput {
public:
    /** gsettings opzionale: se non null, frequenza e gain Pluto si aggiornano da UDP a runtime. */
    PlutoOutput(const Config& cfg, GlobalSettings* gsettings = nullptr);
    ~PlutoOutput();

    /** Invia num_samples campioni MPX (float, [-1,1]) verso Pluto o stdout. */
    void write(const float* mpx, int num_samples);

    bool ok() const { return ok_; }

private:
    // -----------------------------------------------------------------------
    // Costanti
    // -----------------------------------------------------------------------

    /** Dimensione del buffer IIO in millisecondi. */
    static constexpr int    BUF_MS          = 10;
    static constexpr int    BUF_SAMPLES     = BUF_MS * mpx::SAMPLE_RATE_HZ / 1000; // = 9120 @ 912 kHz

    /** Profondità massima coda stdout — backpressure (FIX #8). */
    static constexpr size_t MAX_QUEUE_DEPTH = 8;

    // -----------------------------------------------------------------------
    // Stato
    // -----------------------------------------------------------------------
    const Config& config_;
    bool          ok_  = false;

    FmModulator   fm_modulator_;

    // Buffer temporanei per la conversione float→int16
    std::vector<float>   iq_float_buf_;
    std::vector<int16_t> iq_int16_buf_;   // usato sia per Pluto che per stdout IQ

    // -----------------------------------------------------------------------
    // Thread writer per stdout (solo !use_pluto)
    // -----------------------------------------------------------------------
    std::thread             writer_thread_;
    std::atomic<bool>       writer_quit_{false};
    std::deque<std::vector<uint8_t>> stdout_queue_;
    std::mutex              queue_mutex_;
    std::condition_variable cv_not_empty_;
    std::condition_variable cv_not_full_;

    void writer_loop();
    void enqueue_stdout(const void* data, size_t bytes);

    // -----------------------------------------------------------------------
    // Stato per aggiornamento runtime Pluto (da GlobalSettings)
    // -----------------------------------------------------------------------
    GlobalSettings* gsettings_     = nullptr;
    float           last_freq_mhz_ = -1.f;
    float           last_gain_db_  = -999.f;

    // -----------------------------------------------------------------------
    // libiio (solo USE_LIBIIO && use_pluto)
    // -----------------------------------------------------------------------
#ifdef USE_LIBIIO
    void apply_pluto_attrs(float freq_mhz, float gain_db);
    iio_context* iio_ctx_    = nullptr;
    iio_device*  iio_dev_    = nullptr;
    iio_buffer*  iio_tx_buf_ = nullptr;
    int16_t*     buf_ptr_    = nullptr;   // FIX #9: puntatore int16, non float
    int          buf_capacity_ = 0;

    bool init_pluto();
#endif
};
