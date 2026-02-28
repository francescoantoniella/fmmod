#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <time.h>
#include <stdlib.h>
#include <math.h>

#include "rds_strings.h"

#ifndef RDS_STREAM_PATH_MAX
#define RDS_STREAM_PATH_MAX 256
#endif

#define RT_LENGTH 64
#define PS_LENGTH 8
#define GROUP_LENGTH 4

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define TWO_PI_F         6.283185307179586f
#define MPX_SIN_LUT_SIZE 48
#define RDS_STRIDE       3

struct {
    uint16_t pi;
    int      ta;
    int      tp;     /* Traffic Programme: 0=no, 1=yes */
    uint8_t  pty;
    uint8_t  af1;    /* AF1 code: (freq_MHz×10 - 875), 0=disabilitata */
    uint8_t  af2;    /* AF2 code: (freq_MHz×10 - 875), 0=disabilitata */
    uint8_t  ms;     /* Music/Speech: 1=Music (default), 0=Speech */
    char     ps[PS_LENGTH];
    char     rt[RT_LENGTH];
} rds_params = { .ms = 1 };

#define POLY      0x1B9
#define POLY_DEG  10
#define MSB_BIT   0x8000
#define BLOCK_SIZE 16

#define BITS_PER_GROUP (GROUP_LENGTH * (BLOCK_SIZE + POLY_DEG))
#define SAMPLES_PER_BIT 768

#define RDS_LOG_GROUPS 114
#define RDS_LOG_BITS   (RDS_LOG_GROUPS * BITS_PER_GROUP)
#define RDS_LOG_BYTES  (RDS_LOG_BITS / 8)

static int           rds_log_active    = 0;
static int           rds_log_bit_count = 0;
static int           rds_log_written   = 0;
static unsigned char rds_log_buf[RDS_LOG_BYTES];

/* Offset words EN 50067: A, B, C, D */
static const uint16_t offset_words[4] = { 0x0FC, 0x198, 0x168, 0x1B4 };

/* ---------- CRC ---------- */
static uint16_t rds_crc(uint16_t block) {
    uint16_t c = 0;
    for (int j = 0; j < BLOCK_SIZE; j++) {
        int bit = (block & MSB_BIT) != 0;
        block <<= 1;
        int msb = (c >> (POLY_DEG - 1)) & 1;
        c <<= 1;
        if ((msb ^ bit) != 0) c ^= POLY;
    }
    return c;
}

/* ---------- Helper: blocco B per gruppi 0A (PS e AF) ----------
 *
 * Struttura EN 50067 blocco B gruppo 0A:
 *   bit 15-12  group type = 0000
 *   bit 11     version    = 0 (A)
 *   bit 10     TP
 *   bit  9-5   PTY  (5 bit)
 *   bit  4     TA
 *   bit  3     M/S  (1 = Music)
 *   bit  2     DI   (Decoder Identification)
 *   bit  1-0   segment address (0-3)
 */
static uint16_t make_block_b_0a(int segment) {
    /* DI nibble: seg3 = bit 0 (Stereo), seg2 = bit 1 (Art.Head), seg1 = bit 2 (Compress), seg0 = bit 3 (Dyn.PTY) */
    uint8_t di = (segment == 3) ? 1u : 0u;  /* DI0 = Stereo */
    return ((uint16_t)(rds_params.tp ? 1u : 0u) << 10)
         | ((uint16_t)(rds_params.pty & 31u) << 5)
         | ((uint16_t)(rds_params.ta ? 1u : 0u) << 4)
         | ((uint16_t)(rds_params.ms ? 1u : 0u) << 3)
         | ((uint16_t)di << 2)
         | (uint16_t)(segment & 3);
}

/* ---------- Helper: blocco B per gruppi 2A (RT) ----------
 *
 *   bit 15-12  group type = 0010
 *   bit 11     version    = 0 (A)
 *   bit 10     TP
 *   bit  9-5   PTY
 *   bit  4     TA (AB flag per RT: alterna 0/1 ad ogni cambio testo)
 *   bit  3-0   segment address (0-15)
 */
