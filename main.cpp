#include "audio_pipeline.hpp"
#include "control_udp.hpp"
#include "config.hpp"
#include "globals.hpp"
#include "rds.h"
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <thread>

static void usage(const char* prog) {
    std::cerr
        << "Uso: " << prog << " [opzioni]\n"
        << "\n"
        << "  Ingresso:\n"
        << "    --stdin          PCM s16le 48 kHz stereo da stdin (default)\n"
        << "    --udp[=PORT]     PCM da UDP porta PORT (default 9121)\n"
        << "\n"
        << "  Uscita:\n"
        << "    --no-pluto       Stdout: MPX raw float32\n"
        << "    --fm-iq          Con --no-pluto: stdout IQ FM int16 interleaved\n"
        << "    --tx-freq=F      Frequenza LO Pluto in MHz (default 100.0)\n"
        << "    --tx-gain=G      Gain hardware Pluto dBFS (default -17.0)\n"
        << "\n"
        << "  Debug / test:\n"
        << "    --debug          Statistiche su stderr ogni ~1 s\n"
        << "    --test-stereo    5s solo L, 5s solo R, 5s L=1k R=2k (poi fine)\n"
        << "    --test-separation 10s L / 2s sil / 10s R / 2s sil × 2\n"
        << "\n"
        << "  Controllo UDP porta 9120:\n"
        << "    RDS:    PS=<8chr>  RT=<64chr>  PI=<4hex>  PTY=<0-31>  AF1=<0|875-1080>\n"
        << "            TA=0|1\n"
        << "    Livelli: VOL_PILOT=  VOL_RDS=  VOL_MONO=  VOL_STEREO=  (0.0-1.0)\n"
        << "    Audio:  GAIN=<dB> (-24..+24)  MUTE=0|1  DEBUG=0|1\n"
        << "    Enfasi: PREEMPH=<0|50|75>  DEEMPH=<0|50|75>  (µs)\n"
        << "    Pluto:  TX_FREQ=<MHz>  TX_GAIN=<dB> (-90..0)\n"
        << "    Comp:   COMP_EN=0|1  COMP_THR=<dBFS>  COMP_RATIO=<x>\n"
        << "            COMP_KNEE=<dB>  COMP_ATK=<ms>  COMP_REL=<ms>\n"
        << "            COMP_MU=<dB>  COMP_LIM=<0-1>\n"
        << "    Status: GET  (risponde con tutti i parametri + metering)\n"
        << "    RDS log: RDS_LOG_BIN=1  → /tmp/rds_stream.bin\n";
}

int main(int argc, char* argv[]) {
    Config config;
    config.input_mode     = InputMode::Stdin;
    config.udp_audio_port = 9121;
    config.use_pluto      = true;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--stdin") == 0) {
            config.input_mode = InputMode::Stdin;
        } else if (strncmp(argv[i], "--udp", 5) == 0) {
            config.input_mode = InputMode::Udp;
            if (argv[i][5] == '=') config.udp_audio_port = atoi(argv[i]+6);
        } else if (strcmp(argv[i], "--no-pluto") == 0) {
            config.use_pluto = false;
        } else if (strcmp(argv[i], "--fm-iq") == 0) {
            config.output_fm_iq = true;
        } else if (strncmp(argv[i], "--tx-freq=", 10) == 0) {
            double mhz = std::atof(argv[i]+10);
            if (mhz > 0.0) config.tx_frequency_hz = mhz * 1e6;
        } else if (strncmp(argv[i], "--tx-gain=", 10) == 0) {
            config.tx_gain_db = static_cast<float>(std::atof(argv[i]+10));
        } else if (strcmp(argv[i], "--debug") == 0) {
            config.debug = true;
        } else if (strcmp(argv[i], "--test-stereo") == 0) {
            config.test_stereo = true;
        } else if (strcmp(argv[i], "--test-separation") == 0) {
            config.test_separation = true;
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            usage(argv[0]); return 0;
        } else {
            std::cerr << "Opzione sconosciuta: " << argv[i] << "\n";
            usage(argv[0]); return 1;
        }
    }

    GlobalSettings settings;
    settings.tx_frequency_mhz.store(static_cast<float>(config.tx_frequency_hz / 1e6));
    settings.tx_gain_db.store(config.tx_gain_db);

    // Log su file
    const char* logpath = std::getenv("MODULATORE_LOG");
    if (!logpath || !logpath[0]) logpath = "/tmp/modulatore.log";
    FILE* logfile = std::fopen(logpath, "a");
    if (!logfile) logfile = std::fopen("modulatore.log", "a");
    if (logfile) {
        setvbuf(logfile, nullptr, _IONBF, 0);
        std::fprintf(logfile, "Modulatore avviato. UDP ctrl :9120. Stdout=MPX.\n");
    }

    setvbuf(stderr, nullptr, _IONBF, 0);
    std::cerr << "Modulatore avviato. UDP ctrl :9120.\n";
    set_rds_log_binary(0);

    std::thread control(control_udp_thread, std::ref(settings));
    std::thread audio(audio_processing_thread, std::ref(settings), std::cref(config));

    control.join();
    audio.join();

    if (rds_log_was_written())
        std::cerr << "RDS log: scritto /tmp/rds_stream.bin\n";
    if (logfile) std::fclose(logfile);
    return 0;
}
