#!/usr/bin/env python3
"""
FM Modulator Web Server — Bottle backend
Interfaccia tra browser e modulatore C++ via UDP (porta 9120)
Gestione sensori I2C, EEPROM, DAC, PID, allarmi.
"""

import socket
import json
import time
import threading
import struct
import logging
import os
import csv
from datetime import datetime
from collections import deque

from bottle import Bottle, static_file, request, response, run
from storage import Storage
from chain_manager import ChainManager

# ─────────────────────────────────────────────
# Configurazione
# ─────────────────────────────────────────────
MODULATORE_HOST = "127.0.0.1"
MODULATORE_PORT = 9120          # porta controllo UDP modulatore

WEB_HOST = "0.0.0.0"
WEB_PORT = 8080

LOG_DIR  = "/tmp/fmmod_logs"
DATA_CSV = os.path.join(LOG_DIR, "sensors.csv")

# Sensori I2C (ADC/DAC) — la EEPROM è gestita da storage.py
I2C_BUS  = 1       # /dev/i2c-1
ADDR_ADC = 0x48    # ADS1115
ADDR_DAC = 0x60    # MCP4725

# Soglie allarmi
ALARM_TEMP_MAX   = 65.0    # °C
ALARM_SWR_MAX    = 2.5     # VSWR
ALARM_FWD_MIN    = 0.5     # W (potenza minima)
ALARM_FWD_MAX    = 12.0    # W (potenza massima)

# PID controllo potenza (output → DAC → attenutore/ALC)
PID_KP = 0.8
PID_KI = 0.05
PID_KD = 0.02

# Soft-start
SOFTSTART_STEP_MS  = 100   # ogni quanto ms incrementa
SOFTSTART_STEP_DAC = 50    # quanto incrementa il DAC per step (0-4095)

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "server.log")),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("fmweb")

# ─────────────────────────────────────────────
# Stato globale (thread-safe)
# ─────────────────────────────────────────────
class PIDController:
    def __init__(self, kp, ki, kd, out_min=0, out_max=4095):
        self.kp = kp; self.ki = ki; self.kd = kd
        self.out_min = out_min; self.out_max = out_max
        self._integral = 0.0
        self._prev_err = 0.0
        self._last_t = time.time()

    def reset(self):
        self._integral = 0.0
        self._prev_err = 0.0
        self._last_t = time.time()

    def compute(self, setpoint, measured):
        now = time.time()
        dt = max(now - self._last_t, 0.001)
        self._last_t = now
        err = setpoint - measured
        self._integral += err * dt
        # Anti-windup
        self._integral = max(-500, min(500, self._integral))
        deriv = (err - self._prev_err) / dt
        self._prev_err = err
        out = self.kp * err + self.ki * self._integral + self.kd * deriv
        return max(self.out_min, min(self.out_max, out))

# ─────────────────────────────────────────────
# Comunicazione UDP con il modulatore
# ─────────────────────────────────────────────
_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_udp_sock.settimeout(0.3)