static uint16_t make_block_b_2a(int segment, int ab_flag) {
    return (2u << 12)
         | ((uint16_t)(rds_params.pty & 31u) << 5)
         | ((uint16_t)(rds_params.ta ? 1u : 0u) << 4)
         | ((uint16_t)(ab_flag & 1) << 4)   /* RT A/B flag sovrascrive TA nel tipo 2A */
         | (uint16_t)(segment & 15);
}

/* ---------- Gruppo CT (Clock Time, tipo 4A) ---------- */
static int get_rds_ct_group(uint16_t *blocks) {
    static int latest_minutes = -1;
    time_t now = time(NULL);
    struct tm *utc = gmtime(&now);

    if (utc->tm_min != latest_minutes) {
        latest_minutes = utc->tm_min;

        int l = (utc->tm_mon <= 1) ? 1 : 0;
        int mjd = 14956 + utc->tm_mday
                + (int)((utc->tm_year - l) * 365.25)
                + (int)((utc->tm_mon + 2 + l * 12) * 30.6001);

        /*
         * Blocco B gruppo 4A (CT):
         *   bit 15-12 = 0100  (group 4)
         *   bit 11    = 0     (version A)
         *   bit 10    = TP    (trasportiamo TP=0, nessun traffico)
         *   bit  9-5  = PTY   (0 per CT per spec)
         *   bit  4-1  = MJD bit 17-14
         *   bit  0    = MJD bit 13  -- ma qui usiamo la formula originale
         *
         * Il campo PTY nel CT è sempre 0 per spec (non è una info programme).
         * TP nel CT: usiamo 0 (coerente con gli altri gruppi).
         */
        blocks[1] = (uint16_t)(0x4000u
                    | ((uint16_t)(rds_params.tp ? 1u : 0u) << 10)
                    | (uint16_t)((mjd >> 15) & 0x03u));
        blocks[2] = (uint16_t)(((mjd & 0x7FFF) << 1) | ((utc->tm_hour >> 4) & 1));
        blocks[3] = (uint16_t)(((utc->tm_hour & 0xF) << 12) | (utc->tm_min << 6));

        utc = localtime(&now);
        int offset = (int)(utc->tm_gmtoff / (30 * 60));
        blocks[3] |= (uint16_t)(abs(offset) & 0x1F);
        if (offset < 0) blocks[3] |= 0x20u;

        return 1;
    }
    return 0;
}

