# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**FM Processor 2.0** is a Raspberry Pi FM radio transmitter controller combining:
- A real-time C++ audio DSP pipeline targeting PlutoSDR (ADALM-PLUTO)
- A Python/Bottle web server with REST API
- RDS (Radio Data System) encoding and management
- I2C hardware integration (ADC, DAC, EEPROM)

## Build Commands

### C++ Modulator
```bash
mkdir -p build && cd build
cmake ..          # Requires: libiio-dev, cmake, build-essential
make -j4          # Produces: build/modulatore
```

CMakeLists.txt uses C++17 with `-O3 -march=native`. libiio (PlutoSDR) is optional — if not found, output falls back to stdout.

### Running the Modulator
```bash
# From stdin
ffmpeg -re -i stream.mp3 -f s16le -ac 2 -ar 48000 - | ./build/modulatore --stdin

# From UDP
./build/modulatore --udp=9121

# Test modes
./build/modulatore --test-stereo      # 5s L, 5s R, 5s L+R
./build/modulatore --test-separation  # Stereo isolation measurement
./build/modulatore --debug            # Print stats to stderr every ~1s

# Stdout output modes (no PlutoSDR)
./build/modulatore --no-pluto --fm-iq   # IQ int16
./build/modulatore --no-pluto           # MPX float32
```

### Running the Web Server
```bash
cd control
python3 server.py    # HTTP on port 8080
```

### Systemd Services (Raspberry Pi deployment)
```bash
systemctl start fmmod    # C++ modulator + ffmpeg
systemctl start fmweb    # Python web server
```

## Architecture

### Signal Flow (PCM → RF)
```
PCM 48 kHz (480 samples / 10 ms chunk)
  → input gain → hard limiter
  → de-emphasis IIR (optional, 50/75 µs)
  → SingleBandCompressor (feedforward RMS, soft-knee)
  → pre-emphasis IIR (50/75 µs)
  → PolyphaseUpsampler (48k → 912k, factor 19)
  → MpxModulator (mono + 19 kHz pilot + stereo 38 kHz + RDS 57 kHz)
  → FmModulator (baseband → IQ complex int16)
  → PlutoSDR via libiio  OR  stdout (IQ/MPX)
```

### Thread Model
- **Audio thread** (`audio_pipeline.cpp`): real-time 10 ms PCM loop
- **UDP control thread** (`control_udp.cpp`): listens on port 9120 for commands
- **Python `server.py`** spawns three background threads:
  - `poll_modulatore()` — queries modulator status every 200 ms
  - `sensor_thread()` — reads ADC, runs PID, writes DAC every 1 s
  - `rds_manager_thread()` — cycles PS/RT, fetches Icecast every 500 ms

### Control Path
```
Browser → HTTP:8080 → server.py → UDP:9120 → control_udp.cpp → GlobalSettings atomics
```

### Key Files
| File | Purpose |
|------|---------|
| `main.cpp` | Entry point, CLI parsing, thread launch |
| `audio_pipeline.cpp` | DSP loop: gain → limiter → compressor → upsampler → MPX → FM |
| `control_udp.cpp` | UDP command parser; maps text commands to `GlobalSettings` atomics |
| `globals.hpp` | All shared atomic state (audio params, RDS, compressor, PlutoSDR) |
| `pluto_output.cpp` | libiio PlutoSDR output or stdout fallback |
| `librds/rds.c` | RDS encoding: PS, RT, PI, PTY, AF1, AF2, TA, TP, MS |
| `control/server.py` | Bottle REST API + polling threads + PID power control |
| `control/storage.py` | EEPROM persistence via smbus2 (6 config groups with CRC16) |
| `control/chain_manager.py` | Audio source management (webradio, ALSA, tone, MPX-in) |
| `control/index.html` | Single-page UI (offline, no CDN), 5 tabs: TX/RDS, Compressor, Power, History, System |

### State Persistence (EEPROM AT24C512)
Six fixed-offset 256-byte blocks, each with a magic number and CRC16:
- `0x0080` — TX/Audio (gain, volumes, tx_freq, tx_gain, emphasis, PI, PTY, AF1)
- `0x0180` — Compressor
- `0x0280` — RDS Config (rt_mode, ps cycling, Icecast URL)
- `0x0380` — RDS Text (rt_fixed, rt_alt)
- `0x0480` — Power/PID (target_w, Kp, Ki, Kd, alarms)

Set `USE_JSON=1` to bypass EEPROM and use `~/.fmmod/storage.json` during development.

### UDP Command Protocol (port 9120)
Commands are plain text strings sent to the modulator, e.g.:
```
audio_gain:0.8
rds_ps:RadioName
rds_rt:Now playing...
rds_pi:0xC0DE
comp_enable:1
comp_threshold:-18.0
pluto_freq:98.0e6
```

## Python Dependencies
```
bottle    # HTTP server
smbus2    # I2C (Raspberry Pi only)
requests  # Optional: Icecast song title fetch
```

## Documentation
- `doc/ARCHITETTURA.md` — Full technical reference (626 lines): CLI args, UDP commands, HTTP API endpoints, hardware wiring, calibration
- `doc/SCHEMA.md` — Hardware integration diagrams and I2C peripheral connections
- `rpi_setup/setup.sh` — Full Raspberry Pi provisioning (WiFi AP, systemd, I2C)
