#pragma once

#include "globals.hpp"
#include <mutex>

extern "C" {
#include "rds.h"
}

/**
 * Controller C++ per il core RDS in C (librds).
 * Bridge tra GlobalSettings e le funzioni C set_rds_* / get_rds_samples.
 * RDS a 912 kHz; portante usa la stessa LUT sin 48 campioni di pilot/stereo con stride 3.
 */
class RDSManager {
public:
    RDSManager() { init_rds_luts(); }

    /// Sincronizza sempre PS/RT/PI/AF1 da GlobalSettings a librds; chiamare dal thread audio.
    void update(GlobalSettings& s) {
        std::lock_guard<std::mutex> lock(s.rds_mutex);
        set_rds_ps(const_cast<char*>(s.ps_name.c_str()));
        set_rds_rt(const_cast<char*>(s.rt_text.c_str()));
        set_rds_pi(s.rds_pi_code);
        set_rds_pty(s.rds_pty);
        set_rds_ta(s.rds_ta);
        set_rds_tp(s.rds_tp);
        set_rds_ms(s.rds_ms);
        set_rds_af1(s.rds_af1);
        set_rds_af2(s.rds_af2);
    }

    /// Riempie buffer con campioni RDS a 912 kHz (solo LUT, no math in loop). Usare nel thread audio.
    void get_rds_samples(float* buffer, int count) const {
        ::get_rds_samples(buffer, count);
    }
};