/* ---------- Generazione gruppo RDS ---------- */
static void get_rds_group(int *buffer) {
    static int state    = 0;
    static int ps_state = 0;
    static int rt_state = 0;
    static int rt_ab    = 0;   /* RT A/B flag: cambia ogni volta che il testo RT cambia */

    uint16_t blocks[GROUP_LENGTH] = { rds_params.pi, 0, 0, 0 };

    if (!get_rds_ct_group(blocks)) {

        if (state < 8) {
            /* Gruppi 0A - PS name: 4 segmenti × 2 ripetizioni = state 0..7 */
            ps_state = (state >> 1) & 3;
            blocks[1] = make_block_b_0a(ps_state);
            blocks[2] = 0xCDCD;   /* nessuna AF in questo gruppo */
            blocks[3] = ((uint16_t)(uint8_t)rds_params.ps[ps_state * 2]     << 8)
                      |  (uint16_t)(uint8_t)rds_params.ps[ps_state * 2 + 1];

        } else if (state == 8) {
            /* Gruppo 2A - RadioText: un segmento per volta (16 segmenti × 4 char = 64 char) */
            blocks[1] = make_block_b_2a(rt_state, rt_ab);
            blocks[2] = ((uint16_t)(uint8_t)rds_params.rt[rt_state * 4 + 0] << 8)
                      |  (uint16_t)(uint8_t)rds_params.rt[rt_state * 4 + 1];
            blocks[3] = ((uint16_t)(uint8_t)rds_params.rt[rt_state * 4 + 2] << 8)
                      |  (uint16_t)(uint8_t)rds_params.rt[rt_state * 4 + 3];
            rt_state = (rt_state + 1) % 16;

        } else {
            /*
             * state 9: gruppo 0A con AF (frequenza alternativa).
             * Usiamo segment=0 e make_block_b_0a per avere PTY/TA corretti.
             * blocks[3] = 0 significa nessun char PS per questo gruppo (valido per spec:
             * il decoder aggiorna solo i segmenti che riceve con CRC ok).
             */
            blocks[1] = make_block_b_0a(0);
            /*
             * AF Method A EN 50067 §3.2.1.6.1 — block C = [AF1 | AF2]
             *
             * Logica:
             *  - AF1 e AF2 entrambe valide → [AF1 | AF2]
             *  - Solo AF1 → [AF1 | AF1] (duplicato; evita il filler 0xCD=205 che
             *    molti decoder decodificano erroneamente come freq AM/LF ~1953 kHz)
             *  - Nessuna AF → 0xCDCD (filler standard)
             */
            if (rds_params.af1 && rds_params.af2) {
                blocks[2] = ((uint16_t)rds_params.af1 << 8) | (uint16_t)rds_params.af2;
            } else if (rds_params.af1) {
                blocks[2] = ((uint16_t)rds_params.af1 << 8) | (uint16_t)rds_params.af1;
            } else {
                blocks[2] = 0xCDCDu;
            }
            blocks[3] = ((uint16_t)(uint8_t)rds_params.ps[0] << 8)
                      |  (uint16_t)(uint8_t)rds_params.ps[1];
        }

        state = (state + 1) % 10;
    }

    /* Serializza i 4 blocchi: 16 bit dati + 10 bit CRC+offset */
    for (int i = 0; i < GROUP_LENGTH; i++) {
        uint16_t block = blocks[i];
        uint16_t check = rds_crc(block) ^ offset_words[i];
        for (int j = 0; j < BLOCK_SIZE; j++) {
            *buffer++ = (block & MSB_BIT) != 0;
            block <<= 1;
        }
        for (int j = 0; j < POLY_DEG; j++) {
            *buffer++ = (check & (1 << (POLY_DEG - 1))) != 0;
            check <<= 1;
        }
    }
}

/* ---------- LUT ---------- */
static float mpx_sin_lut[MPX_SIN_LUT_SIZE];
static float rds_pulse_lut[SAMPLES_PER_BIT];
static int   lut_initialized = 0;

void init_rds_luts(void) {
    for (int i = 0; i < MPX_SIN_LUT_SIZE; i++)
        mpx_sin_lut[i] = sinf(TWO_PI_F * (float)i / (float)MPX_SIN_LUT_SIZE);

    for (int i = 0; i < SAMPLES_PER_BIT; i++) {
        float s = sinf((float)M_PI * (float)i / (float)SAMPLES_PER_BIT);
        rds_pulse_lut[i] = s * s;
    }
    lut_initialized = 1;
}

const float *get_mpx_sin_lut(void) {
    if (!lut_initialized) init_rds_luts();
    return mpx_sin_lut;
}

