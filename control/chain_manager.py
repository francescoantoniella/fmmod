"""
chain_manager.py — Gestione della catena webradio

Pipeline:
    ffmpeg (HTTP stream)  ──pipe──►  modulatore (FM-IQ)  ──pipe──►  flowgraph GNURadio
"""
import subprocess
import threading
import time
import logging
from collections import deque
from pathlib import Path

log = logging.getLogger("chain")


class ChainManager:
    """
    Avvia, monitora e ferma la pipeline:
        ffmpeg → modulatore → flowgraph

    Ogni processo è collegato al successivo tramite pipe Python (non shell=True),
    così si ha controllo individuale su PID e returncode.
    """

    DEFAULT_CFG = {
        "stream_url":      "http://nr9.newradio.it:9371/stream",
        "flowgraph":       "flowgraphs/rds_rx.py",         # relativo a base_dir
        "modulatore_bin":  "build/modulatore",              # relativo a base_dir
        "modulatore_args": ["--no-pluto", "--stdin", "--fm-iq"],
        "ffmpeg_extra":    [],                              # args aggiuntivi a ffmpeg
        "auto_restart":    False,
        "restart_delay":   5.0,
        "log_maxlines":    300,
    }

    def __init__(self, base_dir: str):
        self._base = Path(base_dir).resolve()
        self.cfg   = dict(self.DEFAULT_CFG)

        self._procs: list[subprocess.Popen] = []
        self._lock  = threading.Lock()
        self._status      = "stopped"   # stopped | starting | running | error | stopping
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
            log.error(f"[chain] start fallito: {e}")
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
            alive = sum(1 for p in self._procs if p.poll() is None)
            uptime = int(time.time() - self._start_time) if self._start_time else 0
            return {
                "status":       self._status,
                "error":        self._last_error,
                "procs_alive":  alive,
                "procs_total":  len(self._procs),
                "uptime_sec":   uptime,
                "cfg":          dict(self.cfg),
                "log":          list(self._log_buf)[-50:],
            }

    def update_cfg(self, data: dict) -> dict:
        allowed = set(self.DEFAULT_CFG.keys())
        with self._lock:
            for k, v in data.items():
                if k in allowed:
                    self.cfg[k] = v
        return {"ok": True}

    # ─────────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────────

    def _launch(self):
        """Avvia la tripletta di processi collegati via pipe."""
        self._kill_all()

        url      = self.cfg["stream_url"]
        mod_bin  = str(self._base / self.cfg["modulatore_bin"])
        mod_args = self.cfg["modulatore_args"]
        flow     = str(self._base / self.cfg["flowgraph"])
        extra    = self.cfg["ffmpeg_extra"]

        ffmpeg_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-re", "-i", url,
        ] + extra + [
            "-f", "s16le", "-ac", "2", "-ar", "48000", "-"
        ]
        mod_cmd  = [mod_bin] + mod_args
        flow_cmd = ["python3", flow]

        for cmd in (ffmpeg_cmd, mod_cmd, flow_cmd):
            self._log_line("[chain] $ " + " ".join(cmd))

        # Processo 1: ffmpeg
        ffmpeg = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self._base),
        )

        # Processo 2: modulatore (legge da ffmpeg.stdout)
        modulatore = subprocess.Popen(
            mod_cmd,
            stdin=ffmpeg.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self._base),
        )
        ffmpeg.stdout.close()   # modulatore ora possiede la pipe

        # Processo 3: flowgraph GNURadio (legge da modulatore.stdout)
        flowgraph = subprocess.Popen(
            flow_cmd,
            stdin=modulatore.stdout,
            stderr=subprocess.PIPE,
            cwd=str(self._base),
        )
        modulatore.stdout.close()

        self._procs = [ffmpeg, modulatore, flowgraph]

        # Thread stderr logger per ciascun processo
        for proc, name in zip(self._procs, ("ffmpeg", "modulatore", "flowgraph")):
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
        """Monitora i processi ogni 2 s; riavvia se auto_restart=True."""
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
