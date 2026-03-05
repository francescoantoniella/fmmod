#!/usr/bin/env python3
"""
peak_tagger.py — Blocco GNURadio sync float→float (pass-through).
La FFT e il peak-finding girano in un thread separato per non bloccare
mai il thread audio di GNURadio.
"""
import numpy as np
import pmt
import threading
import queue
from gnuradio import gr

try:
    from scipy.signal import find_peaks
    _HAVE_SCIPY = True
except ImportError:
    _HAVE_SCIPY = False


class PeakTagger(gr.sync_block):

    def __init__(self,
                 samp_rate: float = 48000,
                 fft_size: int = 4096,
                 threshold_db: float = -60.0,
                 min_dist_hz: float = 200.0,
                 max_peaks: int = 10,
                 update_interval: int = 0,
                 tag_key: str = "peak"):

        gr.sync_block.__init__(self,
            name="Peak Tagger",
            in_sig=[np.float32],
            out_sig=[np.float32])

        self.samp_rate       = float(samp_rate)
        self.fft_size        = int(fft_size)
        self.threshold_db    = float(threshold_db)
        self.max_peaks       = int(max_peaks)
        self.update_interval = int(update_interval) if update_interval > 0 else fft_size
        self.tag_key         = pmt.intern(tag_key)

        self._window     = np.blackman(self.fft_size).astype(np.float64)
        self._window    /= self._window.sum()
        self._freqs      = np.fft.rfftfreq(self.fft_size, d=1.0 / self.samp_rate)
        self._freq_res   = self.samp_rate / self.fft_size
        self._db_offset  = 10.0 * np.log10(self.fft_size / 2.0)
        self.set_min_dist_hz(min_dist_hz)

        # Buffer circolare
        self._buf    = np.zeros(self.fft_size, dtype=np.float64)
        self._head   = 0
        self._filled = 0
        self._since  = 0

        # Tag pendenti da emettere nel prossimo work()
        self._pending_tags: list[tuple] = []
        self._tags_lock = threading.Lock()

        # Coda snapshot → thread FFT (maxsize=1: droppa se il thread è occupato)
        self._fft_queue: queue.Queue = queue.Queue(maxsize=1)
        self._fft_thread = threading.Thread(target=self._fft_worker, daemon=True)
        self._fft_thread.start()

    # ------------------------------------------------------------------
    def work(self, input_items, output_items):
        in0  = input_items[0]
        out0 = output_items[0]
        n    = len(in0)

        # Pass-through istantaneo
        out0[:n] = in0

        # Aggiorna buffer circolare con slice NumPy
        space = self.fft_size - self._head
        if n <= space:
            self._buf[self._head:self._head + n] = in0
            self._head = (self._head + n) % self.fft_size
        else:
            self._buf[self._head:] = in0[:space]
            rest = n - space
            self._buf[:rest] = in0[space:]
            self._head = rest

        self._filled += n
        self._since  += n

        # Emetti tag calcolati dal thread FFT
        offset = self.nitems_written(0) + n - 1
        with self._tags_lock:
            for (freq_hz, amp_db) in self._pending_tags:
                value = pmt.cons(pmt.from_double(freq_hz), pmt.from_double(amp_db))
                self.add_item_tag(0, offset, self.tag_key, value)
            self._pending_tags.clear()

        # Manda snapshot al thread FFT (non bloccante)
        if self._since >= self.update_interval and self._filled >= self.fft_size:
            self._since = 0
            buf_copy = np.concatenate((self._buf[self._head:], self._buf[:self._head])).copy()
            try:
                self._fft_queue.put_nowait(buf_copy)
            except queue.Full:
                pass  # thread ancora occupato, droppa questo frame

        return n

    # ------------------------------------------------------------------
    def _fft_worker(self):
        """Thread separato: calcola FFT e peak-finding senza toccare il flusso audio."""
        while True:
            buf = self._fft_queue.get()  # blocca finché non arriva uno snapshot
            if buf is None:
                break

            spectrum = np.fft.rfft(buf * self._window)
            mag_sq   = np.maximum(np.abs(spectrum) ** 2, 1e-30)
            db       = 10.0 * np.log10(mag_sq) - self._db_offset

            if _HAVE_SCIPY:
                peaks, _ = find_peaks(db, height=self.threshold_db,
                                      distance=self._min_dist_bins)
                if len(peaks) > self.max_peaks:
                    order = np.argsort(db[peaks])[::-1]
                    peaks = peaks[order[:self.max_peaks]]
            else:
                peaks = self._find_peaks_simple(db)

            tags = [(float(self._freqs[i]), float(db[i])) for i in peaks]
            with self._tags_lock:
                self._pending_tags = tags

    # ------------------------------------------------------------------
    def _find_peaks_simple(self, db):
        peaks, d = [], self._min_dist_bins
        i = 1
        while i < len(db) - 1:
            if db[i] > self.threshold_db and db[i] >= db[i-1] and db[i] >= db[i+1]:
                peaks.append(i); i += d
            else:
                i += 1
        peaks = np.array(peaks, dtype=int)
        if len(peaks) > self.max_peaks:
            peaks = peaks[np.argsort(db[peaks])[::-1][:self.max_peaks]]
        return peaks

    # ------------------------------------------------------------------
    def set_threshold_db(self, db: float): self.threshold_db = float(db)
    def set_max_peaks(self, n: int):       self.max_peaks = int(n)
    def set_min_dist_hz(self, hz: float):
        self.min_dist_hz    = float(hz)
        self._min_dist_bins = max(1, int(hz / self._freq_res))

    def stop(self):
        self._fft_queue.put(None)  # segnala al worker di fermarsi
