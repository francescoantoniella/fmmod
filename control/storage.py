"""
storage.py — Backend di persistenza settings + numero di serie

Selezione automatica del backend:
  - Raspberry Pi (rilevato da /proc/cpuinfo o flag USE_JSON=1) → EEPROM I2C
  - Qualsiasi altra piattaforma, o USE_JSON=1                 → file JSON

Mappa EEPROM (AT24C32, 0x50):
  0x00–0x13  Blocco serializzazione (magic 2B + SN 16B + CRC16 2B)
  0x14–0x7F  Riservato
  0x80–0xFF  Settings JSON (magic 2B + len 2B + payload)

Il file JSON ha la stessa struttura logica:
  {
    "sn": "FM2024001",
    "settings": { ... }
  }
"""

import hashlib
import json
import logging
import os
import struct
import time

log = logging.getLogger("fmweb.storage")

# ── Indirizzi EEPROM ─────────────────────────────────────────
I2C_BUS              = 1
ADDR_EEPROM          = 0x50
MAGIC_SN             = 0xFE02   # blocco serializzazione
MAGIC_SETTINGS       = 0xFE01   # blocco settings
OFFSET_SN            = 0x00
OFFSET_SETTINGS      = 0x80
SETTINGS_MAX_BYTES   = 124      # 128 - 4 (header)

# ── Percorsi file ─────────────────────────────────────────────
DEFAULT_JSON_PATH = os.environ.get(
    "FMMOD_JSON_STORAGE",
    os.path.join(os.path.expanduser("~"), ".fmmod", "storage.json")
)

# ── CRC16 CCITT ───────────────────────────────────────────────
def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc

# ── Rilevamento piattaforma ───────────────────────────────────
def _is_raspberry_pi() -> bool:
    """True se siamo su Raspberry Pi e smbus2 è disponibile."""
    if os.environ.get("USE_JSON", "0") == "1":
        return False
    try:
        with open("/proc/cpuinfo") as f:
            info = f.read()
        if "Raspberry Pi" in info or "BCM" in info:
            import smbus2  # noqa — verifica disponibilità
            return True
    except Exception:
        pass
    return False

# ─────────────────────────────────────────────────────────────
# Backend EEPROM
# ─────────────────────────────────────────────────────────────
class EepromBackend:
    def __init__(self):
        import smbus2
        self._bus = smbus2.SMBus(I2C_BUS)
        log.info("Storage: EEPROM I2C @ 0x%02X bus %d", ADDR_EEPROM, I2C_BUS)

    def _write(self, mem_addr: int, data: bytes):
        chunk = 32
        for i in range(0, len(data), chunk):
            block = list(data[i:i+chunk])
            a = mem_addr + i
            self._bus.write_i2c_block_data(
                ADDR_EEPROM, (a >> 8) & 0xFF, [a & 0xFF] + block)
            time.sleep(0.010)

    def _read(self, mem_addr: int, length: int) -> bytes:
        self._bus.write_i2c_block_data(
            ADDR_EEPROM, (mem_addr >> 8) & 0xFF, [mem_addr & 0xFF])
        time.sleep(0.005)
        return bytes(self._bus.read_i2c_block_data(ADDR_EEPROM, 0, length))

    # ── Numero di serie ───────────────────────────────────────
    def read_sn(self) -> str | None:
        try:
            raw = self._read(OFFSET_SN, 0x14)
            magic = struct.unpack_from(">H", raw, 0)[0]
            if magic != MAGIC_SN:
                return None
            sn_bytes = raw[2:18]
            crc_ok = struct.unpack_from(">H", raw, 18)[0]
            if _crc16(sn_bytes) != crc_ok:
                log.warning("EEPROM SN: CRC errato")
                return None
            return sn_bytes.rstrip(b'\x00').decode('ascii')
        except Exception as e:
            log.warning("EEPROM read_sn: %s", e)
            return None

    def write_sn(self, sn: str) -> bool:
        try:
            sn_bytes = sn.encode('ascii').ljust(16, b'\x00')[:16]
            crc = _crc16(sn_bytes)
            block = struct.pack(">H", MAGIC_SN) + sn_bytes + struct.pack(">H", crc)
            block = block.ljust(0x80, b'\xff')
            self._write(OFFSET_SN, block[:0x80])
            log.info("EEPROM SN scritto: %s", sn)
            return True
        except Exception as e:
            log.error("EEPROM write_sn: %s", e)
            return False

    # ── Settings ──────────────────────────────────────────────
    def load_settings(self) -> dict | None:
        try:
            raw = self._read(OFFSET_SETTINGS, 128)
            magic = struct.unpack_from(">H", raw, 0)[0]
            if magic != MAGIC_SETTINGS:
                return None
            length = struct.unpack_from(">H", raw, 2)[0]
            if length == 0 or length > SETTINGS_MAX_BYTES:
                return None
            js = raw[4:4+length].decode('utf-8')
            return json.loads(js)
        except Exception as e:
            log.warning("EEPROM load_settings: %s", e)
            return None

    def save_settings(self, settings: dict) -> bool:
        try:
            js = json.dumps(settings, separators=(',', ':')).encode('utf-8')
            if len(js) > SETTINGS_MAX_BYTES:
                log.error("Settings troppo grandi per EEPROM (%d > %d byte)",
                          len(js), SETTINGS_MAX_BYTES)
                return False
            raw = struct.pack(">HH", MAGIC_SETTINGS, len(js)) + js
            raw = raw.ljust(128, b'\xff')
            self._write(OFFSET_SETTINGS, raw[:128])
            log.info("EEPROM settings salvati (%d byte)", len(js))
            return True
        except Exception as e:
            log.error("EEPROM save_settings: %s", e)
            return False

