#!/bin/bash
# deploy.sh — Sync, build e restart su Raspberry Pi
set -e

RPI=rfe@192.168.76.103
REMOTE_DIR=/home/rfe/modulatore_2.0
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log()  { echo -e "${BOLD}==> $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}! $1${NC}"; }

# Opzioni
SKIP_BUILD=0
SKIP_RESTART=0
ONLY_WEB=0
for arg in "$@"; do
  case $arg in
    --skip-build)   SKIP_BUILD=1 ;;
    --skip-restart) SKIP_RESTART=1 ;;
    --web-only)     ONLY_WEB=1 ;;
  esac
done

# 1. Sync files
log "Sync → $RPI:$REMOTE_DIR"
rsync -az --delete \
  --exclude='.git' \
  --exclude='build' \
  --exclude='__pycache__' \
  --exclude='*.iq' --exclude='*.mpx' --exclude='*.raw' \
  --exclude='codebase.tgz' --exclude='files.zip' \
  --exclude='out.*' \
  --exclude='*.swp' \
  --exclude='"audio_pipeline (1).cpp"' \
  . "$RPI:$REMOTE_DIR/"
ok "Sync completato"

# 2. Build C++ sul Pi (opzionale)
if [[ $SKIP_BUILD -eq 0 && $ONLY_WEB -eq 0 ]]; then
  log "Build C++ su Raspberry Pi..."
  ssh "$RPI" "cd $REMOTE_DIR && mkdir -p build && cd build && cmake -DCMAKE_BUILD_TYPE=Release .. -q && make -j4 2>&1 | tail -5"
  ok "Build completata"
fi

# 3. Restart servizi
if [[ $SKIP_RESTART -eq 0 ]]; then
  log "Restart servizi..."
  if [[ $ONLY_WEB -eq 0 ]]; then
    ssh "$RPI" "sudo systemctl restart fmmod && sleep 1 && sudo systemctl restart fmweb"
    ok "fmmod + fmweb riavviati"
  else
    ssh "$RPI" "sudo systemctl restart fmweb"
    ok "fmweb riavviato"
  fi

  # Mostra stato
  echo ""
  ssh "$RPI" "systemctl is-active fmmod fmweb 2>/dev/null | paste - - <(echo -e 'fmmod\nfmweb')" 2>/dev/null || \
    ssh "$RPI" "systemctl is-active fmmod; systemctl is-active fmweb"
fi

echo ""
ok "Deploy completato → http://192.168.76.103:8080"