class State:
    def __init__(self):
        self.serial_number: str | None = None
        self.lock = threading.Lock()

        # Parametri modulatore (mirror di GlobalSettings)
        self.params = {
            "gain": 0.0, "vol_pilot": 0.09, "vol_rds": 0.03,
            "vol_mono": 0.44, "vol_stereo": 0.44,
            "gain_l": 0.0, "gain_r": 0.0, "gains_linked": True, "mono_mode": 0,
            "mute_l": False, "mute_r": False,
            "phase_inv_r": False, "phase_offset": 0.0,
            "test_mode": 0, "test_tone_hz": 1000.0, "test_tone_amp": 0.5,
            "preemph": 0.0, "deemph": 0.0,
            "debug": False, "mute": False,
            "tx_freq": 100.0, "tx_gain": -17.0,
            "ps": "MY_RADIO", "rt": "Benvenuti su Cursor Radio",
            "pi": "5253", "pty": 2, "ta": 0, "tp": 0, "ms": 1, "af1": 0, "af2": 0,
            # Compressore
            "comp_en": True, "comp_thr": -18.0, "comp_ratio": 4.0,
            "comp_knee": 6.0, "comp_atk": 5.0, "comp_rel": 150.0,
            "comp_mu": 0.0, "comp_lim": 0.99,
        }

        # Metering dal modulatore
        self.metering = {
            "comp_input_db": -40.0,
            "comp_gr_db": 0.0,
            "comp_output_peak": -40.0,
            "mpx_peak": 0.0,
            "mpx_rms": 0.0,
            "mono_peak": 0.0,
            "stereo_peak": 0.0,
        }

        # Sensori hardware
        self.sensors = {
            "temp_c": 0.0,
            "fwd_w": 0.0,
            "ref_w": 0.0,
            "swr": 1.0,
            "dac_value": 0,       # 0-4095
            "dac_target": 0,      # target PID
            "pid_output": 0.0,
        }

        # Allarmi attivi
        self.alarms = {
            "temp_high": False,
            "swr_high": False,
            "fwd_low": False,
            "fwd_high": False,
        }

        # Storico (ultime N letture)
        self.history_size = 600   # 10 min a 1 Hz
        self.history = {
            "ts": deque(maxlen=self.history_size),
            "temp": deque(maxlen=self.history_size),
            "fwd": deque(maxlen=self.history_size),
            "ref": deque(maxlen=self.history_size),
            "swr": deque(maxlen=self.history_size),
            "comp_gr": deque(maxlen=self.history_size),
        }

        # Soft-start
        self.softstart_active = False
        self.softstart_target = 0

        # PID
        self.pid = PIDController(PID_KP, PID_KI, PID_KD)
        self.power_target_w = 5.0   # watt target per il PID

        # ── RDS avanzato ─────────────────────────────────────────────────────
        self.rds_cfg = {
            "radio_name":           "My Radio",
            "icecast_url":          "http://nr9.newradio.it:9371/status-json.xsl",
            "rt_mode":              "fixed",   # "fixed" | "song"
            # Modalità fixed: alterna tra rt_fixed e rt_alt (se rt_alt non vuoto)
            "rt_fixed":             "Ascolta la nostra radio!",
            "rt_alt":               "",        # testo alternativo (es. URL stream); vuoto = no ciclo
            "rt_alt_sec":           15.0,      # secondi tra alternanza rt_fixed ↔ rt_alt
            # Modalità song: titolo Icecast come RT principale; rt_alt come testo alternativo
            "ps_long":              "MY RADIO",  # fino a 16 chr → ciclato in blocchi da 8
            "ps_cycle_sec":         5.0,          # secondi tra alternanza PS
            "icecast_interval_sec": 15.0,         # polling Icecast (sec)
        }
        self.rds_state = {
            "current_title": "",   # titolo corrente da Icecast
            "current_rt":    "",   # RT principale attualmente in uso (testo completo)
            "rt_slot":       0,    # 0=RT principale, 1=RT alternativo
            "ps_half":       0,    # 0=prima metà PS, 1=seconda metà PS
        }

state = State()

# Storage unificato (EEPROM su RPi, JSON altrove)
storage = Storage(json_path=os.path.expanduser("~/.fmmod/storage.json"))

# ─────────────────────────────────────────────
# PID Controller
# ─────────────────────────────────────────────


def send_cmd(cmd: str) -> str | None:
    """Invia comando UDP al modulatore, ritorna risposta se presente."""
    try:
        _udp_sock.sendto(cmd.encode(), (MODULATORE_HOST, MODULATORE_PORT))
        if cmd.strip() in ("GET", "STATUS"):
            data, _ = _udp_sock.recvfrom(4096)
            return data.decode()
    except Exception as e:
        log.warning(f"UDP send_cmd '{cmd}': {e}")
    return None

