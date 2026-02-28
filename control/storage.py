"""
storage.py — Persistenza multi-gruppo su EEPROM AT24C512 (512kbit = 64KB)

Mappa EEPROM (pagine da 128 byte, indirizzo I2C 0x50):

  Offset  Size  Magic   Gruppo
  0x0000  128B  0xFE02  Numero di serie (binario, compatibile)
  0x0080  256B  0xFE10  TX / Audio  (gain, volumi, tx_freq, tx_gain, enfasi, mute, PI, PTY, AF1)
  0x0180  256B  0xFE11  Compressore (comp_en, thr, ratio, knee, atk, rel, mu, lim)
  0x0280  256B  0xFE12  RDS config  (rt_mode, rt_alt_sec, ps_long, ps_cycle_sec,
                                     radio_name, icecast_url, icecast_interval_sec)
  0x0380  256B  0xFE13  RDS testi   (rt_fixed 64B, rt_alt 64B — separati per dimensione)
  0x0480  256B  0xFE14  Power / PID (power_target_w, kp, ki, kd, soglie allarmi)

Formato header blocco (7 byte):
  [0-1]  magic    uint16 big-endian
  [2]    version  uint8  (attualmente 1)
  [3-4]  length   uint16 big-endian  → lunghezza payload JSON
  [5-6]  crc16    uint16 big-endian  → CRC16-CCITT del solo payload

Formato SN (binario, compatibile con versioni precedenti):
  [0-1]  magic 0xFE02  uint16 big-endian
  [2-17] SN string      16 byte, null-padded ASCII
  [18-19]CRC16 dei byte 2-17

Dev fallback: JsonStorage (attivato se smbus2 non disponibile o USE_JSON=1).
"""

import json
import logging
import os
import struct
import time

log = logging.getLogger("fmweb.storage")

# ── Costanti I2C ──────────────────────────────────────────────
I2C_BUS    = 1
ADDR_EEPROM = 0x50   # AT24C512
PAGE_SIZE   = 128    # byte per pagina di scrittura AT24C512

# ── Mappa gruppi ─────────────────────────────────────────────
GROUPS = {
    #  nome          offset   size   magic
    "sn":          (0x0000,  128,   0xFE02),  # numero di serie (binario)
    "tx_audio":    (0x0080,  256,   0xFE10),
    "compressor":  (0x0180,  256,   0xFE11),
    "rds_cfg":     (0x0280,  256,   0xFE12),
    "rds_text":    (0x0380,  256,   0xFE13),
    "power_pid":   (0x0480,  256,   0xFE14),
}

HEADER_SIZE  = 7   # magic(2) + version(1) + len(2) + crc16(2)
GROUP_VER    = 1   # versione formato corrente

# ── Dev fallback ──────────────────────────────────────────────
DEFAULT_JSON_PATH = os.environ.get(
    "FMMOD_JSON_STORAGE",
    os.path.join(os.path.expanduser("~"), ".fmmod", "storage.json")
)


# ─────────────────────────────────────────────────────────────
# Utilità
# ─────────────────────────────────────────────────────────────

def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def _is_raspberry_pi() -> bool:
    if os.environ.get("USE_JSON", "0") == "1":
        return False
    try:
        with open("/proc/cpuinfo") as f:
            info = f.read()
        if "Raspberry Pi" in info or "BCM" in info:
            import smbus2  # noqa
            return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────
# Backend EEPROM (AT24C512) — produzione
# ─────────────────────────────────────────────────────────────

