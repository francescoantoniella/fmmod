#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# setup.sh — Installazione completa FM Processor su Raspberry Pi
# Eseguire come root: sudo bash setup.sh
# ═══════════════════════════════════════════════════════════════
set -e

# ── Colori per output ─────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
info() { echo -e "${BLUE}  → $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $1${NC}"; }
err()  { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║      FM PROCESSOR — Setup Raspberry Pi    ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Verifica root ─────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Eseguire come root: sudo bash setup.sh"

# ── Configurazione — modifica qui ────────────────────────────
WIFI_SSID="FM-PROCESSOR"
WIFI_PASSWORD="fmprocessor2024"
WIFI_CHANNEL="6"
AP_IP="192.168.73.1"
INSTALL_DIR="/opt/fmmod"
WEB_PORT="8080"
STREAM_URL="http://nr9.newradio.it:9371/stream"
TX_FREQ="100.0"
TX_GAIN="-17"

# ── 1. Aggiorna sistema ───────────────────────────────────────
info "Aggiornamento pacchetti..."
apt-get update -qq
ok "Aggiornamento completato"

# ── 2. Installa dipendenze ────────────────────────────────────
info "Installazione dipendenze..."
apt-get install -y -qq \
    hostapd dnsmasq \
    ffmpeg \
    python3-pip \
    cmake build-essential \
    libi2c-dev i2c-tools \
    || err "Installazione pacchetti fallita"

pip3 install bottle smbus2 --break-system-packages -q
ok "Dipendenze installate"

# ── 3. Abilita I2C ────────────────────────────────────────────
info "Abilitazione I2C..."
if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null; then
    echo "dtparam=i2c_arm=on" >> /boot/config.txt
    ok "I2C abilitato in /boot/config.txt (richiede reboot)"
else
    ok "I2C già abilitato"
fi
modprobe i2c-dev 2>/dev/null || true

# ── 4. Directory installazione ────────────────────────────────
info "Preparazione directory $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/control"
mkdir -p "$INSTALL_DIR/build"
mkdir -p /tmp/fmmod_logs
chown -R pi:pi "$INSTALL_DIR"
chown pi:pi /tmp/fmmod_logs
ok "Directory pronta"

# ── 5. IP statico su wlan0 ────────────────────────────────────
info "Configurazione IP statico wlan0 ($AP_IP)..."
if ! grep -q "interface wlan0" /etc/dhcpcd.conf; then
    cat >> /etc/dhcpcd.conf << DHCPCD

interface wlan0
    static ip_address=${AP_IP}/24
    nohook wpa_supplicant
DHCPCD
    ok "IP statico aggiunto a dhcpcd.conf"
else
    warn "wlan0 già configurato in dhcpcd.conf — verifica manualmente"
fi

# ── 6. hostapd ────────────────────────────────────────────────
info "Configurazione hostapd (SSID: $WIFI_SSID)..."
cat > /etc/hostapd/hostapd.conf << HOSTAPD
interface=wlan0
driver=nl80211
ssid=${WIFI_SSID}
channel=${WIFI_CHANNEL}
hw_mode=g
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase=${WIFI_PASSWORD}
ignore_broadcast_ssid=0
wmm_enabled=0
macaddr_acl=0
HOSTAPD

# Punta hostapd al file di config
sed -i 's|#DAEMON_CONF=""|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' \
    /etc/default/hostapd 2>/dev/null || true
echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' > /etc/default/hostapd
ok "hostapd configurato"

# ── 7. dnsmasq ────────────────────────────────────────────────
info "Configurazione dnsmasq (DHCP + DNS)..."
# Backup configurazione originale
[ -f /etc/dnsmasq.conf ] && cp /etc/dnsmasq.conf /etc/dnsmasq.conf.bak
cat > /etc/dnsmasq.conf << DNSMASQ
interface=wlan0
no-resolv
dhcp-range=192.168.73.10,192.168.73.50,24h
dhcp-option=3,${AP_IP}
address=/#/${AP_IP}
address=/fmproc.local/${AP_IP}
address=/fmproc/${AP_IP}
DNSMASQ
ok "dnsmasq configurato"

# ── 8. Servizio fmweb ─────────────────────────────────────────
info "Installazione servizio fmweb..."
cat > /etc/systemd/system/fmweb.service << SERVICE
[Unit]
Description=FM Processor — Web Interface
After=network.target fmmod.service
Wants=fmmod.service

[Service]
Type=simple
User=pi
WorkingDirectory=${INSTALL_DIR}/control
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/control/server.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE
ok "fmweb.service installato"

# ── 9. Servizio fmmod ─────────────────────────────────────────
info "Installazione servizio fmmod..."
cat > /etc/systemd/system/fmmod.service << SERVICE
[Unit]
Description=FM Processor — Modulatore C++
After=sound.target

[Service]
Type=simple
User=pi
WorkingDirectory=${INSTALL_DIR}
ExecStart=/bin/bash -c 'ffmpeg -hide_banner -loglevel error -re -i "${STREAM_URL}" -f s16le -ac 2 -ar 48000 - | ${INSTALL_DIR}/build/modulatore --stdin --tx-freq=${TX_FREQ} --tx-gain=${TX_GAIN}'
Restart=always
RestartSec=5
CPUSchedulingPolicy=fifo
CPUSchedulingPriority=50
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE
ok "fmmod.service installato"

# ── 10. Abilita e avvia servizi ───────────────────────────────
info "Abilitazione servizi al boot..."
systemctl daemon-reload
systemctl unmask hostapd 2>/dev/null || true
systemctl enable hostapd
systemctl enable dnsmasq
systemctl enable fmweb
systemctl enable fmmod
ok "Servizi abilitati"

# ── 11. Avvio immediato (solo AP e web, non fmmod senza build) ─
info "Avvio access point e web server..."
systemctl restart dhcpcd    2>/dev/null || true
systemctl restart hostapd   2>/dev/null || warn "hostapd non partito — richiede reboot"
systemctl restart dnsmasq   2>/dev/null || warn "dnsmasq non partito"
systemctl restart fmweb     2>/dev/null || warn "fmweb non partito — controlla che server.py sia in $INSTALL_DIR/control/"

# ── Riepilogo ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           Setup completato!               ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  WiFi SSID  : ${YELLOW}${WIFI_SSID}${NC}"
echo -e "  Password   : ${YELLOW}${WIFI_PASSWORD}${NC}"
echo -e "  IP RPi     : ${YELLOW}${AP_IP}${NC}"
echo -e "  Interfaccia: ${YELLOW}http://${AP_IP}:${WEB_PORT}${NC}"
echo -e "  Alias      : ${YELLOW}http://fmproc.local:${WEB_PORT}${NC}"
echo ""
echo -e "  Prossimi passi:"
echo -e "  1. Copia il codebase in ${YELLOW}${INSTALL_DIR}/${NC}"
echo -e "  2. Compila: ${YELLOW}cd ${INSTALL_DIR} && mkdir build && cd build && cmake .. && make -j4${NC}"
echo -e "  3. Copia web: ${YELLOW}cp index.html ${INSTALL_DIR}/control/${NC}"
echo -e "  4. ${YELLOW}sudo reboot${NC}"
echo ""
warn "È necessario un reboot per attivare tutte le modifiche"
echo ""