def poll_modulatore():
    """Ogni 200 ms: invia GET e aggiorna state.params / state.metering."""
    while True:
        try:
            resp = send_cmd("GET")
            if resp:
                with state.lock:
                    for line in resp.strip().splitlines():
                        if "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip().lower()
                        v = v.strip()
                        m = state.params
                        try:
                            if k == "gain":        m["gain"]         = float(v)
                            elif k == "gain_l":    m["gain_l"]       = float(v)
                            elif k == "gain_r":    m["gain_r"]       = float(v)
                            elif k == "gains_linked": m["gains_linked"] = v == "1"
                            elif k == "mono_mode":    m["mono_mode"]    = int(v)
                            elif k == "mute_l":       m["mute_l"]       = v == "1"
                            elif k == "mute_r":       m["mute_r"]       = v == "1"
                            elif k == "phase_inv_r":  m["phase_inv_r"]  = v == "1"
                            elif k == "phase_offset": m["phase_offset"] = float(v)
                            elif k == "test_mode": m["test_mode"]    = int(v)
                            elif k == "test_tone_hz":  m["test_tone_hz"]  = float(v)
                            elif k == "test_tone_amp": m["test_tone_amp"] = float(v)
                            elif k == "vol_pilot": m["vol_pilot"]  = float(v)
                            elif k == "vol_rds":   m["vol_rds"]    = float(v)
                            elif k == "vol_mono":  m["vol_mono"]   = float(v)
                            elif k == "vol_stereo":m["vol_stereo"] = float(v)
                            elif k == "preemph":   m["preemph"]    = float(v)
                            elif k == "deemph":    m["deemph"]     = float(v)
                            elif k == "tx_freq":   m["tx_freq"]    = float(v)
                            elif k == "tx_gain":   m["tx_gain"]    = float(v)
                            elif k == "debug":     m["debug"]      = v == "1"
                            elif k == "mute":      m["mute"]       = v == "1"
                            elif k == "ps":        m["ps"]         = v
                            elif k == "rt":        m["rt"]         = v
                            elif k == "pi":        m["pi"]         = v
                            elif k == "pty":       m["pty"]        = int(v)
                            elif k == "ta":        m["ta"]         = int(v)
                            elif k == "tp":        m["tp"]         = int(v)
                            elif k == "ms":        m["ms"]         = int(v)
                            elif k == "af1":       m["af1"]        = int(v)
                            elif k == "af2":       m["af2"]        = int(v)
                            # Compressore parametri
                            elif k == "comp_en":    m["comp_en"]    = v == "1"
                            elif k == "comp_thr":   m["comp_thr"]   = float(v)
                            elif k == "comp_ratio": m["comp_ratio"] = float(v)
                            elif k == "comp_knee":  m["comp_knee"]  = float(v)
                            elif k == "comp_atk":   m["comp_atk"]   = float(v)
                            elif k == "comp_rel":   m["comp_rel"]   = float(v)
                            elif k == "comp_mu":    m["comp_mu"]    = float(v)
                            elif k == "comp_lim":   m["comp_lim"]   = float(v)
                            # Metering — scritto in state.metering
                            elif k == "comp_gr":    state.metering["comp_gr_db"]        = float(v)
                            elif k == "comp_in":    state.metering["comp_input_db"]     = float(v)
                            elif k == "comp_outpk": state.metering["comp_output_peak"] = float(v)
                            elif k == "mpx_peak":   state.metering["mpx_peak"]          = float(v)
                            elif k == "mpx_rms":    state.metering["mpx_rms"]           = float(v)
                            elif k == "mono_peak":  state.metering["mono_peak"]         = float(v)
                            elif k == "stereo_peak":state.metering["stereo_peak"]       = float(v)
                        except ValueError:
                            pass
        except Exception as e:
            log.debug(f"poll_modulatore: {e}")
        time.sleep(0.2)

# ─────────────────────────────────────────────
# I2C — Hardware (con fallback simulazione)
# ─────────────────────────────────────────────
try:
    import smbus2
    _bus = smbus2.SMBus(I2C_BUS)
    HAS_I2C = True
    log.info("I2C bus aperto")
except ImportError:
    HAS_I2C = False
    log.warning("smbus2 non disponibile — modalità simulazione sensori")

def read_ads1115(channel=0) -> float:
    """Legge tensione da ADS1115 sul canale dato (0-3). Ritorna volt."""
    if not HAS_I2C:
        # Simulazione: rumore + drift lento
        return 1.5 + 0.1 * (time.time() % 10) / 10.0
    try:
        # MUX: AIN0=0x4000, AIN1=0x5000, AIN2=0x6000, AIN3=0x7000
        mux = [0x4000, 0x5000, 0x6000, 0x7000][channel]
        config = 0x8000 | mux | 0x0200 | 0x0010 | 0x0003
        msb = (config >> 8) & 0xFF
        lsb = config & 0xFF
        _bus.write_i2c_block_data(ADDR_ADC, 0x01, [msb, lsb])
        time.sleep(0.009)  # conversion time @250SPS
        data = _bus.read_i2c_block_data(ADDR_ADC, 0x00, 2)
        raw = (data[0] << 8) | data[1]
        if raw > 32767:
            raw -= 65536
        return raw * 4.096 / 32768.0
    except Exception as e:
        log.warning(f"ADS1115 ch{channel}: {e}")
        return 0.0

def write_dac(value: int):
    """Scrive valore 0-4095 su MCP4725."""
    value = max(0, min(4095, value))
    if not HAS_I2C:
        return
    try:
        msb = (value >> 4) & 0xFF
        lsb = (value & 0x0F) << 4
        _bus.write_i2c_block_data(ADDR_DAC, 0x40, [msb, lsb])
    except Exception as e:
        log.warning(f"DAC write {value}: {e}")


# ─────────────────────────────────────────────
# Conversione tensione → grandezze fisiche
# ─────────────────────────────────────────────
# ADATTA questi valori al tuo accoppiatore direzionale!
# Esempio: rilevatore log AD8307, uscita 25 mV/dB, intercetta -84 dBm
def volt_to_dbm(v: float, slope_mv_db=25.0, intercept_dbm=-84.0) -> float:
    if v < 0.001:
        return -100.0
    return v * 1000.0 / slope_mv_db + intercept_dbm

def dbm_to_watt(dbm: float, load_ohm=50.0) -> float:
    return 10 ** ((dbm - 30.0) / 10.0)

def calc_swr(fwd_w: float, ref_w: float) -> float:
    if fwd_w <= 0:
        return 1.0
    rho = (ref_w / fwd_w) ** 0.5
    rho = min(rho, 0.999)
    return (1 + rho) / (1 - rho)

