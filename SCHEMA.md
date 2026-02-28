# FM Processor — Schema di Integrazione

## Architettura del sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                        MAINBOARD                                 │
│                                                                  │
│  ┌──────────────┐   stdin/UDP    ┌──────────────────────────┐   │
│  │   ffmpeg /   │ ─────────────► │   modulatore (C++)        │   │
│  │   sorgente   │                │   porta ctrl UDP :9120   │   │
│  └──────────────┘                └────────────┬─────────────┘   │
│                                               │ IQ / MPX         │
│  ┌──────────────────────────────────┐         │                  │
│  │   server.py (Bottle :8080)       │◄────────┘ UDP GET/SET     │
│  │                                  │                            │
│  │  /api/status   → polling stato   │                            │
│  │  /api/cmd      → invia cmandi    │                            │
│  │  /api/history  → storico CSV     │                            │
│  │  /api/eeprom/* → load/save       │                            │
│  │  /api/softstart → soft-start DAC │                            │
│  │  /api/pid      → target/params   │                            │
│  └─────────┬────────────────────────┘                            │
│            │ I2C (/dev/i2c-1)                                    │
│            ├──► ADS1115 (0x48)  — ADC 4ch 16-bit                │
│            │      CH0: temperatura (LM35 / NTC)                  │
│            │      CH1: tensione FWD (rilevatore log)             │
│            │      CH2: tensione REF (potenza riflessa)           │
│            │      CH3: libero                                     │
│            ├──► MCP4725 (0x60)  — DAC 12-bit                     │
│            │      OUT → ALC / attenuatore variabile → finale     │
│            └──► AT24C32 (0x50)  — EEPROM 32Kbit                  │
│                   Indirizzo 0: magic (2B) + len (2B) + JSON      │
│                                                                  │
│  Browser ──HTTP──► :8080/  (index.html servita da Bottle)        │
└─────────────────────────────────────────────────────────────────┘
```

## Flusso dati

```
Sorgente audio (ffmpeg/sox)
        │ PCM s16le 48kHz stereo (stdin o UDP:9121)
        ▼
modulatore C++
  ├─ SingleBandCompressor (compressor.hpp)
  ├─ Pre-enfasi / De-enfasi
  ├─ PolyphaseUpsampler 48k→912k
  ├─ MpxModulator (mono + pilot + stereo + RDS)
  └─ FmModulator → IQ → PlutoSDR
        │ UDP:9120 (controllo bidirezionale)
        ▼
server.py
  ├─ poll_modulatore()      ogni 200ms: GET → aggiorna state
  ├─ sensor_thread()        ogni 1s:   ADC → PID → DAC → allarmi
  └─ Bottle HTTP :8080
        │ HTTP JSON
        ▼
Browser (index.html)
  └─ pollStatus()           ogni 150ms: /api/status → aggiorna UI
```

## Hardware da collegare

### ADS1115 — Rilevatore di potenza
```
Accoppiatore direzionale (50Ω)
    │                │
  FWD port        REF port
    │                │
 rilevatore      rilevatore
 log (AD8307)   log (AD8307)
    │                │
  V_FWD (0-3.3V)  V_REF (0-3.3V)
    │                │
 ADS1115_CH1    ADS1115_CH2
```
Calibrazione in `server.py`:
```python
def volt_to_dbm(v, slope_mv_db=25.0, intercept_dbm=-84.0):
    # Adatta slope e intercept al tuo AD8307
```

### Temperatura
```
LM35 (o NTC con partitore):
  OUT → ADS1115_CH0
  LM35: 10 mV/°C → volt_to_temp: temp = v * 100
```

### MCP4725 — Controllo ALC/Potenza
```
DAC OUT (0-3.3V) → attenuatore variabile (HMC625 o sim.)
                → ingresso ALC finale RF
```
Il PID mantiene la potenza avanti al target impostato dall'interfaccia.

### EEPROM AT24C32
```
SCL → RPi/CM4 SCL (GPIO3)
SDA → RPi/CM4 SDA (GPIO2)
WP  → GND (write enable)
A0/A1/A2 → GND (indirizzo 0x50)
```
Struttura memoria (indirizzo 0):
```
[0-1]  magic = 0xFE01  (2 byte big-endian)
[2-3]  length           (2 byte big-endian)
[4-N]  JSON UTF-8       (max ~120 byte)
```

## Installazione

```bash
# Dipendenze Python
pip3 install bottle smbus2

# Struttura file
/opt/fmmod/
  ├── server.py
  ├── index.html
  └── logs/           ← creato automaticamente

# Avvio
python3 server.py

# Systemd service (opzionale)
cat > /etc/systemd/system/fmweb.service << EOF
[Unit]
Description=FM Processor Web Server
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/fmmod/server.py
WorkingDirectory=/opt/fmmod
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
EOF
systemctl enable --now fmweb
```

## Fix bug TX_FREQ/TX_GAIN (control_udp.cpp)

Nel file `control_udp.cpp`, righe 172 e 180, cambia:
```cpp
// PRIMA (bug: compare con n=9 su stringa di 8 char → sempre falso)
} else if ((msg.size() >= 9 && (msg.compare(0, 9, "TX_FREQ=") == 0 ...

// DOPO
} else if (msg.compare(0, 8, "TX_FREQ=") == 0 || msg.compare(0, 8, "tx_freq=") == 0) {
    float mhz = std::strtof(msg.c_str() + 8, nullptr);
    ...
} else if (msg.compare(0, 8, "TX_GAIN=") == 0 || msg.compare(0, 8, "tx_gain=") == 0) {
    float db = std::strtof(msg.c_str() + 8, nullptr);
    ...
```

## Integrazione compressore nel modulatore C++

In `audio_pipeline.cpp`, nel loop principale, aggiungere dopo la lettura dell'input:
```cpp
#include "compressor.hpp"

// In audio_processing_thread():
SingleBandCompressor compressor;
CompressorParams comp_params;

// Nel loop while(true):
// Aggiorna params da GlobalSettings (aggiungere a globals.hpp):
comp_params.threshold_db = settings.comp_threshold_db.load();
comp_params.ratio        = settings.comp_ratio.load();
comp_params.knee_db      = settings.comp_knee_db.load();
comp_params.attack_ms    = settings.comp_attack_ms.load();
comp_params.release_ms   = settings.comp_release_ms.load();
comp_params.makeup_db    = settings.comp_makeup_db.load();
comp_params.enabled      = settings.comp_enabled.load();

compressor.process(in_L_48k.data(), in_R_48k.data(), CHUNK_48K, comp_params);

// Esporre metering via GlobalSettings per il polling UDP:
settings.comp_gr_db.store(compressor.current_gr_db());
settings.comp_input_db.store(compressor.current_rms_db());
settings.comp_output_peak.store(compressor.current_output_peak());
```

Aggiungere in `control_udp.cpp` i comandi COMP_*:
```cpp
} else if (msg.compare(0, 9, "COMP_THR=") == 0) {
    float v = std::strtof(msg.c_str()+9, nullptr);
    settings.comp_threshold_db.store(v);
} else if (msg.compare(0, 11, "COMP_RATIO=") == 0) {
    float v = std::strtof(msg.c_str()+11, nullptr);
    settings.comp_ratio.store(v);
// ... etc per COMP_KNEE, COMP_ATK, COMP_REL, COMP_MU, COMP_EN
```

## Variabili da aggiungere in globals.hpp

```cpp
// Compressore
std::atomic<float> comp_threshold_db{-18.f};
std::atomic<float> comp_ratio{4.f};
std::atomic<float> comp_knee_db{6.f};
std::atomic<float> comp_attack_ms{5.f};
std::atomic<float> comp_release_ms{150.f};
std::atomic<float> comp_makeup_db{0.f};
std::atomic<bool>  comp_enabled{true};

// Metering compressore (scritti dal thread audio, letti dal server via GET)
std::atomic<float> comp_gr_db{0.f};
std::atomic<float> comp_input_db{-60.f};
std::atomic<float> comp_output_peak{-60.f};
std::atomic<float> mpx_peak{0.f};
std::atomic<float> mpx_rms{0.f};
```
