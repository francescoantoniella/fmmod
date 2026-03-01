#pragma once

#include <cstdint>

enum class InputMode { Stdin, Udp, MpxStdin };

struct Config {
    InputMode input_mode = InputMode::Stdin;
    int       udp_audio_port = 9121;   // porta per PCM 48k stereo (controllo RDS su 9120)

    // Uscita
    bool use_pluto    = false;   // false = scrive su stdout (MPX raw o IQ FM se output_fm_iq)
    bool output_fm_iq = false;   // con --no-pluto: stdout = IQ FM interleaved (I,Q int16) per analisi
    bool debug        = false;   // stampa su stderr statistiche ingressi/uscita ogni ~1 s

    bool test_stereo      = false; // genera L/R di test per verificare separazione stereo
    bool test_separation  = false; // test definitivo separazione

    // Parametri PlutoSDR (valori usati solo se use_pluto == true)
    // Frequenza LO TX in Hz (default 100.0 MHz)
    double tx_frequency_hz = 100e6;
    // Hardware gain in dBFS (negativo = attenuazione). Default -17 dB.
    float  tx_gain_db      = -17.0f;
};