# ─────────────────────────────────────────────
# Thread sensori + PID + allarmi
# ─────────────────────────────────────────────
def sensor_thread():
    """Ogni 1 s: legge ADC, calcola potenza/SWR, aggiorna PID, gestisce allarmi."""
    # CSV header
    with open(DATA_CSV, "a", newline="") as f:
        csv.writer(f).writerow(["ts","temp_c","fwd_w","ref_w","swr","dac","gr_db"])

    while True:
        try:
            # ── Lettura ADC ──────────────────────────────────────────────
            v_temp = read_ads1115(0)   # canale 0: NTC o LM35
            v_fwd  = read_ads1115(1)   # canale 1: potenza avanti
            v_ref  = read_ads1115(2)   # canale 2: potenza riflessa

            # ── Conversioni ──────────────────────────────────────────────
            # LM35: 10 mV/°C — adatta se usi NTC
            temp_c = v_temp * 100.0

            fwd_dbm = volt_to_dbm(v_fwd)
            ref_dbm = volt_to_dbm(v_ref)
            fwd_w   = dbm_to_watt(fwd_dbm)
            ref_w   = dbm_to_watt(ref_dbm)
            swr     = calc_swr(fwd_w, ref_w)

            # ── PID ──────────────────────────────────────────────────────
            with state.lock:
                target = state.power_target_w
                softstart = state.softstart_active
            
            if not softstart:
                pid_out = state.pid.compute(target, fwd_w)
                dac_val = int(pid_out)
            else:
                dac_val = state.sensors["dac_value"]

            write_dac(dac_val)

            # ── Allarmi ──────────────────────────────────────────────────
            alarms = {
                "temp_high": temp_c > ALARM_TEMP_MAX,
                "swr_high":  swr    > ALARM_SWR_MAX,
                "fwd_low":   fwd_w  < ALARM_FWD_MIN and dac_val > 100,
                "fwd_high":  fwd_w  > ALARM_FWD_MAX,
            }

            # Se allarme attivo: riduzione automatica TX gain SOSPESA
            # (riabilitare quando i sensori sono calibrati con valori reali)
            # if any(alarms.values()):
            #     with state.lock:
            #         cur_gain = state.params.get("tx_gain", -17.0)
            #     new_gain = max(-40.0, cur_gain - 1.0)
            #     send_cmd(f"TX_GAIN={new_gain:.1f}")
            #     log.warning(f"ALLARME: {[k for k,v in alarms.items() if v]} → TX_GAIN={new_gain:.1f}")

            # ── Aggiorna state ────────────────────────────────────────────
            with state.lock:
                state.sensors.update({
                    "temp_c": round(temp_c, 1),
                    "fwd_w":  round(fwd_w, 2),
                    "ref_w":  round(ref_w, 2),
                    "swr":    round(swr, 2),
                    "dac_value": dac_val,
                    "pid_output": round(pid_out if not softstart else 0.0, 1),
                })
                state.alarms = alarms
                gr = state.metering.get("comp_gr_db", 0.0)

                # Storico
                ts = datetime.now().isoformat(timespec="seconds")
                state.history["ts"].append(ts)
                state.history["temp"].append(temp_c)
                state.history["fwd"].append(fwd_w)
                state.history["ref"].append(ref_w)
                state.history["swr"].append(swr)
                state.history["comp_gr"].append(gr)

            # CSV log
            with open(DATA_CSV, "a", newline="") as f:
                csv.writer(f).writerow([ts, temp_c, fwd_w, ref_w, swr, dac_val, gr])

        except Exception as e:
            log.error(f"sensor_thread: {e}")

        time.sleep(1.0)

# ─────────────────────────────────────────────
# Soft-start
# ─────────────────────────────────────────────
def softstart_thread(target_dac: int):
    """Porta il DAC da 0 al target in modo graduale."""
    log.info(f"Soft-start → DAC target {target_dac}")
    with state.lock:
        state.softstart_active = True
        state.sensors["dac_value"] = 0
    write_dac(0)
    current = 0
    while current < target_dac:
        current = min(current + SOFTSTART_STEP_DAC, target_dac)
        write_dac(current)
        with state.lock:
            state.sensors["dac_value"] = current
        time.sleep(SOFTSTART_STEP_MS / 1000.0)
    with state.lock:
        state.softstart_active = False
        state.pid.reset()
    log.info("Soft-start completato")

# ─────────────────────────────────────────────
# RDS Manager — PS cycling, RT cycling, Icecast
# ─────────────────────────────────────────────
try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    _requests = None
    HAS_REQUESTS = False
    log.warning("'requests' non installato — fetch Icecast disabilitato")


