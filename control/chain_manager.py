"""
chain_manager.py — Gestione della catena audio/webradio

Modalità sorgente (audio_source):
  webradio  ffmpeg HTTP → modulatore → flowgraph GNURadio
  alsa1     arecord hw:0,0 (scheda 1) → modulatore → flowgraph
  alsa2     arecord hw:1,0 (scheda 2) → modulatore → flowgraph
  tone      ffmpeg sine 1kHz → modulatore → flowgraph  (test)
  mpx_in    arecord hw:1,0 a 192kHz → flowgraph MPX diretto
            (bypass modulatore: il segnale è già composito stereo+RDS)

Pipeline standard (webradio / alsa1 / alsa2 / tone):
    [sorgente PCM s16le 48kHz]  ──pipe──►  modulatore (FM-IQ)  ──pipe──►  flowgraph

Pipeline MPX-in:
    [arecord 192kHz float32]  ──pipe──►  flowgraph MPX (nessun modulatore)
"""
import subprocess
import threading
import time
import logging
from collections import deque
from pathlib import Path

log = logging.getLogger("chain")

AUDIO_SOURCES = ("webradio", "alsa1", "alsa2", "tone", "mpx_in")


class ChainManager:
    DEFAULT_CFG = {
        # ── Sorgente audio ─────────────────────────────────────────────
        "audio_source":    "webradio",   # webradio | alsa1 | alsa2 | tone | mpx_in

        # ── WebRadio ───────────────────────────────────────────────────
        "stream_url":      "http://nr9.newradio.it:9371/stream",
        "ffmpeg_extra":    [],           # args aggiuntivi a ffmpeg (es. filtri)

        # ── ALSA ───────────────────────────────────────────────────────
        "alsa_dev1":       "hw:0,0",     # scheda audio 1 (PCM 48kHz stereo)
        "alsa_dev2":       "hw:1,0",     # scheda audio 2 (PCM 48kHz stereo o MPX 192kHz)
        "alsa_rate":       48000,        # sample rate ALSA per modalità PCM
        "alsa_channels":   2,            # canali ALSA per modalità PCM

        # ── Tono di test ───────────────────────────────────────────────
        "tone_freq":       1000,         # Hz del tono sinusoidale
        "tone_amplitude":  0.5,          # ampiezza 0.0–1.0 (0 dBFS = 1.0)
        "tone_left_only":  False,        # True = tono solo canale L (test separazione)

        # ── MPX in (da scheda audio 2) ─────────────────────────────────
        "mpx_rate":        192000,       # sample rate per cattura MPX (Hz)
        "mpx_flowgraph":   "flowgraphs/mpx_in.py",  # flowgraph GNURadio MPX-in

        # ── Modulatore ─────────────────────────────────────────────────
        "modulatore_bin":  "build/modulatore",
        "modulatore_args": ["--no-pluto", "--stdin", "--fm-iq"],

        # ── Flowgraph standard ─────────────────────────────────────────
        "flowgraph":       "flowgraphs/rds_rx.py",

        # ── Watchdog ───────────────────────────────────────────────────
        "auto_restart":    False,
        "restart_delay":   5.0,
        "log_maxlines":    300,
    }

    def __init__(self, base_dir: str):
        self._base = Path(base_dir).resolve()
        self.cfg   = dict(self.DEFAULT_CFG)

        self._procs: list[subprocess.Popen] = []
        self._lock        = threading.Lock()
        self._status      = "stopped"
        self._last_error  = ""
        self._start_time  = None
        self._wd_running  = False
        self._log_buf: deque[str] = deque(maxlen=self.cfg["log_maxlines"])

    # ─────────────────────────────────────────────
    # API pubblica
    # ─────────────────────────────────────────────

    def start(self) -> dict:
        with self._lock:
            if self._status in ("starting", "running"):
                return {"ok": False, "error": "già in esecuzione"}
            self._status = "starting"
            self._last_error = ""
        try:
            self._launch()
            return {"ok": True}
        except Exception as e:
            with self._lock:
                self._status = "error"
                self._last_error = str(e)
            log.error("[chain] start fallito: %s", e)
            return {"ok": False, "error": str(e)}

    def stop(self) -> dict:
        with self._lock:
            self._wd_running = False
            self._status = "stopping"
        self._kill_all()
        with self._lock:
            self._status = "stopped"
            self._start_time = None
        self._log_line("[chain] fermato")
        return {"ok": True}

    def restart(self) -> dict:
        self.stop()
        time.sleep(0.3)
        return self.start()

    def status(self) -> dict:
        with self._lock:
            alive  = sum(1 for p in self._procs if p.poll() is None)
            uptime = int(time.time() - self._start_time) if self._start_time else 0
            return {
                "status":      self._status,
                "error":       self._last_error,
                "procs_alive": alive,
                "procs_total": len(self._procs),
                "uptime_sec":  uptime,
                "cfg":         dict(self.cfg),
                "log":         list(self._log_buf)[-50:],
            }

    def update_cfg(self, data: dict) -> dict:
        allowed = set(self.DEFAULT_CFG.keys())
        with self._lock:
            for k, v in data.items():
                if k in allowed:
                    self.cfg[k] = v
        return {"ok": True}

    # ─────────────────────────────────────────────
    # Costruzione comandi per sorgente
    # ─────────────────────────────────────────────

    def _source_cmd(self) -> list[str]:
        """Restituisce il comando per la sorgente audio (output: PCM s16le 48kHz stereo su stdout)."""
        src   = self.cfg["audio_source"]
        extra = list(self.cfg.get("ffmpeg_extra", []))
        rate  = int(self.cfg["alsa_rate"])
        ch    = int(self.cfg["alsa_channels"])

        if src == "webradio":
            return [
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-re", "-i", self.cfg["stream_url"],
            ] + extra + [
                "-f", "s16le", "-ac", "2", "-ar", "48000", "-"
            ]

        if src == "alsa1":
            dev = self.cfg["alsa_dev1"]
            return [
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-f", "alsa", "-ar", str(rate), "-ac", str(ch), "-i", dev,
            ] + extra + [
                "-f", "s16le", "-ac", "2", "-ar", "48000", "-"
            ]

        if src == "alsa2":
            dev = self.cfg["alsa_dev2"]
            return [
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-f", "alsa", "-ar", str(rate), "-ac", str(ch), "-i", dev,
            ] + extra + [
                "-f", "s16le", "-ac", "2", "-ar", "48000", "-"
            ]

        if src == "tone":
            freq  = int(self.cfg["tone_freq"])
            amp   = max(0.0, min(1.0, float(self.cfg.get("tone_amplitude", 0.5))))
            left_only = self.cfg.get("tone_left_only", False)
            # Genera tono L+R o solo L (per test separazione stereo)
            if left_only:
                lavfi = (f"sine=frequency={freq}:sample_rate=48000:amplitude={amp:.4f},"
                         f"aformat=channel_layouts=stereo,pan=stereo|c0=c0|c1=0*c0")
            else:
                lavfi = f"sine=frequency={freq}:sample_rate=48000:amplitude={amp:.4f}"
            return [
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-f", "lavfi", "-i", lavfi,
                "-f", "s16le", "-ac", "2", "-ar", "48000", "-"
            ]

        raise ValueError(f"audio_source non gestito da _source_cmd: {src!r}")

    def _mpx_source_cmd(self) -> list[str]:
        """Comando cattura MPX da scheda audio (float32 mono/stereo a alta freq)."""
        dev  = self.cfg["alsa_dev2"]
        rate = int(self.cfg["mpx_rate"])
        # Cattura come float32 mono (il MPX composito è mono)
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-f", "alsa", "-ar", str(rate), "-ac", "1", "-i", dev,
            "-f", "f32le", "-ar", str(rate), "-ac", "1", "-"
        ]

    # ─────────────────────────────────────────────
    # Avvio pipeline
    # ─────────────────────────────────────────────

    def _launch(self):
        self._kill_all()
        src = self.cfg["audio_source"]

        if src == "mpx_in":
            self._launch_mpx_in()
        else:
            self._launch_standard()

    def _launch_standard(self):
        """Pipeline: [sorgente PCM] → modulatore → flowgraph."""
        src_cmd  = self._source_cmd()
        mod_cmd  = [str(self._base / self.cfg["modulatore_bin"])] + list(self.cfg["modulatore_args"])
        flow_cmd = ["python3", str(self._base / self.cfg["flowgraph"])]

        for cmd, lbl in zip((src_cmd, mod_cmd, flow_cmd), ("source", "modulatore", "flowgraph")):
            self._log_line(f"[chain:{lbl}] $ " + " ".join(str(a) for a in cmd))

        source = subprocess.Popen(
            src_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self._base),
        )
        modulatore = subprocess.Popen(
            mod_cmd,
            stdin=source.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self._base),
        )
        source.stdout.close()

        flowgraph = subprocess.Popen(
            flow_cmd,
            stdin=modulatore.stdout,
            stderr=subprocess.PIPE,
            cwd=str(self._base),
        )
        modulatore.stdout.close()

        self._procs = [source, modulatore, flowgraph]
        self._start_procs(("source", "modulatore", "flowgraph"))

    def _launch_mpx_in(self):
        """Pipeline MPX: [arecord 192kHz] → flowgraph MPX-in (bypass modulatore)."""
        src_cmd  = self._mpx_source_cmd()
        flow_cmd = ["python3", str(self._base / self.cfg["mpx_flowgraph"])]

        for cmd, lbl in zip((src_cmd, flow_cmd), ("mpx-capture", "mpx-flowgraph")):
            self._log_line(f"[chain:{lbl}] $ " + " ".join(str(a) for a in cmd))

        source = subprocess.Popen(
            src_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self._base),
        )
        flowgraph = subprocess.Popen(
            flow_cmd,
            stdin=source.stdout,
            stderr=subprocess.PIPE,
            cwd=str(self._base),
        )
        source.stdout.close()

        self._procs = [source, flowgraph]
        self._start_procs(("mpx-capture", "mpx-flowgraph"))

    def _start_procs(self, names: tuple):
        for proc, name in zip(self._procs, names):
            threading.Thread(
                target=self._drain_stderr,
                args=(proc, name),
                daemon=True,
            ).start()

        with self._lock:
            self._status     = "running"
            self._start_time = time.time()
            self._wd_running = True

        self._log_line(f"[chain] avviato — PID {[p.pid for p in self._procs]}")
        threading.Thread(target=self._watchdog_loop, daemon=True).start()

    # ─────────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────────

    def _kill_all(self):
        for p in self._procs:
            try:
                p.terminate()
                p.wait(timeout=4)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self._procs = []

    def _watchdog_loop(self):
        while True:
            time.sleep(2)
            with self._lock:
                if not self._wd_running:
                    break
                dead = [p for p in self._procs if p.poll() is not None]
                if not dead:
                    continue
                codes = {p.pid: p.returncode for p in dead}
                msg   = f"processo terminato (rc={codes})"
                self._status     = "error"
                self._last_error = msg
                do_restart = self.cfg["auto_restart"]
                delay      = float(self.cfg["restart_delay"])

            self._log_line(f"[watchdog] {msg}")

            if do_restart:
                self._log_line(f"[watchdog] riavvio tra {delay:.1f}s …")
                time.sleep(delay)
                with self._lock:
                    if not self._wd_running:
                        break
                try:
                    self._launch()
                except Exception as e:
                    with self._lock:
                        self._status     = "error"
                        self._last_error = str(e)
                break
            else:
                self._kill_all()
                break

    def _drain_stderr(self, proc: subprocess.Popen, name: str):
        try:
            for raw in proc.stderr:
                line = raw.decode(errors="replace").rstrip()
                if line:
                    self._log_line(f"[{name}] {line}")
        except Exception:
            pass

    def _log_line(self, text: str):
        ts    = time.strftime("%H:%M:%S")
        entry = f"{ts}  {text}"
        log.info(entry)
        self._log_buf.append(entry)