class EepromStorage:
    """
    Storage multi-gruppo su AT24C512.
    Usa smbus2.i2c_rdwr per accesso affidabile con indirizzi a 2 byte
    e scritture page-aligned (128B).
    """

    def __init__(self):
        from smbus2 import SMBus, i2c_msg
        self._SMBus = SMBus
        self._i2c_msg = i2c_msg
        self._bus = SMBus(I2C_BUS)
        log.info("EepromStorage: AT24C512 @ I2C 0x%02X bus %d", ADDR_EEPROM, I2C_BUS)

    # ── I/O di basso livello ──────────────────────────────────

    def _read_bytes(self, offset: int, length: int) -> bytes:
        """Legge `length` byte da `offset`. Lettura sequenziale senza limite di pagina."""
        addr_msg = self._i2c_msg.write(ADDR_EEPROM, [(offset >> 8) & 0xFF, offset & 0xFF])
        read_msg = self._i2c_msg.read(ADDR_EEPROM, length)
        self._bus.i2c_rdwr(addr_msg, read_msg)
        return bytes(read_msg)

    def _write_bytes(self, offset: int, data: bytes):
        """
        Scrive `data` a partire da `offset`, rispettando i confini di pagina
        da 128 byte dell'AT24C512. Attesa 5 ms dopo ogni ciclo di scrittura.
        """
        pos = 0
        while pos < len(data):
            # Quanti byte possiamo scrivere senza superare il confine di pagina?
            page_start = (offset + pos) & ~(PAGE_SIZE - 1)
            page_end   = page_start + PAGE_SIZE
            avail      = page_end - (offset + pos)
            chunk      = data[pos:pos + avail]
            addr       = offset + pos
            msg = self._i2c_msg.write(
                ADDR_EEPROM,
                [(addr >> 8) & 0xFF, addr & 0xFF] + list(chunk)
            )
            self._bus.i2c_rdwr(msg)
            time.sleep(0.005)          # AT24C512: max tWR = 5 ms
            pos += len(chunk)

    # ── Numero di serie (formato binario fisso) ───────────────

    def read_sn(self) -> str | None:
        offset, size, magic_expected = GROUPS["sn"]
        try:
            raw = self._read_bytes(offset, 20)
            magic = struct.unpack_from(">H", raw, 0)[0]
            if magic != magic_expected:
                return None
            sn_bytes = raw[2:18]
            crc_ok   = struct.unpack_from(">H", raw, 18)[0]
            if _crc16(sn_bytes) != crc_ok:
                log.warning("EepromStorage.read_sn: CRC errato")
                return None
            return sn_bytes.rstrip(b'\x00').decode('ascii')
        except Exception as e:
            log.warning("EepromStorage.read_sn: %s", e)
            return None

    def write_sn(self, sn: str) -> bool:
        offset, size, magic_expected = GROUPS["sn"]
        try:
            sn_bytes = sn.encode('ascii').ljust(16, b'\x00')[:16]
            crc  = _crc16(sn_bytes)
            block = (struct.pack(">H", magic_expected)
                     + sn_bytes
                     + struct.pack(">H", crc))
            block = block.ljust(size, b'\xff')
            self._write_bytes(offset, block)
            log.info("EepromStorage.write_sn: '%s'", sn)
            return True
        except Exception as e:
            log.error("EepromStorage.write_sn: %s", e)
            return False

    # ── Gruppi JSON ───────────────────────────────────────────

    def save_group(self, name: str, data: dict) -> bool:
        if name not in GROUPS:
            log.error("save_group: gruppo '%s' sconosciuto", name)
            return False
        offset, size, magic = GROUPS[name]
        max_payload = size - HEADER_SIZE
        try:
            payload = json.dumps(data, separators=(',', ':')).encode('utf-8')
            if len(payload) > max_payload:
                log.error("save_group '%s': payload %d B > max %d B",
                          name, len(payload), max_payload)
                return False
            header = (struct.pack(">H", magic)
                      + struct.pack(">B", GROUP_VER)
                      + struct.pack(">H", len(payload))
                      + struct.pack(">H", _crc16(payload)))
            block = header + payload
            block = block.ljust(size, b'\xff')
            self._write_bytes(offset, block)
            log.info("EepromStorage.save_group '%s': %d B payload", name, len(payload))
            return True
        except Exception as e:
            log.error("EepromStorage.save_group '%s': %s", name, e)
            return False

    def load_group(self, name: str) -> dict | None:
        if name not in GROUPS:
            log.error("load_group: gruppo '%s' sconosciuto", name)
            return None
        offset, size, magic_expected = GROUPS[name]
        try:
            raw   = self._read_bytes(offset, size)
            magic = struct.unpack_from(">H", raw, 0)[0]
            if magic != magic_expected:
                log.debug("load_group '%s': magic 0x%04X ≠ 0x%04X (non inizializzato?)",
                          name, magic, magic_expected)
                return None
            # version = raw[2]  (non usato per ora, pronto per future versioni)
            length  = struct.unpack_from(">H", raw, 3)[0]
            crc_stored = struct.unpack_from(">H", raw, 5)[0]
            if length == 0 or length > size - HEADER_SIZE:
                log.warning("load_group '%s': length %d non valido", name, length)
                return None
            payload = raw[HEADER_SIZE:HEADER_SIZE + length]
            if _crc16(payload) != crc_stored:
                log.warning("load_group '%s': CRC errato", name)
                return None
            return json.loads(payload.decode('utf-8'))
        except Exception as e:
            log.warning("load_group '%s': %s", name, e)
            return None

    @property
    def backend_name(self) -> str:
        return "EEPROM"


# ─────────────────────────────────────────────────────────────
# Backend JSON — solo sviluppo / non-RPi
# ─────────────────────────────────────────────────────────────

class JsonStorage:
    """
    Fallback per sviluppo quando smbus2 non è disponibile o USE_JSON=1.
    Memorizza tutti i gruppi come chiavi separate in un unico file JSON.
    NON usare in produzione.
    """

    def __init__(self, path: str = DEFAULT_JSON_PATH):
        self._path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        log.warning("JsonStorage attivo — solo sviluppo, non usare in produzione! → %s", path)

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    return json.load(f)
            except Exception as e:
                log.warning("JsonStorage._load: %s", e)
        return {}

    def _save(self, data: dict) -> bool:
        try:
            tmp = self._path + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._path)
            return True
        except Exception as e:
            log.error("JsonStorage._save: %s", e)
            return False

    def read_sn(self) -> str | None:
        return self._load().get("sn")

    def write_sn(self, sn: str) -> bool:
        data = self._load()
        data["sn"] = sn
        return self._save(data)

    def save_group(self, name: str, payload: dict) -> bool:
        data = self._load()
        data[name] = payload
        return self._save(data)

    def load_group(self, name: str) -> dict | None:
        return self._load().get(name)

    @property
    def backend_name(self) -> str:
        return "JSON"


# ─────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────

def create_storage(json_path: str = DEFAULT_JSON_PATH):
    """
    Restituisce EepromStorage su Raspberry Pi con smbus2,
    JsonStorage altrove (o se USE_JSON=1).
    """
    if _is_raspberry_pi():
        try:
            return EepromStorage()
        except Exception as e:
            log.warning("EepromStorage non disponibile (%s) → JsonStorage", e)
    return JsonStorage(json_path)


# Alias pubblico per compatibilità con server.py
def Storage(json_path: str = DEFAULT_JSON_PATH):
    return create_storage(json_path)