def _fetch_icecast_title(url: str) -> str | None:
    """Legge il titolo dalla JSON status di Icecast."""
    if not HAS_REQUESTS:
        return None
    try:
        r = _requests.get(url, timeout=5)
        data = r.json()
        source = data.get("icestats", {}).get("source")
        if source is None:
            return None
        if isinstance(source, list):
            title = source[0].get("title", "")
        else:
            title = source.get("title", "")
        return title.strip() or None
    except Exception as e:
        log.debug(f"Icecast fetch: {e}")
        return None


def _ps_halves(ps_long: str) -> tuple[str, str]:
    """Divide ps_long (max 16 chr) in due PS da 8 chr padded."""
    ps = ps_long.ljust(16)[:16]
    return ps[:8], ps[8:]


def rds_manager_thread():
    """
    Gestisce in modo autonomo:
      - Alternanza PS (ps_long[:8] ↔ ps_long[8:]) ogni ps_cycle_sec
      - Fetch Icecast ogni icecast_interval_sec (modalità 'song')
      - Alternanza RT tra testo principale e testo alternativo (rt_alt) ogni rt_alt_sec.
        Il testo viene sempre inviato COMPLETO (max 64 chr): è il modulatore C++
        a ciclarlo autonomamente sui gruppi RDS 2A — non serve spezzarlo qui.
    """
    last_icecast_fetch = 0.0
    last_ps_switch     = 0.0
    last_rt_switch     = 0.0

    while True:
        now = time.time()

        with state.lock:
            cfg = dict(state.rds_cfg)
            rs  = dict(state.rds_state)

        # ── Fetch Icecast (solo in modalità 'song') ───────────────────────────
        if cfg["rt_mode"] == "song":
            if now - last_icecast_fetch >= cfg["icecast_interval_sec"]:
                last_icecast_fetch = now
                title = _fetch_icecast_title(cfg["icecast_url"])
                if title and title != rs["current_title"]:
                    log.info(f"Icecast → nuovo titolo: {title}")
                    rt = title[:64]
                    with state.lock:
                        state.rds_state["current_title"] = title
                        state.rds_state["current_rt"]    = rt
                        state.rds_state["rt_slot"]       = 0
                    # Invia subito il titolo completo
                    send_cmd(f"RT={rt}")
                    last_rt_switch = now

        # ── Alternanza PS ─────────────────────────────────────────────────────
        if now - last_ps_switch >= cfg["ps_cycle_sec"]:
            last_ps_switch = now
            ps1, ps2 = _ps_halves(cfg["ps_long"])
            do_cycle = bool(ps2.strip())  # cicla solo se la seconda metà è non vuota
            with state.lock:
                if do_cycle:
                    next_half = 1 - state.rds_state["ps_half"]
                else:
                    next_half = 0
                state.rds_state["ps_half"] = next_half
            ps_to_send = ps1 if next_half == 0 else ps2
            # Invia sempre ps1 se non c'è seconda metà; invia entrambe se c'è ciclo
            if do_cycle or next_half == 0:
                send_cmd(f"PS={ps_to_send}")
                log.debug(f"PS → '{ps_to_send}' (slot={next_half}, ciclo={'sì' if do_cycle else 'no'})")

        # ── Alternanza RT principale ↔ alternativo ────────────────────────────
        # Solo se rt_alt non è vuoto e l'intervallo è scaduto
        rt_alt = cfg.get("rt_alt", "").strip()
        rt_alt_sec = cfg.get("rt_alt_sec", 15.0)
        if rt_alt and now - last_rt_switch >= rt_alt_sec:
            last_rt_switch = now
            with state.lock:
                next_slot = 1 - state.rds_state["rt_slot"]
                state.rds_state["rt_slot"] = next_slot
                if cfg["rt_mode"] == "fixed":
                    rt_main = cfg["rt_fixed"]
                else:
                    rt_main = state.rds_state["current_rt"] or cfg["rt_fixed"]
            rt_to_send = rt_alt if next_slot == 1 else rt_main
            if rt_to_send:
                send_cmd(f"RT={rt_to_send[:64]}")
                log.debug(f"RT → slot {next_slot}: '{rt_to_send[:40]}...'")
        elif not rt_alt and cfg["rt_mode"] == "fixed":
            # Nessun alternativo: invia RT fisso una volta sola quando cambia
            rt_fixed = cfg["rt_fixed"]
            if rt_fixed != rs.get("current_rt", ""):
                with state.lock:
                    state.rds_state["current_rt"] = rt_fixed
                send_cmd(f"RT={rt_fixed[:64]}")
                log.debug(f"RT fisso → '{rt_fixed[:40]}'")

        time.sleep(0.5)


# ─────────────────────────────────────────────
# Bottle App
# ─────────────────────────────────────────────
app = Bottle()

# ─── Catena webradio ──────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
chain = ChainManager(base_dir=_BASE_DIR)


