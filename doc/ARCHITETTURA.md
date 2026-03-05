# FM Processor 2.0 — Documentazione Tecnica

## Indice

1. [Architettura generale](#1-architettura-generale)
2. [Flusso dati](#2-flusso-dati)
3. [Modulatore C++](#3-modulatore-c)
4. [Server web Python](#4-server-web-python)
5. [Interfaccia web (index.html)](#5-interfaccia-web-indexhtml)
6. [Persistenza EEPROM (storage.py)](#6-persistenza-eeprom-storagepy)
7. [RDS Manager](#7-rds-manager)
8. [Hardware](#8-hardware)
9. [API HTTP](#9-api-http)
10. [Comandi UDP modulatore](#10-comandi-udp-modulatore)
11. [Installazione e deploy](#11-installazione-e-deploy)
12. [Flowgraphs GNURadio](#12-flowgraphs-gnuradio-flowgraphs)
13. [Font offline](#13-font-offline)

---

## 1. Architettura generale

```
┌──────────────────────────────────────────────────────────────────┐
│                         RASPBERRY PI / CM4                        │
│                                                                   │
│  ┌──────────────┐  PCM s16le 48kHz  ┌──────────────────────────┐ │
│  │  ffmpeg /    │ ─────────────────► │  modulatore (C++)         │ │
│  │  sorgente    │   stdin o UDP:9121 │  ctrl UDP :9120           │ │
│  └──────────────┘                   └────────────┬─────────────┘ │
│                                                  │ IQ int16       │
│  ┌───────────────────────────────────┐           │ (PlutoSDR)     │
│  │  server.py  (Bottle HTTP :8080)   │◄──────────┘ UDP GET/SET   │
│  │                                   │                            │
│  │  /api/status      polling stato   │                            │
│  │  /api/cmd         invio comandi   │                            │
│  │  /api/history     storico CSV     │                            │
│  │  /api/eeprom/*    load/save       │                            │
│  │  /api/softstart   ramp DAC        │                            │
│  │  /api/pid         target/params   │                            │
│  │  /api/rds/config  RDS avanzato    │                            │
│  │  /api/rds/status  stato RDS       │                            │
│  └────────────┬──────────────────────┘                            │
│               │ I2C /dev/i2c-1                                    │
│               ├──► ADS1115 (0x48)  ADC 4ch 16-bit                │
│               │      CH0: temperatura (LM35 / NTC)               │
│               │      CH1: V_FWD  rilevatore log potenza diretta   │
│               │      CH2: V_REF  rilevatore log potenza riflessa  │
│               │      CH3: libero                                   │
│               ├──► MCP4725 (0x60)  DAC 12-bit                    │
│               │      OUT → attenuatore variabile / ALC finale RF  │
│               └──► AT24C512 (0x50) EEPROM 512kbit                │
│                      6 gruppi in pagine da 128 B                  │
│                                                                   │
│  Browser ──HTTP──► :8080/  (index.html servita da Bottle)        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Flusso dati

```
Sorgente audio (ffmpeg / sox / icecast relay)
        │ PCM s16le 48 kHz stereo
        │ stdin  oppure  UDP:9121
        ▼
modulatore (C++)
  ├─ AudioLimiter       hard limiter pre-compressore
  ├─ De-enfasi          (0 / 50 / 75 µs) — opzionale
  ├─ SingleBandCompressor
  ├─ Pre-enfasi         (0 / 50 / 75 µs)
  ├─ PolyphaseUpsampler 48 kHz → 912 kHz
  ├─ MpxModulator       mono + pilot 19 kHz + stereo 38 kHz + RDS 57 kHz
  ├─ FmModulator        FM → IQ int16
  └─ PlutoSDR (libiio)  o stdout (MPX float32 / IQ int16)
        │ UDP:9120 — bidirezionale
        ▼
server.py
  ├─ poll_modulatore()      ogni 200 ms: GET → aggiorna state
  ├─ sensor_thread()        ogni 1 s:   ADC → PID → DAC → allarmi → CSV
  ├─ rds_manager_thread()   ogni 500 ms: ciclo PS, ciclo RT, fetch Icecast
  └─ Bottle HTTP :8080
        │ JSON / HTTP
        ▼
Browser (index.html)
  ├─ pollStatus()    ogni 150 ms: /api/status
  └─ pollRdsStatus() ogni 2 s:   /api/rds/status
```

---

## 3. Modulatore C++

### File sorgente (`src/`)

I sorgenti C++ risiedono in `src/`. La libreria RDS è in `librds/` (separata).

| File | Ruolo |
|------|-------|
| `src/main.cpp` | Entry point, parsing argomenti, avvio thread |
| `src/audio_pipeline.cpp/.hpp` | Thread audio: lettura → DSP → upsampling → MPX → output |
| `src/audio_input.cpp/.hpp` | Lettura PCM da stdin o UDP:9121 |
| `src/compressor.hpp` | SingleBandCompressor (soglia, ratio, knee, att/rel, makeup, limiter) |
| `src/mpx_modulator.hpp` | Generazione segnale MPX a 912 kHz con LUT sin; bypass condizionale per vol=0 su pilot/stereo/RDS |
| `src/fm_modulator.hpp` | Modulazione FM → IQ int16 |
| `src/upsampler.hpp` | Polyphase upsampler 48 k→ 912 k (fattore 19) |
| `src/pluto_output.cpp/.hpp` | Uscita verso PlutoSDR (libiio) o stdout |
| `src/control_udp.cpp/.hpp` | Thread controllo UDP:9120 (GET/SET parametri) |
| `src/globals.hpp` | `GlobalSettings`: tutte le variabili `std::atomic<>` condivise |
| `src/rds_manager.hpp` | Wrapper attorno a librds per PS/RT/PI/PTY/TA/AF1 |
| `src/iio_compat.hpp` | Compatibilità libiio v0/v1 |
| `src/config.hpp` | Costanti di configurazione build-time |
| `src/constants.hpp` | Costanti DSP condivise |
| `librds/` | Libreria RDS (rds.c, rds_strings.c) |

### Argomenti da riga di comando

```
--stdin              PCM da stdin (default)
--udp[=PORT]         PCM da UDP (default porta 9121)
--no-pluto           Stdout: MPX raw float32
--fm-iq              Con --no-pluto: IQ int16 (come Pluto)
--tx-freq=F          Frequenza LO in MHz (default 100.0)
--tx-gain=G          Gain hardware dBFS (default -17.0)
--debug              Statistiche su stderr ogni ~1 s
--test-stereo        Sequenza test canali L/R/1k+2k
--test-separation    Separazione L/R per misura isolamento
```

### Pipeline audio (dettaglio)

```
Chunk 480 campioni @ 48 kHz (10 ms)
  1. apply_input_processing()   gain L/R (linked o separati), mute, mono_mode routing
  2. AudioLimiter.process()     hard limiter (threshold 0.99, release 100 ms)
  3. apply_deemphasis()         IIR 1° ordine, τ = 50 µs o 75 µs
  4. SingleBandCompressor       feedforward RMS, soft-knee, makeup gain
  5. apply_pre_emphasis()       IIR 1° ordine, τ = 50 µs o 75 µs
  6. PolyphaseUpsampler         48k → 912k (CHUNK_912K = 9120 camp.)
  7. MpxModulator.process()     segnale MPX completo (tanh soft-limit)
  8. PlutoOutput.write()        IQ int16 → PlutoSDR o stdout
```

#### Gain ingresso e modalità mono

`apply_input_processing()` sostituisce `apply_input_gain()` e gestisce:

| Parametro | Comando UDP | Default | Descrizione |
|-----------|-------------|---------|-------------|
| `input_gain_db` | `GAIN=<dB>` | 0 | Gain master (–24 … +24 dB); applicato a L e R quando linked |
| `gain_l_db` | `GAIN_L=<dB>` | 0 | Gain canale L indipendente |
| `gain_r_db` | `GAIN_R=<dB>` | 0 | Gain canale R indipendente |
| `gains_linked` | `GAINS_LINKED=0\|1` | 1 | Se 1, `GAIN=` sovrascrive entrambi i canali |
| `mono_mode` | `MONO_MODE=0..3` | 0 | 0=stereo, 1=mono da L, 2=mono da R, 3=mix (L+R)/2 |

`GAIN=<dB>` scrive contemporaneamente `input_gain_db`, `gain_l_db` e `gain_r_db`.

---

## 4. Server web Python

### File

| File | Ruolo |
|------|-------|
| `control/server.py` | Backend Bottle: API REST, thread, stato globale |
| `control/storage.py` | Persistenza multi-gruppo su EEPROM AT24C512 |
| `control/index.html` | UI single-page servita da Bottle |
| `control/app.js` | Logica UI: polling, slider, RDS, temi, selettore sorgente |
| `control/style.css` | Stile principale (variabili CSS, layout, componenti) |
| `control/theme-dark.css` | Tema scuro (default) |
| `control/theme-light.css` | Tema chiaro |
| `control/theme-neon.css` | Tema neon verde |
| `control/fonts.css` | Dichiarazioni `@font-face` per font locali |
| `control/fonts/` | Font woff2 locali (offline, nessuna richiesta CDN) |

### Driver periferiche I2C/SPI (`drivers/`)

| Directory | Chip | Bus | Classe | Uso |
|-----------|------|-----|--------|-----|
| `drivers/ADS7066/` | ADS7066 | SPI | `ADS7066` | ADC 8 canali 16-bit |
| `drivers/DAC121/` | DAC121 | I2C | `DAC121` | DAC 12-bit, uscita tensione |
| `drivers/EEPROM/` | M24512 / AT24C512 | I2C | `N24512` | EEPROM 512kbit, R/W byte/pagina/blocco |
| `drivers/PCA9534/` | TCA9534 / PCA9534 | I2C | `TCA9534` | GPIO expander 8-bit |
| `drivers/TLV320ADC6140/` | TLV320ADC6140 | I2C + I2S | `TLV320ADC` | Audio ADC 4ch, master I2S, 48–384 kHz |

### Thread in esecuzione

| Thread | Funzione | Intervallo |
|--------|----------|------------|
| `poll_modulatore` | GET UDP → aggiorna `state.params` e `state.metering` | 200 ms |
| `sensor_thread` | ADC → calcoli → PID → DAC → allarmi → CSV | 1 s |
| `rds_manager_thread` | Ciclo PS, ciclo RT, fetch Icecast | 500 ms tick |
| Bottle (main) | HTTP :8080 | event-driven |

### Stato globale (`State`)

```python
state.params       # mirror GlobalSettings C++: gain, gain_l, gain_r, gains_linked,
                   #   mono_mode, vol_*, tx_freq, tx_gain, pi, ps, rt, ...
state.metering     # comp_gr_db, comp_input_db, comp_output_peak,
                   #   mpx_peak, mpx_rms, mono_peak, stereo_peak
state.sensors      # temp_c, fwd_w, ref_w, swr, dac_value, pid_output
state.alarms       # temp_high, swr_high, fwd_low, fwd_high
state.history      # deque 600 punti (10 min @ 1 Hz): ts, temp, fwd, ref, swr, comp_gr
state.rds_cfg      # configurazione RDS manager
state.rds_state    # stato runtime RDS (titolo, slot, half PS)
state.pid          # PIDController (kp, ki, kd, integrale)
state.power_target_w
state.softstart_active
state.serial_number
```

### Auto-apply parametri

All'avvio `server.py` esegue `auto_apply_settings()` in un thread separato: aspetta fino a 60 s che il modulatore risponda a `GET`, poi chiama `apply_params_to_modulator()` per inviare tutti i parametri salvati in EEPROM.

`apply_params_to_modulator()` carica i gruppi `tx_audio`, `compressor`, `rds_cfg`, `rds_text`, `power_pid` dallo storage e invia i comandi UDP corrispondenti. È richiamabile in qualsiasi momento.

`wait_and_apply(delay)` viene lanciata anche dopo `chain.start()` e `chain.restart()`: attende che il modulatore torni raggiungibile, poi ri-applica i parametri.

All'avvio, `server.py` avvia automaticamente la catena webradio (`chain.start()`). Il risultato viene loggato ma un fallimento non blocca il server.

---

## 5. Interfaccia web (index.html)

Applicazione single-page. **Nessuna dipendenza esterna a runtime** (font serviti localmente da `control/fonts/`).

### Struttura file UI

Il codice originariamente inline è stato estratto in file separati serviti da Bottle:

| File | Contenuto |
|------|-----------|
| `index.html` | Solo struttura HTML e `<link>`/`<script>` |
| `app.js` | Tutta la logica JS (polling, slider, RDS, temi, sorgente) |
| `style.css` | CSS principale con variabili per tutti i temi |
| `theme-dark.css` | Override variabili tema scuro |
| `theme-light.css` | Override variabili tema chiaro |
| `theme-neon.css` | Override variabili tema neon verde |
| `fonts.css` | `@font-face` declarations |

Il tema attivo viene salvato in `localStorage` (`fm_theme`) e ripristinato al ricaricamento. Il selettore cicla tra i temi disponibili tramite `cycleTheme()`.

### Selettore sorgente audio

Il tab TX include un pannello sorgente con tre pulsanti (WEBRADIO / AUDIO IN / MPX). La sorgente attiva viene sincronizzata dallo stato (`s.audio_source`) e la configurazione per sorgente (`s.audio_source_cfg`) popola i campi URL/device.

### Tab

| Tab | Contenuto |
|-----|-----------|
| TX | Pannello Ingresso (gain L/R, link, mono mode), Output MPX (peak, VU per componente), Impostazioni (freq/gain TX, enfasi, MUTE), catena webradio |
| RDS | Pannello RDS avanzato: PI, PTY, PS lungo, modalità RT, Icecast, AF1/AF2, TA/TP/MS |
| Compressore | Curva di trasferimento, parametri, metering GR/in/out |
| Potenza / RF | Temperatura, FWD/REF/SWR, DAC, PID, Soft-start, allarmi |
| Storico | Grafici canvas: temp, FWD, SWR, GR compressore (ultimi 10 min) |
| Sistema | Serial number, backend storage, uptime, Save/Load EEPROM |

### Pannello Ingresso (tab TX)

Slider di gain separabili per canale L e R; bottone link (🔗) per tenerli agganciati.
Quando linked, trascinare il master L equivale a `GAIN=<dB>` (aggiorna entrambi i canali).
Quando unlinked, invia `GAIN_L=` o `GAIN_R=` separatamente.

Pulsanti modalità mono (invia `MONO_MODE=`):

| Bottone | Valore | Effetto |
|---------|--------|---------|
| STEREO | 0 | Stereo normale |
| MONO | 3 | Mix (L+R)/2 su entrambi i canali |
| MONO L | 1 | Canale L duplicato su R |
| MONO R | 2 | Canale R duplicato su L |

La pre-enfasi è selezionata tramite tre pulsanti: **LINEAR** (0 µs), **50 µs**, **75 µs** (invia `PREEMPH=`).

I pulsanti **PILOT**, **STEREO**, **RDS** attivano/disattivano le rispettive sottoportanti.
Il valore del cursore viene salvato e ripristinato al re-enable. Inviano `VOL_PILOT=0`, `VOL_STEREO=0`, `VOL_RDS=0` al modulatore tramite UDP.

### Pannello Output FM (tab TX)

Il pannello "Output" mostra la deviazione FM peak/rms in kHz (scala: valore normalizzato × 75 kHz).
Visualizza separatamente la deviazione nominale di ogni componente MPX:

| Componente | Frequenza sottoportante | Calcolo deviazione nominale |
|-----------|------------------------|----------------------------|
| Mono (L+R) | — | `vol_mono × 75 kHz` |
| Pilot | 19 kHz | `vol_pilot × 75 kHz` |
| Stereo (L-R) | 38 kHz DSB-SC | `vol_stereo × 75 kHz` |
| RDS | 57 kHz | `vol_rds × 75 kHz` |

Il metering include anche `mono_peak` e `stereo_peak` (picco normalizzato 0–1 del segnale L+R e L–R prima della modulazione MPX), aggiornati ogni chunk (10 ms) e disponibili nella risposta `GET` UDP e in `/api/status`.

### Pannello RDS avanzato

- **PS lungo (16 chr)**: campo con anteprima live di PS1 e PS2; inviato a blocchi di 8 ogni `ps_cycle_sec` secondi
- **Modalità RT**: `TESTO FISSO` oppure `TITOLO CANZONE` (Icecast)
- **RT principale** (64 chr): testo fisso o titolo Icecast; inviato **completo** al modulatore
- **RT alternativo** (64 chr): testo alternativo (es. URL stazione); se non vuoto, cicla con il principale ogni `rt_alt_sec` secondi
- **URL Icecast**: endpoint JSON status (es. `http://host:8000/status-json.xsl`)
- **Fetch Icecast**: intervallo polling in secondi
- **Display in aria**: titolo corrente, RT in onda, slot attivo (MAIN/ALT), metà PS in onda

---

## 6. Persistenza EEPROM (storage.py)

### Dispositivo: AT24C512 (512kbit = 64 KB, pagine da 128 B, I2C 0x50)

### Mappa memoria

```
Offset   Size  Magic   Gruppo        Contenuto
────────────────────────────────────────────────────────────────────
0x0000   128B  0xFE02  sn            Numero di serie (binario)
0x0080   256B  0xFE10  tx_audio      Gain (master, L, R, linked), mono_mode, volumi MPX,
                                     TX freq/gain, enfasi, mute, PI, PTY, AF1, AF2, TA, TP, MS
0x0180   256B  0xFE11  compressor    comp_en, thr, ratio, knee, atk, rel, mu, lim
0x0280   256B  0xFE12  rds_cfg       rt_mode, rt_alt_sec, ps_long, ps_cycle_sec,
                                     radio_name, icecast_url, icecast_interval_sec
0x0380   256B  0xFE13  rds_text      rt_fixed (64B), rt_alt (64B)
0x0480   256B  0xFE14  power_pid     power_target_w, kp, ki, kd, soglie allarmi
────────────────────────────────────────────────────────────────────
Usato: 1408 B / 65536 B  (2.1%)
```

### Formato header blocco (7 byte)

```
[0-1]  magic    uint16 big-endian  — identifica il gruppo
[2]    version  uint8              — versione formato (attuale: 1)
[3-4]  length   uint16 big-endian  — lunghezza payload JSON in byte
[5-6]  crc16    uint16 big-endian  — CRC16-CCITT del solo payload
[7…]   payload  JSON compact UTF-8
[N…]   0xFF     padding fino alla fine del blocco
```

### Formato SN (binario fisso, 0x0000)

```
[0-1]   magic 0xFE02  uint16 big-endian
[2-17]  SN string     16 byte null-padded ASCII
[18-19] CRC16         dei byte 2-17
[20…]   0xFF          padding
```

### Accesso I2C

Utilizza `smbus2.i2c_rdwr` + `i2c_msg` per:
- **Scrittura**: page-aligned da 128 B, pausa 5 ms per ciclo di scrittura (tWR AT24C512)
- **Lettura**: sequenziale, nessun limite di pagina

### Selezione backend

| Condizione | Backend | Note |
|------------|---------|-------|
| Raspberry Pi + smbus2 | `EepromStorage` | Produzione |
| `USE_JSON=1` o no smbus2 | `JsonStorage` | Solo sviluppo — warning in log |

In sviluppo su PC: `export USE_JSON=1` → usa `~/.fmmod/storage.json`.

---

## 7. RDS Manager

Il thread `rds_manager_thread` (tick 500 ms) gestisce tutto in modo autonomo:

### PS cycling

```
ps_long (max 16 chr)  →  PS1 = ps_long[:8]  /  PS2 = ps_long[8:]
Ogni ps_cycle_sec secondi alterna: send_cmd("PS=PS1") ↔ send_cmd("PS=PS2")
```

### RT — modalità FIXED

```
Ogni rt_alt_sec secondi (solo se rt_alt non vuoto):
  slot 0: send_cmd("RT=" + rt_fixed[:64])
  slot 1: send_cmd("RT=" + rt_alt[:64])

Se rt_alt vuoto: invia rt_fixed una volta sola quando cambia.
```

### RT — modalità SONG (Icecast)

```
Ogni icecast_interval_sec: fetch JSON Icecast → estrae "title"
Se titolo cambiato:
  → aggiorna current_title, current_rt
  → invia subito: send_cmd("RT=" + title[:64])

Ogni rt_alt_sec (se rt_alt non vuoto):
  → cicla tra current_rt (titolo) e rt_alt (es. URL stazione)
```

> **Nota**: il RT viene sempre inviato **completo** (max 64 chr). Il modulatore C++ gestisce internamente la suddivisione in gruppi RDS 2A e il ciclo sui 57 kHz. Non è necessario né corretto spezzare il testo a livello software.

### Configurazione via API

```bash
# Lettura stato corrente
curl http://localhost:8080/api/rds/status

# Modalità song con RT alternativo
curl -X POST http://localhost:8080/api/rds/config \
  -H "Content-Type: application/json" \
  -d '{"rt_mode":"song","icecast_url":"http://host:8000/status-json.xsl",
       "rt_alt":"www.miaradio.it","rt_alt_sec":20,
       "ps_long":"MY RADIO","ps_cycle_sec":5}'

# Modalità testo fisso
curl -X POST http://localhost:8080/api/rds/config \
  -H "Content-Type: application/json" \
  -d '{"rt_mode":"fixed","rt_fixed":"Musica tutto il giorno!",
       "rt_alt":"www.miaradio.it","rt_alt_sec":30}'
```

---

## 8. Hardware

### Connessioni I2C

```
Raspberry Pi / CM4
  GPIO2 (SDA) ──┬──► ADS1115  0x48  (ADDR → GND)
                ├──► MCP4725  0x60  (A0/A1 → GND)
                ├──► AT24C512 0x50  (A0/A1/A2 → GND, WP → GND)
                ├──► DAC121   0x0D  (12-bit DAC I2C)
                ├──► TCA9534  0x20  (8-bit GPIO expander)
                └──► TLV320ADC6140 0x4C (audio ADC 4ch I2S master)
  GPIO3 (SCL) ──┴── (tutte le periferiche)

  SPI0 ─────────► ADS7066  CE0/CE1  (8-ch ADC 16-bit SPI)
```

### ADS1115 — ADC 4 canali 16-bit

| Canale | Segnale | Conversione |
|--------|---------|-------------|
| CH0 | Temperatura | LM35: `temp = V × 100` (°C/V = 0.01 V/°C) |
| CH1 | V_FWD (AD8307) | `volt_to_dbm(V, slope=25mV/dB, intercept=-84dBm)` |
| CH2 | V_REF (AD8307) | idem |
| CH3 | Libero | — |

Configurazione: gain ±4.096 V (FSR), 250 SPS single-shot.

### MCP4725 — DAC 12-bit (0–4095 → 0–3.3 V)

Uscita collegata all'ingresso ALC o attenuatore variabile del finale RF. Il PID mantiene `fwd_w ≈ power_target_w`.

### AT24C512 — EEPROM 512kbit

```
SCL  → GPIO3
SDA  → GPIO2
WP   → GND  (write enable)
A0/A1/A2 → GND  (indirizzo 0x50)
VCC  → 3.3 V
```

### ADS7066 — ADC 8 canali 16-bit (SPI)

Driver: `drivers/ADS7066/ads7066.py`

ADC SPI a 16 bit con 8 ingressi analogici multiplexati. Operato in modalità auto-sequenza o manuale.

| Pin ADS7066 | GPIO Raspberry Pi |
|-------------|-------------------|
| SCLK | GPIO11 (SPI0 SCLK) |
| DIN (MOSI) | GPIO10 (SPI0 MOSI) |
| DOUT (MISO) | GPIO9 (SPI0 MISO) |
| CS | GPIO8 (SPI0 CE0) o GPIO7 (CE1) |
| VDD/AVDD | 3.3 V o 5 V |

```python
from ads7066 import ADS7066
adc = ADS7066(bus=0, device=0, vref=5.0)
voltage = adc.get_voltage(channel=0)          # lettura canale singolo
voltage, ch = adc.get_voltage_auto()          # lettura auto-sequenza
```

Script di test completo: `drivers/ADS7066/test_ads7066.py`
Supporta acquisizione multipla, visualizzazione matplotlib, export CSV.

### DAC121 — DAC 12-bit I2C

Driver: `drivers/DAC121/dac121.py`

DAC 12-bit con uscita singola e modalità di power-down configurabile.
Indirizzo I2C di default: `0x0D` (configurabile via pin).

```python
from dac121 import DAC121
dac = DAC121(bus_number=1, address=0x0D, vref=5.0)
dac.set_voltage(2.5)
v = dac.get_voltage()
```

Modalità power-down: `MODE_NORMAL`, `MODE_2_5K_GND`, `MODE_100K_GND`, `MODE_HIGH_IMP`.

### M24512 / AT24C512 — EEPROM generica I2C

Driver: `drivers/EEPROM/m24512.py` (classe `N24512`)

Compatibile con AT24C512 e M24512. Pagine da 128 B, indirizzo 16-bit.
Scrittura in blocchi max 31 byte (limite buffer I2C Linux). Pausa 5 ms per ciclo write interno.

```python
from m24512 import N24512
eeprom = N24512(bus_number=1, address=0x50)
eeprom.write_byte(0x0100, 0xAA)
val = eeprom.read_byte(0x0100)
eeprom.write_page(0x0200, [0x01, 0x02, 0x03])
block = eeprom.read_block(0x0200, 10)
```

Nota: il driver di produzione per la persistenza dei parametri è `control/storage.py` (usa `smbus2` direttamente). `drivers/EEPROM/m24512.py` è il driver standalone di basso livello.

### TCA9534 — GPIO expander 8-bit I2C

Driver: `drivers/PCA9534/pca9534.py` (classe `TCA9534`, compatibile PCA9534/TCA9534)

Espansore I/O a 8 pin su I2C. Indirizzo di default `0x20` (A0/A1/A2 → GND).

```python
from pca9534 import TCA9534
gpio = TCA9534(bus_number=1, address=0x20)
gpio.set_gpio_mode(0, 0)    # pin 0 → OUTPUT
gpio.set_gpio(0, True)      # pin 0 → HIGH
state = gpio.get_gpio(1)    # legge pin 1
gpio.set_gpio_invert(1, True)  # polarità invertita
```

### TLV320ADC6140 — Audio ADC 4 canali I2S

Driver: `drivers/TLV320ADC6140/TLV320ADC.py` (classe `TLV320ADC`)

ADC audio professionale a 4 canali (fino a 8 canali PDM), master I2S/TDM, sample rate fino a 384 kHz.
Richiede MCLK 24.576 MHz su GPIO1 (configurato come input MCLK via registro `ADCX140_GPIO_CFG0`).
Pin standby/shutdown: GPIO4 (BCM), attivo alto.

| Parametro | Valore |
|-----------|--------|
| Indirizzo I2C | 0x4C |
| Sample rate supportati | 48 / 96 / 192 / 384 kHz |
| Canali | 4 (ingressi MIC/LINE diff/single) |
| Gain analogico | 0–42 dB (step 0.25 dB) |
| Gain digitale | -100–+27 dB (step 0.5 dB) |
| Output | I2S, Left Justified, TDM (word length 16/20/24/32 bit) |

```python
from TLV320ADC import TLV320ADC
adc = TLV320ADC(i2c_address=0x4C)
adc.set_wake()
adc.set_power_config()
adc.set_communication(samplerate=48)
adc.set_output_type(protocol="I2S", word_length=32, compatibility=True)
adc.set_input(channel=1, in_type="LINE", config="DIFF", coupling="AC", impedance=2.5)
adc.set_analog_gain(1, analog_gain_db=20)
adc.set_input_power([1, 2], power="ON", enable=True)
adc.set_adc_power(mic_bias="OFF", vref_volt=2.75)
adc.set_digital_gain(channel=1, digital_gain_db=0.0)
```

Script di test: `drivers/TLV320ADC6140/test_192khz.py` (192 kHz, LINE, SINGLE, DC)
e `drivers/TLV320ADC6140/test_384khz.py` (384 kHz).
Riferimento: `drivers/TLV320ADC6140/sbaa382.pdf` (SBAA382, TI application note — configurazione I2S master).

### Rilevatore di potenza (AD8307)

```
Accoppiatore direzionale 50Ω
  FWD port → AD8307 → V_FWD → ADS1115_CH1
  REF port → AD8307 → V_REF → ADS1115_CH2

Calibrazione:
  slope     = 25.0 mV/dB   (tipico AD8307)
  intercept = -84.0 dBm    (da datasheet / misura)
```

### PlutoSDR (ADALM-PLUTO)

Connesso via USB. Controllato con libiio. Parametri configurabili a runtime:
- `TX_FREQ=<MHz>` — frequenza LO (es. `TX_FREQ=103.5`)
- `TX_GAIN=<dBFS>` — gain hardware (es. `TX_GAIN=-17`)

---

## 9. API HTTP

Tutte le API rispondono in `application/json`.

### GET `/api/status`

Stato completo (params, metering, sensori, allarmi).

```json
{
  "params":   { "gain": 0.0, "tx_freq": 100.0, "ps": "MY_RADI", ... },
  "metering": { "comp_gr_db": -3.2, "mpx_peak": 0.87, ... },
  "sensors":  { "temp_c": 42.1, "fwd_w": 4.8, "swr": 1.12, "dac_value": 2100 },
  "alarms":   { "temp_high": false, "swr_high": false, ... },
  "softstart": false,
  "power_target_w": 5.0,
  "serial_number": "FM2024001",
  "storage_backend": "EEPROM"
}
```

### POST `/api/cmd`

Invia un comando UDP al modulatore.

```json
{ "cmd": "TX_GAIN=-20" }
```

### GET `/api/history`

Storico ultimi 10 minuti (600 punti @ 1 Hz).

```json
{ "ts": [...], "temp": [...], "fwd": [...], "ref": [...], "swr": [...], "comp_gr": [...] }
```

### POST `/api/eeprom/save`

Salva tutti i parametri in EEPROM (6 gruppi).

```json
{ "ok": true, "groups": { "tx_audio": true, "compressor": true, "rds_cfg": true, ... }, "backend": "EEPROM" }
```

### POST `/api/eeprom/load`

Carica tutti i gruppi e applica le impostazioni al modulatore.

### POST `/api/softstart`

Avvia soft-start DAC.

```json
{ "dac_target": 2000 }
```

### POST `/api/pid`

Imposta target PID e coefficienti.

```json
{ "target_w": 5.0, "kp": 0.8, "ki": 0.05, "kd": 0.02 }
```

### GET `/api/rds/status`

Stato del RDS manager.

```json
{
  "cfg":   { "rt_mode": "song", "ps_long": "MY RADIO", "icecast_url": "...", ... },
  "state": { "current_title": "Artista - Titolo", "rt_slot": 0, "ps_half": 1 },
  "ps1": "MY RADI", "ps2": "O       "
}
```

### GET/POST `/api/rds/config`

Legge o aggiorna la configurazione RDS manager.

### GET `/api/csv`

Download CSV sensori (`/tmp/fmmod_logs/sensors.csv`).

---

## 10. Comandi UDP modulatore

Porta UDP **9120**. Comandi in testo ASCII, uno per pacchetto.

### Lettura stato

```
GET    →  risposta multiline KEY=VALUE

Campi restituiti (oltre a tutti i parametri settabili):
  GAIN_L, GAIN_R, GAINS_LINKED, MONO_MODE
  COMP_GR, COMP_IN, COMP_OUTPK
  MPX_PEAK, MPX_RMS, MONO_PEAK, STEREO_PEAK
  PS, RT, PI, PTY, TA, TP, MS, AF1, AF2
```

### Audio

```
GAIN=<dB>             gain master L+R (-24..+24); aggiorna anche gain_l e gain_r
GAIN_L=<dB>           gain canale L (-24..+24); usato solo se GAINS_LINKED=0
GAIN_R=<dB>           gain canale R (-24..+24); usato solo se GAINS_LINKED=0
GAINS_LINKED=0|1      1=gain L e R si muovono insieme tramite GAIN=
MONO_MODE=0|1|2|3     0=stereo, 1=mono da L (R←L), 2=mono da R (L←R), 3=mix (L+R)/2
MUTE=0|1
VOL_PILOT=<0-1>       volume sottoportante 19 kHz
VOL_RDS=<0-1>         volume sottoportante RDS 57 kHz
VOL_MONO=<0-1>        volume canale mono (L+R)
VOL_STEREO=<0-1>      volume canale stereo (L-R)
PREEMPH=<0|50|75>     pre-enfasi in µs (0=lineare)
DEEMPH=<0|75>         de-enfasi in µs (0=lineare)
```

### RDS

```
PS=<8chr>          Programme Service (8 caratteri, padded)
RT=<64chr>         RadioText (max 64 caratteri, completo)
PI=<4hex>          Programme Identifier (es. PI=E123)
PTY=<0-31>         Programme Type
AF1=<0|875-1080>   Alternative Frequency (0=off, es. 1015 = 101.5 MHz)
TA=0|1             Traffic Announcement
RDS_LOG_BIN=1      log binario su /tmp/rds_stream.bin
```

### PlutoSDR

```
TX_FREQ=<MHz>     frequenza LO (es. TX_FREQ=103.5)
TX_GAIN=<dBFS>    gain hardware (-90..0)
```

### Compressore

```
COMP_EN=0|1
COMP_THR=<dBFS>   soglia (es. COMP_THR=-18)
COMP_RATIO=<x>    rapporto (es. COMP_RATIO=4)
COMP_KNEE=<dB>    knee width
COMP_ATK=<ms>     attack time
COMP_REL=<ms>     release time
COMP_MU=<dB>      makeup gain
COMP_LIM=<0-1>    hard limiter finale (0=off)
```

### Debug

```
DEBUG=0|1
```

---

## 11. Installazione e deploy

### Script di sviluppo

| Script | Uso |
|--------|-----|
| `compile.sh` | Shorthand per `cd build && make clean && make && cd ..` |
| `deploy.sh` | Sync + build + restart su Raspberry Pi via rsync/SSH |
| `import.sh` | Importa file singoli o `files.zip` da `~/Scaricati` nel progetto |
| `pack.sh` | Crea `codebase.tgz` escludendo build, pycache, file raw |

#### deploy.sh

```bash
./deploy.sh                  # sync + build C++ + restart fmmod+fmweb
./deploy.sh --skip-build     # solo sync + restart
./deploy.sh --web-only       # solo sync + restart fmweb
./deploy.sh --skip-restart   # solo sync (e build)
```

Il target predefinito è `pi@192.168.76.103:/home/rfe/modulatore_2.0`.
Esegue rsync escludendo `.git`, `build`, `__pycache__`, file raw/binari, poi lancia `cmake + make -j4` sul Pi e riavvia i servizi systemd.

#### import.sh

Cerca file per nome in `~/Scaricati`, `~/Downloads` e nella directory del progetto.
Se trova `files.zip`, estrae i file mappati. Supporta `--deploy`, `--web-only`, `--skip-build` (passa le opzioni a `deploy.sh` dopo l'import).

### Dipendenze

```bash
pip3 install bottle smbus2 requests
```

`requests` è necessario solo per il fetch Icecast (modalità RT = song). Se non installato, la modalità song è disabilitata ma il server funziona normalmente.

### Struttura file

```
/opt/fmmod/
  ├── server.py
  ├── storage.py
  ├── index.html
  └── fonts/
        ├── barlow-condensed-300.woff2
        ├── barlow-condensed-400.woff2
        ├── barlow-condensed-600.woff2
        ├── barlow-condensed-700.woff2
        ├── orbitron.woff2
        └── share-tech-mono.woff2
```

### Avvio manuale

```bash
python3 /opt/fmmod/server.py
```

### Servizi systemd

```ini
# /etc/systemd/system/fmmod.service
[Unit]
Description=FM Modulator (C++)
After=network.target

[Service]
ExecStart=/opt/fmmod/modulatore --udp=9121
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/fmweb.service
[Unit]
Description=FM Processor Web Server
After=network.target fmmod.service

[Service]
ExecStart=/usr/bin/python3 /opt/fmmod/server.py
WorkingDirectory=/opt/fmmod
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now fmmod fmweb
```

### Variabili d'ambiente

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `USE_JSON=1` | — | Forza backend JSON (sviluppo) |
| `FMMOD_JSON_STORAGE` | `~/.fmmod/storage.json` | Path file JSON dev |
| `MODULATORE_LOG` | `/tmp/modulatore.log` | Log modulatore C++ |

---

## 12. Flowgraphs GNURadio (`flowgraphs/`)

Flowgraph di test e analisi per lo sviluppo. Ogni `.grc` ha il corrispondente `.py` generato da GNURadio Companion.

| File | Descrizione |
|------|-------------|
| `cos_sig.grc/.py` | Generatore di segnale cosinusoidale — utile per test MPX e calibrazione |
| `cw.grc/.py` | Generatore CW (Morse) — genera burst RF per test antenna/PA |
| `peak_tagger.py` | Tagger di picchi sul segnale FM — per analisi deviazione e clipping |
| `rds_rx.py` | Ricevitore RDS base |
| `rds_rx_peaks.py` | Ricevitore RDS con rilevamento picchi MPX |
| `rds_rx_singh.py` | Ricevitore RDS con demodulazione variante Singh |

I flowgraphs sono standalone e non dipendono dal modulatore C++. Richiedono GNURadio 3.10+.

---

## 13. Font offline

L'interfaccia web non effettua richieste verso internet a runtime. I font sono serviti localmente da Bottle tramite la route `/fonts/<filename>`.

| Font | Pesi | Uso |
|------|------|-----|
| Orbitron | 400, 700, 900 | Logo, titoli |
| Barlow Condensed | 300, 400, 600, 700 | Testo generale, pannelli |
| Share Tech Mono | 400 | Valori numerici, display |

Per rigenerare i font (se necessario aggiornare versione):

```bash
cd /opt/fmmod/fonts
curl -sL -o barlow-condensed-300.woff2 "https://fonts.gstatic.com/s/barlowcondensed/v13/HTxwL3I-JCGChYJ8VI-L6OO_au7B47rxz3bWuYMBYro.woff2"
curl -sL -o barlow-condensed-400.woff2 "https://fonts.gstatic.com/s/barlowcondensed/v13/HTx3L3I-JCGChYJ8VI-L6OO_au7B6xHT2lv0tKk.woff2"
curl -sL -o barlow-condensed-600.woff2 "https://fonts.gstatic.com/s/barlowcondensed/v13/HTxwL3I-JCGChYJ8VI-L6OO_au7B4873z3bWuYMBYro.woff2"
curl -sL -o barlow-condensed-700.woff2 "https://fonts.gstatic.com/s/barlowcondensed/v13/HTxwL3I-JCGChYJ8VI-L6OO_au7B46r2z3bWuYMBYro.woff2"
curl -sL -o orbitron.woff2            "https://fonts.gstatic.com/s/orbitron/v35/yMJRMIlzdpvBhQQL_Qq7dy1biN15.woff2"
curl -sL -o share-tech-mono.woff2     "https://fonts.gstatic.com/s/sharetechmono/v16/J7aHnp1uDWRBEqV98dVQztYldFcLowEFA87Heg.woff2"
```
