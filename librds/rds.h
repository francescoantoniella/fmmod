/*
    PiFmRds - FM/RDS transmitter for the Raspberry Pi
    Copyright (C) 2014 Christophe Jacquet, F8FTK
    
    See https://github.com/ChristopheJacquet/PiFmRds

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#ifndef RDS_H
#define RDS_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Inizializza le LUT (chiamare prima di get_mpx_sin_lut / get_rds_samples). */
extern void init_rds_luts(void);
/** LUT sin condivisa 48 campioni: pilot stride 1, stereo 2, RDS 3. */
extern const float* get_mpx_sin_lut(void);

extern void get_rds_samples(float *buffer, int count);
extern void set_rds_pi(uint16_t pi_code);
extern void set_rds_rt(char *rt);
extern void set_rds_ps(char *ps);
extern void set_rds_ta(int ta);
/** TP = Traffic Programme: 1 = la stazione trasmette info sul traffico, 0 = no */
extern void set_rds_tp(int tp);
/** PTY = Programme Type 0-31 (es. 0=None, 1=News, 2=Pop Music, ...) */
extern void set_rds_pty(uint8_t pty);
/** MS = Music/Speech: 1 = Music (default), 0 = Speech */
extern void set_rds_ms(int ms);
/** AF1/AF2: freq_01mhz = frequenza in 0.1 MHz (876-1079 = 87.6-107.9 MHz), 0 = disabilitata.
 *  Codice RDS = freq_01mhz − 875 (es. 1015 → codice 140 → 101.5 MHz) */
extern void set_rds_af1(int freq_01mhz);
extern void set_rds_af2(int freq_01mhz);
/** Abilita log stream binario: 1 = avvia cattura di 114 gruppi (11856 bit) in /tmp/rds_stream.bin */
extern void set_rds_log_binary(int enable);
/** Ritorna 1 se in questa sessione e' stato scritto /tmp/rds_stream.bin. */
extern int rds_log_was_written(void);

#ifdef __cplusplus
}
#endif

#endif /* RDS_H */