def json_resp(data):
    response.content_type = "application/json"
    return json.dumps(data)

# ── API: status completo ─────────────────────
@app.route("/api/status")
def api_status():
    with state.lock:
        return json_resp({
            "params":   dict(state.params),
            "metering": dict(state.metering),
            "sensors":  dict(state.sensors),
            "alarms":   dict(state.alarms),
            "softstart": state.softstart_active,
            "power_target_w": state.power_target_w,
            "serial_number": state.serial_number,
            "storage_backend": storage.backend_name,
        })

# ── API: invia comando al modulatore ─────────
@app.route("/api/cmd", method="POST")
def api_cmd():
    data = request.json or {}
    cmd = data.get("cmd", "").strip()
    if not cmd:
        response.status = 400
        return json_resp({"error": "cmd vuoto"})
    resp = send_cmd(cmd)
    return json_resp({"ok": True, "response": resp})

# ── API: storico sensori ─────────────────────
@app.route("/api/history")
def api_history():
    with state.lock:
        return json_resp({
            "ts":     list(state.history["ts"]),
            "temp":   list(state.history["temp"]),
            "fwd":    list(state.history["fwd"]),
            "ref":    list(state.history["ref"]),
            "swr":    list(state.history["swr"]),
            "comp_gr":list(state.history["comp_gr"]),
        })

# ── API: save/load EEPROM (multi-gruppo) ─────
@app.route("/api/eeprom/save", method="POST")
def api_eeprom_save():
    results = {}
    with state.lock:
        p   = state.params
        cfg = state.rds_cfg

        results["tx_audio"] = storage.save_group("tx_audio", {
            "gain": p["gain"], "gain_l": p["gain_l"], "gain_r": p["gain_r"],
            "gains_linked": p["gains_linked"], "mono_mode": p["mono_mode"],
            "vol_pilot": p["vol_pilot"],
            "vol_rds": p["vol_rds"], "vol_mono": p["vol_mono"],
            "vol_stereo": p["vol_stereo"], "preemph": p["preemph"],
            "deemph": p["deemph"], "tx_freq": p["tx_freq"],
            "tx_gain": p["tx_gain"], "mute": p["mute"],
            "pi": p["pi"], "pty": p["pty"], "ta": p["ta"],
            "tp": p["tp"], "ms": p["ms"], "af1": p["af1"], "af2": p["af2"],
        })
        results["compressor"] = storage.save_group("compressor", {
            "comp_en": p["comp_en"], "comp_thr": p["comp_thr"],
            "comp_ratio": p["comp_ratio"], "comp_knee": p["comp_knee"],
            "comp_atk": p["comp_atk"], "comp_rel": p["comp_rel"],
            "comp_mu": p["comp_mu"], "comp_lim": p["comp_lim"],
        })
        results["rds_cfg"] = storage.save_group("rds_cfg", {
            "rt_mode": cfg["rt_mode"],
            "rt_alt_sec": cfg["rt_alt_sec"],
            "ps_long": cfg["ps_long"],
            "ps_cycle_sec": cfg["ps_cycle_sec"],
            "radio_name": cfg["radio_name"],
            "icecast_url": cfg["icecast_url"],
            "icecast_interval_sec": cfg["icecast_interval_sec"],
        })
        results["rds_text"] = storage.save_group("rds_text", {
            "rt_fixed": cfg["rt_fixed"],
            "rt_alt":   cfg["rt_alt"],
        })
        results["power_pid"] = storage.save_group("power_pid", {
            "power_target_w": state.power_target_w,
            "kp": state.pid.kp, "ki": state.pid.ki, "kd": state.pid.kd,
            "alarm_temp_max": ALARM_TEMP_MAX,
            "alarm_swr_max":  ALARM_SWR_MAX,
            "alarm_fwd_min":  ALARM_FWD_MIN,
            "alarm_fwd_max":  ALARM_FWD_MAX,
        })

    ok = all(results.values())
    log.info("EEPROM save: %s", results)
    return json_resp({"ok": ok, "groups": results, "backend": storage.backend_name})