/* ---------- Generazione campioni RDS ---------- */
void get_rds_samples(float *buffer, int count) {
    if (!lut_initialized) init_rds_luts();

    static int bit_buffer[BITS_PER_GROUP];
    static int bit_pos      = BITS_PER_GROUP;  /* forza get_rds_group al primo campione */
    static int diff_bit     = 0;
    static int sample_count = 0;
    static int rds_phase    = 0;

    for (int i = 0; i < count; i++) {

        if (sample_count >= SAMPLES_PER_BIT) {
            if (bit_pos >= BITS_PER_GROUP) {
                get_rds_group(bit_buffer);
                bit_pos = 0;
            }

            /* Log binario */
            if (rds_log_active && rds_log_bit_count < RDS_LOG_BITS) {
                static int log_first_printed = 0;
                if (!log_first_printed) {
                    log_first_printed = 1;
                    fprintf(stderr, "[rds.c] RDS log branch entered, capturing bits (rds_log_active=1)\n");
                    fflush(stderr);
                }
                int bit     = bit_buffer[bit_pos] & 1;
                int byte_ix = rds_log_bit_count / 8;
                int shift   = 7 - (rds_log_bit_count % 8);
                rds_log_buf[byte_ix] |= (unsigned char)(bit << shift);
                if (++rds_log_bit_count == RDS_LOG_BITS) {
                    fprintf(stderr, "[rds.c] RDS log: reached %d bits, writing file\n", RDS_LOG_BITS);
                    fflush(stderr);
                    char path[RDS_STREAM_PATH_MAX];
                    const char *env = getenv("RDS_STREAM_PATH");
                    if (env && env[0]) {
                        size_t len = strlen(env);
                        if (len >= RDS_STREAM_PATH_MAX) len = RDS_STREAM_PATH_MAX - 1;
                        memcpy(path, env, len);
                        path[len] = '\0';
                    } else {
                        memcpy(path, "/tmp/rds_stream.bin", sizeof("/tmp/rds_stream.bin"));
                    }
                    FILE *f = fopen(path, "wb");
                    const char *written_path = path;
                    if (!f) {
                        f = fopen("rds_stream.bin", "wb");
                        written_path = "rds_stream.bin";
                    }
                    if (f) {
                        size_t n = fwrite(rds_log_buf, 1, RDS_LOG_BYTES, f);
                        fclose(f);
                        if (n == (size_t)RDS_LOG_BYTES) {
                            rds_log_written = 1;
                            fprintf(stderr, "RDS log binary: written %s (%d bytes)\n", written_path, RDS_LOG_BYTES);
                        }
                    } else {
                        fprintf(stderr, "RDS log binary: failed to open %s and rds_stream.bin\n", path);
                    }
                    fflush(stderr);
                    rds_log_active = 0;
                }
            }

            /* Codifica differenziale */
            diff_bit     = bit_buffer[bit_pos] ^ diff_bit;
            bit_pos++;
            sample_count = 0;
        }

        /* Biphase Mark: prima metà in fase, seconda in controfase */
        float m_logic = (sample_count < (SAMPLES_PER_BIT / 2)) ? 1.0f : -1.0f;
        float bit_val = (diff_bit == 1) ? m_logic : -m_logic;

        buffer[i] = rds_pulse_lut[sample_count] * bit_val * mpx_sin_lut[rds_phase];

        sample_count++;
        rds_phase = (rds_phase + RDS_STRIDE) % MPX_SIN_LUT_SIZE;
    }
}

/* ---------- Interfaccia ---------- */
void set_rds_pi(uint16_t pi_code)    { rds_params.pi  = pi_code; }
void set_rds_rt(char *rt)            { fill_rds_string(rds_params.rt, rt, 64); }
void set_rds_ps(char *ps)            { fill_rds_string(rds_params.ps, ps, 8); }
void set_rds_ta(int ta)              { rds_params.ta  = ta ? 1 : 0; }
void set_rds_tp(int tp)              { rds_params.tp  = tp ? 1 : 0; }
void set_rds_pty(uint8_t pty)        { rds_params.pty = (pty <= 31u) ? pty : 0u; }
void set_rds_ms(int ms)              { rds_params.ms  = ms ? 1u : 0u; }

static uint8_t _af_encode(int freq_01mhz) {
    /* Codici validi EN 50067: 1–204 → 87,6–107,9 MHz (passo 100 kHz)
     * formula: codice = freq_01mhz − 875  (es. 880 → 5 → 88,0 MHz)
     * Codice 0 riservato, codice 205 = filler → entrambi esclusi.       */
    if (freq_01mhz < 876 || freq_01mhz > 1079) return 0;
    return (uint8_t)(freq_01mhz - 875);
}

void set_rds_af1(int freq_01mhz) { rds_params.af1 = _af_encode(freq_01mhz); }
void set_rds_af2(int freq_01mhz) { rds_params.af2 = _af_encode(freq_01mhz); }

void set_rds_log_binary(int enable) {
    if (enable) {
        rds_log_written   = 0;
        rds_log_active    = 1;
        rds_log_bit_count = 0;
        memset(rds_log_buf, 0, sizeof(rds_log_buf));
        fprintf(stderr, "[rds.c] set_rds_log_binary(1): started, rds_log_active=1 (~10 s per 114 gruppi)\n");
        fflush(stderr);
    } else {
        rds_log_active = 0;
    }
}

int rds_log_was_written(void) { return rds_log_written; }