# ─────────────────────────────────────────────────────────────
# Backend JSON
# ─────────────────────────────────────────────────────────────
class JsonBackend:
    def __init__(self, path: str = DEFAULT_JSON_PATH):
        self._path = path
        dirpath = os.path.dirname(path)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        log.info("Storage: JSON file → %s", path)

    def _load_file(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    return json.load(f)
            except Exception as e:
                log.warning("JSON load: %s", e)
        return {}

    def _save_file(self, data: dict) -> bool:
        try:
            tmp = self._path + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._path)  # atomico
            return True
        except Exception as e:
            log.error("JSON save: %s", e)
            return False

    # ── Numero di serie ───────────────────────────────────────
    def read_sn(self) -> str | None:
        return self._load_file().get("sn")

    def write_sn(self, sn: str) -> bool:
        data = self._load_file()
        data["sn"] = sn
        return self._save_file(data)

    # ── Settings ──────────────────────────────────────────────
    def load_settings(self) -> dict | None:
        return self._load_file().get("settings")

    def save_settings(self, settings: dict) -> bool:
        data = self._load_file()
        data["settings"] = settings
        return self._save_file(data)

# ─────────────────────────────────────────────────────────────
# Factory — selezione automatica backend
# ─────────────────────────────────────────────────────────────
def create_backend(json_path: str = DEFAULT_JSON_PATH):
    """
    Crea il backend appropriato:
      - Raspberry Pi + smbus2 disponibile → EepromBackend
      - Altrimenti (o USE_JSON=1)          → JsonBackend

    In entrambi i casi il JsonBackend viene tenuto come backup:
    se EEPROM fallisce, i settings vengono letti/scritti su JSON.
    """
    if _is_raspberry_pi():
        try:
            return EepromBackend()
        except Exception as e:
            log.warning("EEPROM non disponibile (%s) → fallback JSON", e)
    return JsonBackend(json_path)


# ─────────────────────────────────────────────────────────────
# Wrapper con fallback automatico EEPROM → JSON
# ─────────────────────────────────────────────────────────────
class Storage:
    """
    Interfaccia unificata con fallback automatico.
    Su Raspberry: tenta EEPROM, se fallisce scrive anche su JSON.
    Altrove: solo JSON.
    """
    def __init__(self, json_path: str = DEFAULT_JSON_PATH):
        self._primary = create_backend(json_path)
        self._json_backup = None
        # Se il primary è EEPROM, mantieni anche il backup JSON
        if isinstance(self._primary, EepromBackend):
            self._json_backup = JsonBackend(json_path)
            log.info("Storage: EEPROM primario + JSON backup")

    @property
    def backend_name(self) -> str:
        return "EEPROM" if isinstance(self._primary, EepromBackend) else "JSON"

    def read_sn(self) -> str | None:
        sn = self._primary.read_sn()
        if sn is None and self._json_backup:
            sn = self._json_backup.read_sn()
            if sn:
                log.info("SN letto da JSON backup (EEPROM vuota)")
        return sn

    def write_sn(self, sn: str) -> bool:
        ok = self._primary.write_sn(sn)
        if self._json_backup:
            self._json_backup.write_sn(sn)  # sempre sincronizza il backup
        return ok

    def load_settings(self) -> dict | None:
        s = self._primary.load_settings()
        if s is None and self._json_backup:
            s = self._json_backup.load_settings()
            if s:
                log.info("Settings letti da JSON backup (EEPROM vuota)")
        return s

    def save_settings(self, settings: dict) -> bool:
        ok = self._primary.save_settings(settings)
        if self._json_backup:
            self._json_backup.save_settings(settings)  # sempre sincronizza
        return ok