@app.route("/api/eeprom/load", method="POST")
def api_eeprom_load():
    loaded = {}

    tx = storage.load_group("tx_audio")
    if tx:
        loaded["tx_audio"] = True
        cmd_map = {
            "gain": "GAIN", "gain_l": "GAIN_L", "gain_r": "GAIN_R",
            "vol_pilot": "VOL_PILOT", "vol_rds": "VOL_RDS",
            "vol_mono": "VOL_MONO", "vol_stereo": "VOL_STEREO",
            "preemph": "PREEMPH", "deemph": "DEEMPH",
            "tx_freq": "TX_FREQ", "tx_gain": "TX_GAIN",
            "pi": "PI", "pty": "PTY", "af1": "AF1", "af2": "AF2", "ta": "TA", "tp": "TP",
            "mono_mode": "MONO_MODE",
        }
        for k, cmd in cmd_map.items():
            if k in tx:
                send_cmd(f"{cmd}={tx[k]}")
        if "mute" in tx:
            send_cmd(f"MUTE={'1' if tx['mute'] else '0'}")
        if "ms" in tx:
            send_cmd(f"MS={'1' if tx['ms'] else '0'}")
        if "gains_linked" in tx:
            send_cmd(f"GAINS_LINKED={'1' if tx['gains_linked'] else '0'}")
        with state.lock:
            state.params.update({k: v for k, v in tx.items() if k in state.params})

    comp = storage.load_group("compressor")
    if comp:
        loaded["compressor"] = True
        cmd_map_c = {
            "comp_thr": "COMP_THR", "comp_ratio": "COMP_RATIO",
            "comp_knee": "COMP_KNEE", "comp_atk": "COMP_ATK",
            "comp_rel": "COMP_REL", "comp_mu": "COMP_MU",
            "comp_lim": "COMP_LIM",
        }
        for k, cmd in cmd_map_c.items():
            if k in comp:
                send_cmd(f"{cmd}={comp[k]}")
        if "comp_en" in comp:
            send_cmd(f"COMP_EN={'1' if comp['comp_en'] else '0'}")
        with state.lock:
            state.params.update({k: v for k, v in comp.items() if k in state.params})

    rds_c = storage.load_group("rds_cfg")
    rds_t = storage.load_group("rds_text")
    if rds_c or rds_t:
        loaded["rds"] = True
        with state.lock:
            if rds_c:
                for k in ("rt_mode","rt_alt_sec","ps_long","ps_cycle_sec",
                          "radio_name","icecast_url","icecast_interval_sec"):
                    if k in rds_c:
                        state.rds_cfg[k] = rds_c[k]
            if rds_t:
                if "rt_fixed" in rds_t: state.rds_cfg["rt_fixed"] = rds_t["rt_fixed"]
                if "rt_alt"   in rds_t: state.rds_cfg["rt_alt"]   = rds_t["rt_alt"]
        # Invia subito il PS al modulatore (senza attendere il ciclo rds_manager_thread)
        if rds_c and "ps_long" in rds_c:
            ps1, _ = _ps_halves(rds_c["ps_long"])
            send_cmd(f"PS={ps1}")
        if rds_t and "rt_fixed" in rds_t:
            send_cmd(f"RT={rds_t['rt_fixed']}")

    pp = storage.load_group("power_pid")
    if pp:
        loaded["power_pid"] = True
        with state.lock:
            if "power_target_w" in pp: state.power_target_w = pp["power_target_w"]
            if "kp" in pp: state.pid.kp = pp["kp"]
            if "ki" in pp: state.pid.ki = pp["ki"]
            if "kd" in pp: state.pid.kd = pp["kd"]

    if not loaded:
        return json_resp({"ok": False, "error": "nessun dato in EEPROM",
                          "backend": storage.backend_name})
    log.info("EEPROM load: %s", loaded)
    return json_resp({"ok": True, "loaded": loaded, "backend": storage.backend_name})

# ── API: soft-start ──────────────────────────
@app.route("/api/softstart", method="POST")
def api_softstart():
    data = request.json or {}
    target = int(data.get("dac_target", 2000))
    with state.lock:
        state.softstart_target = target
    t = threading.Thread(target=softstart_thread, args=(target,), daemon=True)
    t.start()
    return json_resp({"ok": True, "dac_target": target})

# ── API: PID target ──────────────────────────
@app.route("/api/pid", method="POST")
def api_pid():
    data = request.json or {}
    with state.lock:
        if "target_w" in data:
            state.power_target_w = float(data["target_w"])
            state.pid.reset()
        if "kp" in data: state.pid.kp = float(data["kp"])
        if "ki" in data: state.pid.ki = float(data["ki"])
        if "kd" in data: state.pid.kd = float(data["kd"])
    return json_resp({"ok": True})

# ── API: download CSV ────────────────────────
@app.route("/api/csv")
def api_csv():
    return static_file("sensors.csv", root=LOG_DIR, download="sensors.csv")

# ── API: RDS config (GET / POST) ─────────────
@app.route("/api/rds/config", method=["GET", "POST"])
def api_rds_config():
    if request.method == "POST":
        data = request.json or {}
        with state.lock:
            cfg = state.rds_cfg
            for key in ("radio_name", "icecast_url", "rt_mode", "rt_fixed",
                        "rt_alt", "rt_alt_sec", "ps_long", "ps_cycle_sec",
                        "icecast_interval_sec"):
                if key in data:
                    val = data[key]
                    if key in ("ps_cycle_sec", "rt_alt_sec", "icecast_interval_sec"):
                        val = max(1.0, float(val))
                    cfg[key] = val
            cfg["ps_long"]  = str(cfg["ps_long"])[:16]
            cfg["rt_fixed"] = str(cfg["rt_fixed"])[:64]
            cfg["rt_alt"]   = str(cfg.get("rt_alt", ""))[:64]
        log.info(f"RDS config aggiornata: {data}")
        return json_resp({"ok": True})
    with state.lock:
        return json_resp(dict(state.rds_cfg))

