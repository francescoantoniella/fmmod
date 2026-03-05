#!/bin/bash
# import.sh — Importa files.zip o file singoli da ~/Scaricati nel progetto
# Uso: ./import.sh [--deploy] [--web-only] [--skip-build]
set -eE
trap 'err "Errore alla riga $LINENO"' ERR

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${BOLD}==> $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}! $1${NC}"; }
err()  { echo -e "${RED}✗ $1${NC}"; }

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
SEARCH_DIRS=("$HOME/Scaricati" "$HOME/Downloads" "$PROJECT_DIR")

# Mappa filename → percorso relativo nel progetto
# Aggiungi qui nuovi file quando necessario
declare -A FILE_MAP=(
  ["audio_pipeline.cpp"]="audio_pipeline.cpp"
  ["control_udp.cpp"]="control_udp.cpp"
  ["globals.hpp"]="globals.hpp"
  ["mpx_modulator.hpp"]="mpx_modulator.hpp"
  ["upsampler.hpp"]="upsampler.hpp"
  ["main.cpp"]="main.cpp"
  ["pluto_output.cpp"]="pluto_output.cpp"
  ["CMakeLists.txt"]="CMakeLists.txt"
  ["index.html"]="control/index.html"
  ["server.py"]="control/server.py"
  ["chain_manager.py"]="control/chain_manager.py"
  ["storage.py"]="control/storage.py"
  ["peak_tagger.py"]="peak_tagger.py"
  ["rds_rx_peaks.py"]="rds_rx_peaks.py"
  ["rds_rx.py"]="rds_rx.py"
  ["style.css"]="control/style.css"
  ["app.js"]="control/app.js"
  ["fonts.css"]="control/fonts.css"
  ["theme-light.css"]="control/theme-light.css"
  ["theme-dark.css"]="control/theme-dark.css"
  ["theme-neon.css"]="control/theme-neon.css"
)

# Opzioni
DO_DEPLOY=0
DEPLOY_OPTS=""
for arg in "$@"; do
  case $arg in
    --deploy)     DO_DEPLOY=1 ;;
    --web-only)   DO_DEPLOY=1; DEPLOY_OPTS="--web-only" ;;
    --skip-build) DO_DEPLOY=1; DEPLOY_OPTS="--skip-build" ;;
  esac
done

# Funzione: importa una lista di file sorgente, poi li cancella
import_files() {
  local -n srcs=$1
  for src in "${srcs[@]}"; do
    local filename
    filename=$(basename "$src")
    if [[ -n "${FILE_MAP[$filename]}" ]]; then
      local dest="$PROJECT_DIR/${FILE_MAP[$filename]}"
      cp "$src" "$dest"
      ok "$filename → ${FILE_MAP[$filename]}"
      rm -f "$src"
      IMPORTED=$((IMPORTED + 1))
    else
      warn "$filename: non in FILE_MAP"
      # Menu cartelle disponibili
      local FOLDERS=("." "control" "librds" "drivers" "rpi_setup" "doc" "[salta]")
      echo "  In quale cartella va?"
      local i=1
      for f in "${FOLDERS[@]}"; do echo "  $i) $f"; i=$((i + 1)); done
      local choice
      read -r choice </dev/tty
      if [[ -z "$choice" || "$choice" == "${#FOLDERS[@]}" ]]; then
        warn "$filename saltato"
        SKIPPED=$((SKIPPED + 1))
      else
        local folder="${FOLDERS[$((choice-1))]}"
        local dest_rel
        if [[ "$folder" == "." ]]; then
          dest_rel="$filename"
        else
          dest_rel="$folder/$filename"
        fi
        local dest="$PROJECT_DIR/$dest_rel"
        mkdir -p "$(dirname "$dest")"
        cp "$src" "$dest"
        ok "$filename → $dest_rel"
        rm -f "$src"
        IMPORTED=$((IMPORTED + 1))
        # Aggiorna FILE_MAP nello script per le prossime volte
        { grep -q "\"$filename\"" "$PROJECT_DIR/import.sh"; } 2>/dev/null || \
          sed -i "/^  \[\"storage.py\"\]/a\\  [\"$filename\"]=\"$dest_rel\"" "$PROJECT_DIR/import.sh"
        ok "FILE_MAP aggiornata in import.sh"
      fi
    fi
  done
}

IMPORTED=0
SKIPPED=0
TMP=""

# 1. Cerca files.zip
ZIP_PATH=""
for dir in "${SEARCH_DIRS[@]}"; do
  if [[ -f "$dir/files.zip" ]]; then
    ZIP_PATH="$dir/files.zip"
    break
  fi
done

if [[ -n "$ZIP_PATH" ]]; then
  log "Importo da zip: $ZIP_PATH"
  TMP=$(mktemp -d)
  trap "rm -rf $TMP" EXIT
  unzip -q "$ZIP_PATH" -d "$TMP"
  rm -f "$ZIP_PATH"
  ok "files.zip eliminato"

  mapfile -t zip_files < <(find "$TMP" -maxdepth 2 -type f)
  import_files zip_files
fi

# 2. Cerca file singoli noti in ~/Scaricati e ~/Downloads
for dir in "$HOME/Scaricati" "$HOME/Downloads"; do
  [[ -d "$dir" ]] || continue
  single_files=()
  for filename in "${!FILE_MAP[@]}"; do
    if [[ -f "$dir/$filename" ]]; then
      single_files+=("$dir/$filename")
    fi
  done
  if [[ ${#single_files[@]} -gt 0 ]]; then
    log "File singoli trovati in $dir"
    import_files single_files
  fi
done

echo ""
if [[ $IMPORTED -eq 0 && $SKIPPED -eq 0 ]]; then
  warn "Nessun file trovato in ~/Scaricati, ~/Downloads o dir progetto"
  exit 0
fi

log "Importati: $IMPORTED file, saltati: $SKIPPED"

# Deploy opzionale
if [[ $DO_DEPLOY -eq 1 && $IMPORTED -gt 0 ]]; then
  echo ""
  "$PROJECT_DIR/deploy.sh" $DEPLOY_OPTS
elif [[ $IMPORTED -gt 0 ]]; then
  echo ""
  echo "Per deployare: ./deploy.sh  oppure  ./deploy.sh --web-only"
fi
