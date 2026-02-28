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

# ─────────────────────────────────────────────
# Configurazione
# ─────────────────────────────────────────────
MODULATORE_HOST = "127.0.0.1"
MODULATORE_PORT = 9120          # porta controllo UDP modulatore

WEB_HOST = "0.0.0.0"
WEB_PORT = 8080

LOG_DIR  = "/tmp/fmmod_logs"
DATA_CSV = os.path.join(LOG_DIR, "sensors.csv")
EEPROM_FILE = os.path.join(LOG_DIR, "eeprom.json")   # backup EEPROM su disco

# Sensori I2C — modifica gli indirizzi in base al tuo hardware
I2C_BUS          = 1       # /dev/i2c-1
ADDR_ADC         = 0x48    # ADS1115: temperatura + VSWR
ADDR_DAC         = 0x60    # MCP4725: controllo potenza
ADDR_EEPROM      = 0x50    # AT24C32 o simile

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
class State:
    def __init__(self):
        self.lock = threading.Lock()

        # Parametri modulatore (mirror di GlobalSettings)
        self.params = {
            "gain": 0.0, "vol_pilot": 0.09, "vol_rds": 0.03,
            "vol_mono": 0.44, "vol_stereo": 0.44,
            "preemph": 0.0, "deemph": 0.0,
            "debug": False, "mute": False,
            "tx_freq": 100.0, "tx_gain": -17.0,
            "ps": "MY_RADIO", "rt": "Benvenuti su Cursor Radio",
            "pi": "5253", "pty": 2, "ta": 0, "af1": 0,
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

state = State()

# ─────────────────────────────────────────────
# PID Controller
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
                            if k == "gain":        m["gain"]       = float(v)
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
                            elif k == "af1":       m["af1"]        = int(v)
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

def read_eeprom(addr: int, length: int) -> bytes:
    """Legge dalla EEPROM I2C."""
    if not HAS_I2C:
        return bytes(length)
    try:
        _bus.write_i2c_block_data(ADDR_EEPROM, (addr >> 8) & 0xFF, [addr & 0xFF])
        time.sleep(0.005)
        return bytes(_bus.read_i2c_block_data(ADDR_EEPROM, 0, length))
    except Exception as e:
        log.warning(f"EEPROM read @{addr}: {e}")
        return bytes(length)

def write_eeprom(addr: int, data: bytes):
    """Scrive sulla EEPROM I2C (max 32 byte per write cycle)."""
    if not HAS_I2C:
        return
    try:
        chunk = 32
        for i in range(0, len(data), chunk):
            block = list(data[i:i+chunk])
            a = addr + i
            _bus.write_i2c_block_data(ADDR_EEPROM, (a >> 8) & 0xFF, [a & 0xFF] + block)
            time.sleep(0.010)
    except Exception as e:
        log.warning(f"EEPROM write @{addr}: {e}")

# ─────────────────────────────────────────────
# EEPROM settings (load/save)
# ─────────────────────────────────────────────
EEPROM_MAGIC = 0xFE01   # versione struttura

def eeprom_load() -> dict | None:
    """Carica settings da EEPROM (o file backup)."""
    # Prova prima EEPROM hardware
    raw = read_eeprom(0, 128)
    magic = struct.unpack_from(">H", raw, 0)[0]
    if magic == EEPROM_MAGIC:
        try:
            length = struct.unpack_from(">H", raw, 2)[0]
            js = raw[4:4+length].decode("utf-8")
            return json.loads(js)
        except Exception:
            pass
    # Fallback su file
    if os.path.exists(EEPROM_FILE):
        try:
            with open(EEPROM_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return None

def eeprom_save(settings: dict):
    """Salva settings su EEPROM e file backup."""
    js = json.dumps(settings).encode("utf-8")
    raw = struct.pack(">HH", EEPROM_MAGIC, len(js)) + js
    raw += bytes(128 - len(raw)) if len(raw) < 128 else b""
    write_eeprom(0, raw[:128])
    # Backup su file
    with open(EEPROM_FILE, "w") as f:
        json.dump(settings, f, indent=2)
    log.info("Settings salvati su EEPROM + file")

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

            # Se allarme attivo: riduci TX gain progressivamente
            if any(alarms.values()):
                with state.lock:
                    cur_gain = state.params.get("tx_gain", -17.0)
                new_gain = max(-40.0, cur_gain - 1.0)
                send_cmd(f"TX_GAIN={new_gain:.1f}")
                log.warning(f"ALLARME: {[k for k,v in alarms.items() if v]} → TX_GAIN={new_gain:.1f}")

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
# Bottle App
# ─────────────────────────────────────────────
app = Bottle()

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

# ── API: save/load EEPROM ────────────────────
@app.route("/api/eeprom/save", method="POST")
def api_eeprom_save():
    with state.lock:
        settings = dict(state.params)
        settings["power_target_w"] = state.power_target_w
    eeprom_save(settings)
    return json_resp({"ok": True})

@app.route("/api/eeprom/load", method="POST")
def api_eeprom_load():
    s = eeprom_load()
    if s is None:
        return json_resp({"ok": False, "error": "nessun dato in EEPROM"})
    # Applica al modulatore
    cmd_map = {
        "gain": "GAIN", "vol_pilot": "VOL_PILOT", "vol_rds": "VOL_RDS",
        "vol_mono": "VOL_MONO", "vol_stereo": "VOL_STEREO",
        "preemph": "PREEMPH", "deemph": "DEEMPH",
        "tx_freq": "TX_FREQ", "tx_gain": "TX_GAIN",
        "ps": "PS", "rt": "RT", "pi": "PI", "pty": "PTY",
        "comp_thr": "COMP_THR", "comp_ratio": "COMP_RATIO",
        "comp_knee": "COMP_KNEE", "comp_atk": "COMP_ATK",
        "comp_rel": "COMP_REL", "comp_mu": "COMP_MU",
    }
    for k, cmd in cmd_map.items():
        if k in s:
            send_cmd(f"{cmd}={s[k]}")
    with state.lock:
        state.params.update({k: v for k, v in s.items() if k in state.params})
        if "power_target_w" in s:
            state.power_target_w = s["power_target_w"]
    return json_resp({"ok": True, "settings": s})

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

# ── Serve HTML ───────────────────────────────
@app.route("/")
@app.route("/index.html")
def index():
    return static_file("index.html", root=os.path.dirname(__file__))

# ─────────────────────────────────────────────
# Avvio
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # Carica settings da EEPROM all'avvio
    saved = eeprom_load()
    if saved:
        log.info("Settings caricati da EEPROM")
        with state.lock:
            state.params.update({k: v for k, v in saved.items() if k in state.params})

    # Thread modulatore
    threading.Thread(target=poll_modulatore, daemon=True).start()
    # Thread sensori
    threading.Thread(target=sensor_thread, daemon=True).start()

    log.info(f"Web server su http://{WEB_HOST}:{WEB_PORT}")
    run(app, host=WEB_HOST, port=WEB_PORT, quiet=True)
