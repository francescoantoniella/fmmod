#!/usr/bin/env python3
"""
serialize.py — Tool di serializzazione in fabbrica
Uso: sudo python3 serialize.py --sn FM2024001

Scrive il SN in EEPROM, rigenera hostapd.conf con la password
derivata dal SN, e aggiorna il web server.

Struttura EEPROM (AT24C32, indirizzo 0x50):
  [0x00-0x01]  magic = 0xFE02  (versione struttura serializzazione)
  [0x02-0x11]  SN    = 16 byte ASCII, padding con 0x00
  [0x12-0x13]  CRC16 del SN
  [0x14-0x7F]  riservato
  [0x80-...]   settings JSON (usato da server.py)
"""

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time

# ── Configurazione ────────────────────────────────────────────
I2C_BUS      = 1
ADDR_EEPROM  = 0x50
EEPROM_MAGIC = 0xFE02

SN_OFFSET    = 0x00   # inizio blocco serializzazione
SETTINGS_OFFSET = 0x80  # settings JSON (non sovrascrivere)

HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"
WIFI_SSID_PREFIX = "FM-PROC"   # SSID = FM-PROC-<SN>
INSTALL_DIR  = "/opt/fmmod"

# ── CRC16 CCITT ───────────────────────────────────────────────
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc

# ── Derivazione password dal SN ───────────────────────────────
def sn_to_password(sn: str) -> str:
    """SHA256 del SN → primi 8 caratteri hex."""
    h = hashlib.sha256(sn.encode('ascii')).hexdigest()
    return h[:8]

# ── Derivazione SSID dal SN ───────────────────────────────────
def sn_to_ssid(sn: str) -> str:
    return f"{WIFI_SSID_PREFIX}-{sn}"

# ── I2C EEPROM ────────────────────────────────────────────────
def eeprom_write(bus, addr_chip, mem_addr: int, data: bytes):
    """Scrive su EEPROM I2C con page write da 32 byte."""
    chunk = 32
    for i in range(0, len(data), chunk):
        block = list(data[i:i+chunk])
        a = mem_addr + i
        bus.write_i2c_block_data(addr_chip,
                                  (a >> 8) & 0xFF,
                                  [a & 0xFF] + block)
        time.sleep(0.010)  # write cycle time

def eeprom_read(bus, addr_chip, mem_addr: int, length: int) -> bytes:
    """Legge dalla EEPROM I2C."""
    bus.write_i2c_block_data(addr_chip,
                              (mem_addr >> 8) & 0xFF,
                              [mem_addr & 0xFF])
    time.sleep(0.005)
    return bytes(bus.read_i2c_block_data(addr_chip, 0, length))

def write_sn_to_eeprom(sn: str, dry_run: bool = False):
    """Serializza il SN in EEPROM."""
    sn_bytes = sn.encode('ascii').ljust(16, b'\x00')[:16]
    crc = crc16(sn_bytes)
    block = struct.pack(">H", EEPROM_MAGIC) + sn_bytes + struct.pack(">H", crc)
    # Padding a 0x80 byte
    block = block.ljust(0x80, b'\xff')

    print(f"  SN bytes : {sn_bytes}")
    print(f"  CRC16    : 0x{crc:04X}")
    print(f"  Blocco   : {len(block)} byte → EEPROM @ 0x{SN_OFFSET:02X}")

    if dry_run:
        print("  [DRY RUN] EEPROM non scritta")
        return

    try:
        import smbus2
        bus = smbus2.SMBus(I2C_BUS)
        write_sn_to_eeprom_bus(bus, block)
        bus.close()
        print("  ✓ EEPROM scritta")
    except ImportError:
        print("  ⚠ smbus2 non disponibile — EEPROM non scritta")
    except Exception as e:
        print(f"  ✗ Errore EEPROM: {e}")

def write_sn_to_eeprom_bus(bus, block: bytes):
    eeprom_write(bus, ADDR_EEPROM, SN_OFFSET, block)

def read_sn_from_eeprom() -> str | None:
    """Legge il SN dalla EEPROM. Ritorna None se non serializzato."""
    try:
        import smbus2
        bus = smbus2.SMBus(I2C_BUS)
        raw = eeprom_read(bus, ADDR_EEPROM, SN_OFFSET, 0x14)
        bus.close()
    except Exception as e:
        print(f"  ⚠ Lettura EEPROM fallita: {e}")
        return None

    magic = struct.unpack_from(">H", raw, 0)[0]
    if magic != EEPROM_MAGIC:
        return None

    sn_bytes = raw[2:18]
    crc_stored = struct.unpack_from(">H", raw, 18)[0]
    crc_calc = crc16(sn_bytes)

    if crc_stored != crc_calc:
        print(f"  ✗ CRC errato: atteso 0x{crc_calc:04X}, trovato 0x{crc_stored:04X}")
        return None

    return sn_bytes.rstrip(b'\x00').decode('ascii')

