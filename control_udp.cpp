#include "control_udp.hpp"
#include "globals.hpp"
#include "rds.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>

#ifdef __linux__
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace {
std::atomic<bool> g_control_running{true};
} // namespace

void control_udp_thread(GlobalSettings& settings) {
#ifdef __linux__
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) { std::cerr << "control_udp: socket failed\n"; return; }

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port        = htons(9120);

    if (bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::cerr << "control_udp: bind 9120 failed\n";
        close(fd); return;
    }

    char buf[1024];
    sockaddr_in from{};
    socklen_t   fromlen = sizeof(from);

    while (g_control_running) {
        ssize_t n = recvfrom(fd, buf, sizeof(buf)-1, 0,
                             reinterpret_cast<sockaddr*>(&from), &fromlen);
        if (n <= 0) continue;
        buf[n] = '\0';

        // Trim trailing whitespace
        while (n > 0 && (buf[n-1]=='\n'||buf[n-1]=='\r'||buf[n-1]==' '))
            buf[--n] = '\0';
        // Trim leading whitespace
        char* start = buf;
        while (*start==' '||*start=='\t') ++start;

        std::string msg(start);

        // ── Helper lambda ─────────────────────────────────────────────────────
        auto startsWith = [&](const char* prefix, size_t len) {
            return msg.compare(0, len, prefix) == 0;
        };

        // ── RDS ───────────────────────────────────────────────────────────────
        if (startsWith("PS=", 3)) {
            std::string val = msg.substr(3);
            while (!val.empty() && (val.back()==' '||val.back()=='\r'||val.back()=='\n'))
                val.pop_back();
            if (val.size() > 8) val.resize(8);
            while (val.size() < 8) val += ' ';
            { std::lock_guard<std::mutex> lk(settings.rds_mutex);
              settings.ps_name = val; settings.rds_dirty = true; }
            std::cerr << "RDS PS=\"" << val << "\"\n";

        } else if (startsWith("RT=", 3)) {
            std::string val = msg.substr(3);
            while (!val.empty() && (val.back()==' '||val.back()=='\r'||val.back()=='\n'))
                val.pop_back();
            if (val.size() > 64) val.resize(64);
            { std::lock_guard<std::mutex> lk(settings.rds_mutex);
              settings.rt_text = val; settings.rds_dirty = true; }
            std::cerr << "RDS RT=\"" << val << "\"\n";

        } else if (startsWith("PI=", 3)) {
            std::string val = msg.substr(3);
            unsigned int x = 0;
            if (val.size() >= 4 && std::sscanf(val.substr(0,4).c_str(), "%04X", &x) == 1) {
                uint16_t pi = static_cast<uint16_t>(x & 0xFFFF);
                { std::lock_guard<std::mutex> lk(settings.rds_mutex);
                  settings.rds_pi_code = pi; settings.rds_dirty = true; }
                std::cerr << "RDS PI=0x" << std::hex << pi << std::dec << "\n";
            } else {
                std::cerr << "RDS PI?: serve 4 cifre hex (es. PI=E123)\n";
            }

        } else if (startsWith("PTY=", 4)) {
            int p = std::atoi(msg.c_str() + 4);
            if (p >= 0 && p <= 31) {
                std::lock_guard<std::mutex> lk(settings.rds_mutex);
                settings.rds_pty = static_cast<uint8_t>(p);
                settings.rds_dirty = true;
            }
            std::cerr << "RDS PTY=" << p << "\n";

        } else if (startsWith("AF1=", 4)) {
            int f = std::atoi(msg.c_str() + 4);
            if (f == 0 || (f >= 876 && f <= 1079)) {
                { std::lock_guard<std::mutex> lk(settings.rds_mutex);
                  settings.rds_af1 = f; settings.rds_dirty = true; }
                std::cerr << "RDS AF1=" << f << " (" << (f?f/10.0:0) << " MHz)\n";
            } else {
                std::cerr << "RDS AF1?: 0 (off) o 876-1079 (87.6-107.9 MHz, es. 1015 = 101.5 MHz)\n";
            }

        } else if (startsWith("AF2=", 4)) {
            int f = std::atoi(msg.c_str() + 4);
            if (f == 0 || (f >= 876 && f <= 1079)) {
                { std::lock_guard<std::mutex> lk(settings.rds_mutex);
                  settings.rds_af2 = f; settings.rds_dirty = true; }
                std::cerr << "RDS AF2=" << f << " (" << (f?f/10.0:0) << " MHz)\n";
            } else {
                std::cerr << "RDS AF2?: 0 (off) o 876-1079 (87.6-107.9 MHz, es. 1015 = 101.5 MHz)\n";
            }

        } else if (startsWith("TA=", 3)) {
            int ta = (msg.size()>3 && (msg[3]=='1'||
                      msg.substr(3).find("true")==0||
                      msg.substr(3).find("on")==0)) ? 1 : 0;
            { std::lock_guard<std::mutex> lk(settings.rds_mutex);
              settings.rds_ta = ta; settings.rds_dirty = true; }
            std::cerr << "RDS TA=" << ta << "\n";

        } else if (startsWith("TP=", 3)) {
            int tp = (msg.size()>3 && (msg[3]=='1'||
                      msg.substr(3).find("true")==0||
                      msg.substr(3).find("on")==0)) ? 1 : 0;
            { std::lock_guard<std::mutex> lk(settings.rds_mutex);
              settings.rds_tp = tp; settings.rds_dirty = true; }
            std::cerr << "RDS TP=" << tp << "\n";

        } else if (startsWith("MS=", 3)) {
            int ms = (msg.size()>3 && (msg[3]=='1'||
                      msg.substr(3).find("true")==0||
                      msg.substr(3).find("on")==0||
                      msg.substr(3).find("music")==0)) ? 1 : 0;
            { std::lock_guard<std::mutex> lk(settings.rds_mutex);
              settings.rds_ms = ms; settings.rds_dirty = true; }
            std::cerr << "RDS MS=" << (ms?"Music":"Speech") << "\n";

        // ── Livelli MPX ───────────────────────────────────────────────────────
        } else if (startsWith("VOL_PILOT=", 10)) {
            float v = std::strtof(msg.c_str()+10, nullptr);
            if (v>=0.f&&v<=1.f) { settings.vol_pilot.store(v); std::cerr<<"VOL_PILOT="<<v<<"\n"; }

        } else if (startsWith("VOL_RDS=", 8)) {
            float v = std::strtof(msg.c_str()+8, nullptr);
            if (v>=0.f&&v<=1.f) { settings.vol_rds.store(v); std::cerr<<"VOL_RDS="<<v<<"\n"; }

        } else if (startsWith("VOL_MONO=", 9)) {
            float v = std::strtof(msg.c_str()+9, nullptr);
            if (v>=0.f&&v<=1.f) { settings.vol_mono.store(v); std::cerr<<"VOL_MONO="<<v<<"\n"; }

        } else if (startsWith("VOL_STEREO=", 11)) {
            float v = std::strtof(msg.c_str()+11, nullptr);
            if (v>=0.f&&v<=1.f) { settings.vol_stereo.store(v); std::cerr<<"VOL_STEREO="<<v<<"\n"; }

        // ── Enfasi ────────────────────────────────────────────────────────────
        } else if (startsWith("PREEMPH=", 8)) {
            float us = std::strtof(msg.c_str()+8, nullptr);
            if (us>=0.f&&us<=200.f) { settings.preemphasis_us.store(us); std::cerr<<"PREEMPH="<<us<<" us\n"; }

        } else if (startsWith("DEEMPH=", 7)) {
            float us = std::strtof(msg.c_str()+7, nullptr);
            if (us>=0.f&&us<=200.f) { settings.deemphasis_us.store(us); std::cerr<<"DEEMPH="<<us<<" us\n"; }

        // ── Gain / Mute / Debug ───────────────────────────────────────────────
        } else if (startsWith("GAIN=", 5)) {
            float db = std::strtof(msg.c_str()+5, nullptr);
            if (db>=-24.f&&db<=24.f) {
                settings.input_gain_db.store(db);
                settings.gain_l_db.store(db);
                settings.gain_r_db.store(db);
                std::cerr<<"GAIN="<<db<<" dB\n";
            }

        } else if (startsWith("GAIN_L=", 7)) {
            float db = std::strtof(msg.c_str()+7, nullptr);
            if (db>=-24.f&&db<=24.f) { settings.gain_l_db.store(db); std::cerr<<"GAIN_L="<<db<<"\n"; }

        } else if (startsWith("GAIN_R=", 7)) {
            float db = std::strtof(msg.c_str()+7, nullptr);
            if (db>=-24.f&&db<=24.f) { settings.gain_r_db.store(db); std::cerr<<"GAIN_R="<<db<<"\n"; }

        } else if (startsWith("GAINS_LINKED=", 13)) {
            bool on = msg.size()>13 && (msg[13]=='1'||msg.substr(13).find("true")==0||msg.substr(13).find("on")==0);
            settings.gains_linked.store(on);
            std::cerr<<"GAINS_LINKED="<<(on?"1":"0")<<"\n";

        } else if (startsWith("MONO_MODE=", 10)) {
            int m = std::atoi(msg.c_str()+10);
            if (m>=0&&m<=3) { settings.mono_mode.store(m); std::cerr<<"MONO_MODE="<<m<<"\n"; }

        } else if (startsWith("MUTE=", 5)) {
            bool on = msg.size()>5 && (msg[5]=='1'||
                      msg.substr(5).find("true")==0||
                      msg.substr(5).find("on")==0);
            settings.mute.store(on);
            std::cerr << "MUTE=" << (on?"1":"0") << "\n";

        } else if (startsWith("DEBUG=", 6)) {
            bool on = msg.size()>6 && (msg[6]=='1'||
                      msg.substr(6).find("true")==0||
                      msg.substr(6).find("on")==0);
            settings.debug.store(on);
            std::cerr << "DEBUG=" << (on?"1":"0") << "\n";

        } else if (startsWith("RDS_LOG_BIN=", 12)) {
            int on = (msg.size()>12 && (msg[12]=='1'||msg.substr(12).find("true")==0)) ? 1 : 0;
            set_rds_log_binary(on);
            std::cerr << "RDS_LOG_BIN=" << on << "\n";

        // ── PlutoSDR — TX_FREQ / TX_GAIN (BUG FIX: compare con lunghezza corretta 8) ──
        } else if (startsWith("TX_FREQ=", 8) || startsWith("tx_freq=", 8)) {
            float mhz = std::strtof(msg.c_str()+8, nullptr);
            if (mhz > 0.f && mhz <= 6000.f) {
                settings.tx_frequency_mhz.store(mhz);
                std::cerr << "TX_FREQ=" << mhz << " MHz\n";
            } else {
                std::cerr << "TX_FREQ?: valore in MHz (0.1–6000), es. TX_FREQ=100.5\n";
            }

        } else if (startsWith("TX_GAIN=", 8) || startsWith("tx_gain=", 8)) {
            float db = std::strtof(msg.c_str()+8, nullptr);
            if (db >= -90.f && db <= 0.f) {
                settings.tx_gain_db.store(db);
                std::cerr << "TX_GAIN=" << db << " dB\n";
            } else {
                std::cerr << "TX_GAIN?: valore in dB (-90…0), es. TX_GAIN=-17\n";
            }

        // ── Compressore — parametri ───────────────────────────────────────────
        } else if (startsWith("COMP_EN=", 8)) {
            bool on = msg.size()>8 && (msg[8]=='1'||
                      msg.substr(8).find("true")==0||
                      msg.substr(8).find("on")==0);
            settings.comp_enabled.store(on);
            std::cerr << "COMP_EN=" << (on?"1":"0") << "\n";

        } else if (startsWith("COMP_THR=", 9)) {
            float v = std::strtof(msg.c_str()+9, nullptr);
            if (v>=-60.f&&v<=0.f) { settings.comp_threshold_db.store(v); std::cerr<<"COMP_THR="<<v<<"\n"; }

        } else if (startsWith("COMP_RATIO=", 11)) {
            float v = std::strtof(msg.c_str()+11, nullptr);
            if (v>=1.f&&v<=100.f) { settings.comp_ratio.store(v); std::cerr<<"COMP_RATIO="<<v<<"\n"; }

        } else if (startsWith("COMP_KNEE=", 10)) {
            float v = std::strtof(msg.c_str()+10, nullptr);
            if (v>=0.f&&v<=24.f) { settings.comp_knee_db.store(v); std::cerr<<"COMP_KNEE="<<v<<"\n"; }

        } else if (startsWith("COMP_ATK=", 9)) {
            float v = std::strtof(msg.c_str()+9, nullptr);
            if (v>=0.1f&&v<=500.f) { settings.comp_attack_ms.store(v); std::cerr<<"COMP_ATK="<<v<<"ms\n"; }

        } else if (startsWith("COMP_REL=", 9)) {
            float v = std::strtof(msg.c_str()+9, nullptr);
            if (v>=10.f&&v<=5000.f) { settings.comp_release_ms.store(v); std::cerr<<"COMP_REL="<<v<<"ms\n"; }

        } else if (startsWith("COMP_MU=", 8)) {
            float v = std::strtof(msg.c_str()+8, nullptr);
            if (v>=-12.f&&v<=24.f) { settings.comp_makeup_db.store(v); std::cerr<<"COMP_MU="<<v<<"\n"; }

        } else if (startsWith("COMP_LIM=", 9)) {
            float v = std::strtof(msg.c_str()+9, nullptr);
            if (v>=0.f&&v<=1.f) { settings.comp_limit.store(v); std::cerr<<"COMP_LIM="<<v<<"\n"; }

        // ── GET / STATUS ──────────────────────────────────────────────────────
        } else if (msg == "GET" || msg == "STATUS") {
            std::ostringstream out;
            // Audio
            out << "GAIN="         << settings.input_gain_db.load()   << "\n";
            out << "GAIN_L="       << settings.gain_l_db.load()       << "\n";
            out << "GAIN_R="       << settings.gain_r_db.load()       << "\n";
            out << "GAINS_LINKED=" << (settings.gains_linked.load()?"1":"0") << "\n";
            out << "MONO_MODE="    << settings.mono_mode.load()       << "\n";
            out << "MUTE="       << (settings.mute.load()?"1":"0")  << "\n";
            out << "DEBUG="      << (settings.debug.load()?"1":"0") << "\n";
            // Livelli
            out << "VOL_PILOT="  << settings.vol_pilot.load()       << "\n";
            out << "VOL_RDS="    << settings.vol_rds.load()         << "\n";
            out << "VOL_MONO="   << settings.vol_mono.load()        << "\n";
            out << "VOL_STEREO=" << settings.vol_stereo.load()      << "\n";
            // Enfasi
            out << "PREEMPH="    << settings.preemphasis_us.load()  << "\n";
            out << "DEEMPH="     << settings.deemphasis_us.load()   << "\n";
            // Pluto
            out << "TX_FREQ="    << settings.tx_frequency_mhz.load()<< "\n";
            out << "TX_GAIN="    << settings.tx_gain_db.load()      << "\n";
            // Compressore — parametri
            out << "COMP_EN="    << (settings.comp_enabled.load()?"1":"0") << "\n";
            out << "COMP_THR="   << settings.comp_threshold_db.load()      << "\n";
            out << "COMP_RATIO=" << settings.comp_ratio.load()             << "\n";
            out << "COMP_KNEE="  << settings.comp_knee_db.load()           << "\n";
            out << "COMP_ATK="   << settings.comp_attack_ms.load()         << "\n";
            out << "COMP_REL="   << settings.comp_release_ms.load()        << "\n";
            out << "COMP_MU="    << settings.comp_makeup_db.load()         << "\n";
            out << "COMP_LIM="   << settings.comp_limit.load()             << "\n";
            // Compressore — metering
            out << "COMP_GR="    << settings.comp_gr_db.load()            << "\n";
            out << "COMP_IN="    << settings.comp_input_rms_db.load()     << "\n";
            out << "COMP_OUTPK=" << settings.comp_output_peak_db.load()   << "\n";
            // MPX metering
            out << "MPX_PEAK="    << settings.mpx_peak.load()              << "\n";
            out << "MPX_RMS="     << settings.mpx_rms.load()               << "\n";
            out << "MONO_PEAK="   << settings.mono_peak.load()             << "\n";
            out << "STEREO_PEAK=" << settings.stereo_peak.load()           << "\n";
            // RDS
            {
                std::lock_guard<std::mutex> lk(settings.rds_mutex);
                out << "PS="  << settings.ps_name  << "\n";
                out << "RT="  << settings.rt_text  << "\n";
                char pi_buf[8];
                std::snprintf(pi_buf, sizeof(pi_buf), "%04X",
                              settings.rds_pi_code & 0xFFFF);
                out << "PI="  << pi_buf            << "\n";
                out << "PTY=" << (int)settings.rds_pty << "\n";
                out << "TA="  << settings.rds_ta   << "\n";
                out << "TP="  << settings.rds_tp   << "\n";
                out << "MS="  << settings.rds_ms   << "\n";
                out << "AF1=" << settings.rds_af1  << "\n";
                out << "AF2=" << settings.rds_af2  << "\n";
            }
            std::string resp = out.str();
            sendto(fd, resp.data(), resp.size(), 0,
                   reinterpret_cast<sockaddr*>(&from), fromlen);
        }
        // comandi sconosciuti: silenzio (nessun log inutile)
    }
    close(fd);
#else
    (void)settings;
    while (g_control_running)
        std::this_thread::sleep_for(std::chrono::seconds(1));
#endif
}