# ── API: RDS stato corrente ───────────────────
@app.route("/api/rds/status")
def api_rds_status():
    with state.lock:
        cfg = dict(state.rds_cfg)
        rs  = dict(state.rds_state)
        ps1, ps2 = _ps_halves(cfg["ps_long"])
    return json_resp({
        "cfg":   cfg,
        "state": rs,
        "ps1":   ps1,
        "ps2":   ps2,
    })

# ── Serve font locali ────────────────────────
@app.route("/fonts/<filename>")
def serve_fonts(filename):
    return static_file(filename, root=os.path.join(os.path.dirname(__file__), "fonts"))

# ── Serve HTML ───────────────────────────────
@app.route("/")
@app.route("/index.html")
def index():
    return static_file("index.html", root=os.path.dirname(__file__))


# ─────────────────────────────────────────────
# API: Catena webradio
# ─────────────────────────────────────────────

@app.route("/api/chain/status")
def api_chain_status():
    return json_resp(chain.status())

@app.route("/api/chain/start", method="POST")
def api_chain_start():
    return json_resp(chain.start())

@app.route("/api/chain/stop", method="POST")
def api_chain_stop():
    return json_resp(chain.stop())

@app.route("/api/chain/restart", method="POST")
def api_chain_restart():
    return json_resp(chain.restart())

@app.route("/api/chain/config", method=["GET", "POST"])
def api_chain_config():
    if request.method == "POST":
        data = request.json or {}
        return json_resp(chain.update_cfg(data))
    return json_resp(chain.status()["cfg"])


# ─────────────────────────────────────────────
# Avvio
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # Numero di serie
    state.serial_number = storage.read_sn()
    if state.serial_number:
        log.info(f"Numero di serie: {state.serial_number}")
    else:
        log.warning("Nessun numero di serie in EEPROM")

    # Carica tutti i gruppi all'avvio
    tx = storage.load_group("tx_audio")
    if tx:
        with state.lock:
            state.params.update({k: v for k, v in tx.items() if k in state.params})
        log.info("EEPROM: tx_audio caricato")

    comp = storage.load_group("compressor")
    if comp:
        with state.lock:
            state.params.update({k: v for k, v in comp.items() if k in state.params})
        log.info("EEPROM: compressor caricato")

    rds_c = storage.load_group("rds_cfg")
    rds_t = storage.load_group("rds_text")
    if rds_c or rds_t:
        with state.lock:
            if rds_c:
                for k in ("rt_mode","rt_alt_sec","ps_long","ps_cycle_sec",
                          "radio_name","icecast_url","icecast_interval_sec"):
                    if k in rds_c: state.rds_cfg[k] = rds_c[k]
            if rds_t:
                if "rt_fixed" in rds_t: state.rds_cfg["rt_fixed"] = rds_t["rt_fixed"]
                if "rt_alt"   in rds_t: state.rds_cfg["rt_alt"]   = rds_t["rt_alt"]
        if rds_c and "ps_long" in rds_c:
            ps1, _ = _ps_halves(rds_c["ps_long"])
            send_cmd(f"PS={ps1}")
        if rds_t and "rt_fixed" in rds_t:
            send_cmd(f"RT={rds_t['rt_fixed']}")
        log.info("EEPROM: rds_cfg/rds_text caricati")

    pp = storage.load_group("power_pid")
    if pp:
        with state.lock:
            if "power_target_w" in pp: state.power_target_w = pp["power_target_w"]
            if "kp" in pp: state.pid.kp = pp["kp"]
            if "ki" in pp: state.pid.ki = pp["ki"]
            if "kd" in pp: state.pid.kd = pp["kd"]
        log.info("EEPROM: power_pid caricato")

    # Thread modulatore
    threading.Thread(target=poll_modulatore, daemon=True).start()
    # Thread sensori
    threading.Thread(target=sensor_thread, daemon=True).start()
    # Thread RDS manager (PS cycling, RT cycling, Icecast)
    threading.Thread(target=rds_manager_thread, daemon=True).start()

    # Avvia la catena webradio all'avvio
    result = chain.start()
    if result.get("ok"):
        log.info("Catena webradio avviata automaticamente")
    else:
        log.warning(f"Catena webradio non avviata: {result.get('error')}")

    log.info(f"Web server su http://{WEB_HOST}:{WEB_PORT}")
    run(app, host=WEB_HOST, port=WEB_PORT, quiet=True)