# ── hostapd.conf ──────────────────────────────────────────────
def update_hostapd(sn: str, dry_run: bool = False):
    """Rigenera hostapd.conf con SSID e password derivati dal SN."""
    ssid     = sn_to_ssid(sn)
    password = sn_to_password(sn)

    content = f"""# Generato automaticamente da serialize.py
# SN: {sn}
interface=wlan0
driver=nl80211
ssid={ssid}
channel=6
hw_mode=g
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase={password}
ignore_broadcast_ssid=0
wmm_enabled=0
macaddr_acl=0
"""
    print(f"  SSID     : {ssid}")
    print(f"  Password : {password}  (SHA256({sn})[:8])")

    if dry_run:
        print("  [DRY RUN] hostapd.conf non scritto")
        return

    if not os.path.exists(os.path.dirname(HOSTAPD_CONF)):
        os.makedirs(os.path.dirname(HOSTAPD_CONF), exist_ok=True)

    with open(HOSTAPD_CONF, 'w') as f:
        f.write(content)
    print(f"  ✓ {HOSTAPD_CONF} aggiornato")

    # Riavvia hostapd se in esecuzione
    try:
        subprocess.run(["systemctl", "restart", "hostapd"],
                       check=True, capture_output=True)
        print("  ✓ hostapd riavviato")
    except Exception:
        print("  ⚠ hostapd non riavviato (forse non in esecuzione)")

# ── Etichetta da stampare ─────────────────────────────────────
def print_label(sn: str):
    """Stampa le info per l'etichetta del prodotto."""
    ssid     = sn_to_ssid(sn)
    password = sn_to_password(sn)
    print()
    print("  ┌─────────────────────────────────────┐")
    print("  │         FM PROCESSOR                │")
    print(f"  │  SN       : {sn:<24} │")
    print(f"  │  WiFi     : {ssid:<24} │")
    print(f"  │  Password : {password:<24} │")
    print(f"  │  URL      : http://192.168.73.1:8080│")
    print("  └─────────────────────────────────────┘")
    print()

# ── Salva SN su file locale (backup) ─────────────────────────
def save_sn_local(sn: str):
    path = os.path.join(INSTALL_DIR, ".serial")
    try:
        with open(path, 'w') as f:
            f.write(sn)
        print(f"  ✓ SN salvato in {path}")
    except Exception as e:
        print(f"  ⚠ Salvataggio locale fallito: {e}")

def load_sn_local() -> str | None:
    """Fallback: legge SN dal file locale se EEPROM non disponibile."""
    path = os.path.join(INSTALL_DIR, ".serial")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return None

# ── Main ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="FM Processor — Tool di serializzazione",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  sudo python3 serialize.py --sn FM2024001          # Serializza
  sudo python3 serialize.py --sn FM2024001 --dry-run # Test senza scrivere
  sudo python3 serialize.py --read                   # Legge SN da EEPROM
  sudo python3 serialize.py --sn FM2024001 --label   # Solo stampa etichetta
        """
    )
    parser.add_argument('--sn',      help='Numero di serie (es. FM2024001)')
    parser.add_argument('--read',    action='store_true', help='Legge SN da EEPROM')
    parser.add_argument('--dry-run', action='store_true', help='Simula senza scrivere')
    parser.add_argument('--label',   action='store_true', help='Stampa solo etichetta')
    parser.add_argument('--force',   action='store_true', help='Sovrascrivi se già serializzato')
    args = parser.parse_args()

    # ── Lettura ───────────────────────────────────────────────
    if args.read:
        print("\n  Lettura SN da EEPROM...")
        sn = read_sn_from_eeprom()
        if sn:
            print(f"  ✓ SN trovato: {sn}")
            print_label(sn)
        else:
            # Fallback file locale
            sn = load_sn_local()
            if sn:
                print(f"  ⚠ EEPROM non disponibile, SN da file locale: {sn}")
                print_label(sn)
            else:
                print("  ✗ Nessun SN trovato (EEPROM non serializzata)")
                sys.exit(1)
        return

    if not args.sn:
        parser.print_help()
        sys.exit(1)

    sn = args.sn.strip().upper()

    # Validazione SN: lettere, numeri, trattini, max 16 char
    if not re.match(r'^[A-Z0-9\-]{1,16}$', sn):
        print(f"  ✗ SN non valido: '{sn}'")
        print("    Usa solo lettere maiuscole, numeri e trattini (max 16 char)")
        sys.exit(1)

    # ── Solo etichetta ────────────────────────────────────────
    if args.label:
        print_label(sn)
        return

    # ── Serializzazione ───────────────────────────────────────
    print(f"\n  FM Processor — Serializzazione {sn}")
    print(f"  {'─' * 40}")

    # Controlla se già serializzato
    if not args.force and not args.dry_run:
        existing = read_sn_from_eeprom()
        if existing:
            print(f"  ⚠ EEPROM già serializzata con SN: {existing}")
            print("    Usa --force per sovrascrivere")
            sys.exit(1)

    print("\n  [1/3] Scrittura EEPROM...")
    write_sn_to_eeprom(sn, dry_run=args.dry_run)

    print("\n  [2/3] Aggiornamento hostapd...")
    update_hostapd(sn, dry_run=args.dry_run)

    print("\n  [3/3] Backup locale SN...")
    if not args.dry_run:
        save_sn_local(sn)
    else:
        print("  [DRY RUN] File locale non scritto")

    print_label(sn)
    print("  Serializzazione completata.\n")

if __name__ == "__main__":
    main()
