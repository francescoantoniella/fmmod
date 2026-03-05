#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Stereo FM receiver and RDS Decoder
# GNU Radio version: 3.10.8.0

from PyQt5 import Qt
from gnuradio import qtgui
from gnuradio import analog
import math
from gnuradio import audio
from gnuradio import blocks
import pmt
from gnuradio import digital
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from PyQt5 import Qt
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from gnuradio.qtgui import Range, RangeWidget
from PyQt5 import QtCore
import rds
import sip
from peak_tagger import PeakTagger



class rds_rx(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "Stereo FM receiver and RDS Decoder", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("Stereo FM receiver and RDS Decoder")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("GNU Radio", "rds_rx")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        ##################################################
        # Variables
        ##################################################
        self.rrc_taps = rrc_taps = firdes.root_raised_cosine(1.0, 19000,19000/8, 1.0, 151)
        self.freq_offset = freq_offset = 250000
        self.freq = freq = 88.5
        self.volume = volume = -6
        self.samp_rate = samp_rate = 912000
        self.rrc_taps_manchester = rrc_taps_manchester = [rrc_taps[n] - rrc_taps[n+8] for n in range(len(rrc_taps)-8)]
        self.pilot_taps = pilot_taps = firdes.complex_band_pass(1.0, 240000, 18980, 19020, 1000, window.WIN_HAMMING, 6.76)
        self.gain = gain = 25
        self.freq_tune = freq_tune = freq*1e6 - freq_offset
        self.decimation = decimation = 1

        ##################################################
        # Blocks
        ##################################################

        self._volume_range = Range(-20, 10, 1, -6, 200)
        self._volume_win = RangeWidget(self._volume_range, self.set_volume, "Volume", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._volume_win, 1, 0, 1, 1)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 1):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._freq_range = Range(77, 108, 0.1, 88.5, 200)
        self._freq_win = RangeWidget(self._freq_range, self.set_freq, "Frequency", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._freq_win, 0, 0, 1, 1)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 1):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.rds_parser_0 = rds.parser(False, False, 0)
        self.rds_panel_0 = rds.rdsPanel(freq)
        self._rds_panel_0_win = self.rds_panel_0
        self.top_layout.addWidget(self._rds_panel_0_win)
        self.rds_decoder_0 = rds.decoder(False, False)
        self.rational_resampler_xxx_1 = filter.rational_resampler_ccc(
                interpolation=19000,
                decimation=(samp_rate // decimation // 10),
                taps=[],
                fractional_bw=0)
        self.rational_resampler_xxx_0 = filter.rational_resampler_fff(
                interpolation=240000,
                decimation=(samp_rate // decimation),
                taps=[],
                fractional_bw=0.4)
        self.qtgui_time_sink_x_0 = qtgui.time_sink_f(
            4096, #size
            48E3, #samp_rate
            "", #name
            2, #number of inputs
            None # parent
        )
        self.qtgui_time_sink_x_0.set_update_time(0.10)
        self.qtgui_time_sink_x_0.set_y_axis(-1, 1)

        self.qtgui_time_sink_x_0.set_y_label('Amplitude', "")

        self.qtgui_time_sink_x_0.enable_tags(True)
        self.qtgui_time_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, 0, "")
        self.qtgui_time_sink_x_0.enable_autoscale(False)
        self.qtgui_time_sink_x_0.enable_grid(False)
        self.qtgui_time_sink_x_0.enable_axis_labels(True)
        self.qtgui_time_sink_x_0.enable_control_panel(False)
        self.qtgui_time_sink_x_0.enable_stem_plot(False)


        labels = ['Signal 1', 'Signal 2', 'Signal 3', 'Signal 4', 'Signal 5',
            'Signal 6', 'Signal 7', 'Signal 8', 'Signal 9', 'Signal 10']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ['blue', 'red', 'green', 'black', 'cyan',
            'magenta', 'yellow', 'dark red', 'dark green', 'dark blue']
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]
        styles = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        markers = [-1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1]


        for i in range(2):
            if len(labels[i]) == 0:
                self.qtgui_time_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_time_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_time_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_time_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_time_sink_x_0.set_line_style(i, styles[i])
            self.qtgui_time_sink_x_0.set_line_marker(i, markers[i])
            self.qtgui_time_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_time_sink_x_0_win = sip.wrapinstance(self.qtgui_time_sink_x_0.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._qtgui_time_sink_x_0_win)
        self.qtgui_freq_sink_x_1 = qtgui.freq_sink_f(
            4096, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            0, #fc
            48000, #bw
            "", #name
            2,
            None # parent
        )
        self.qtgui_freq_sink_x_1.set_update_time(0.10)
        self.qtgui_freq_sink_x_1.set_y_axis((-140), 10)
        self.qtgui_freq_sink_x_1.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink_x_1.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_1.enable_autoscale(False)
        self.qtgui_freq_sink_x_1.enable_grid(False)
        self.qtgui_freq_sink_x_1.set_fft_average(1.0)
        self.qtgui_freq_sink_x_1.enable_axis_labels(True)
        self.qtgui_freq_sink_x_1.enable_control_panel(False)
        self.qtgui_freq_sink_x_1.set_fft_window_normalized(False)


        self.qtgui_freq_sink_x_1.set_plot_pos_half(not False)

        labels = ['', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(2):
            if len(labels[i]) == 0:
                self.qtgui_freq_sink_x_1.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink_x_1.set_line_label(i, labels[i])
            self.qtgui_freq_sink_x_1.set_line_width(i, widths[i])
            self.qtgui_freq_sink_x_1.set_line_color(i, colors[i])
            self.qtgui_freq_sink_x_1.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_x_1_win = sip.wrapinstance(self.qtgui_freq_sink_x_1.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._qtgui_freq_sink_x_1_win)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_f(
            2048, #size
            window.WIN_BLACKMAN_hARRIS, #wintype
            0, #fc
            (samp_rate / decimation), #bw
            "", #name
            1,
            None # parent
        )
        self.qtgui_freq_sink_x_0.set_update_time(0.10)
        self.qtgui_freq_sink_x_0.set_y_axis((-140), 10)
        self.qtgui_freq_sink_x_0.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_0.enable_autoscale(False)
        self.qtgui_freq_sink_x_0.enable_grid(False)
        self.qtgui_freq_sink_x_0.set_fft_average(1.0)
        self.qtgui_freq_sink_x_0.enable_axis_labels(True)
        self.qtgui_freq_sink_x_0.enable_control_panel(False)
        self.qtgui_freq_sink_x_0.set_fft_window_normalized(True)


        self.qtgui_freq_sink_x_0.set_plot_pos_half(not False)

        labels = ['', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_freq_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_freq_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_freq_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_freq_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_x_0_win = sip.wrapinstance(self.qtgui_freq_sink_x_0.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._qtgui_freq_sink_x_0_win)
        # ── Peak Tagger: MPX composito a 912 kHz ──────────────────────────
        self.peak_tagger_mpx = PeakTagger(
            samp_rate=samp_rate / decimation,   # 912000 Hz
            fft_size=2048,
            threshold_db=-80,       # mostra solo picchi sopra -80 dBFS
            min_dist_hz=1000,       # almeno 1 kHz tra picchi (pilot, stereo, rds ben separati)
            max_peaks=8,
            tag_key="peak"
        )

        # ── Peak Tagger: audio demodulato a 48 kHz ────────────────────────────
        self.peak_tagger_audio = PeakTagger(
            samp_rate=48000,
            fft_size=4096,
            threshold_db=-80,       # mostra solo picchi rilevanti
            min_dist_hz=50,         # 50 Hz di separazione minima
            max_peaks=6,
            tag_key="peak"
        )

        self._gain_range = Range(0, 49.6, 1, 25, 200)
        self._gain_win = RangeWidget(self._gain_range, self.set_gain, "RF Gain", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_grid_layout.addWidget(self._gain_win, 2, 0, 1, 1)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 1):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.freq_xlating_fir_filter_xxx_1_0 = filter.freq_xlating_fir_filter_fcc(10, firdes.low_pass(1.0, samp_rate / decimation, 7.5e3, 5e3), 57e3, (samp_rate / decimation))
        self.fir_filter_xxx_2 = filter.fir_filter_ccc(1, rrc_taps_manchester)
        self.fir_filter_xxx_2.declare_sample_delay(0)
        self.fir_filter_xxx_1_0 = filter.fir_filter_fff(5, firdes.low_pass(-2,240000,15e3,2e3))
        self.fir_filter_xxx_1_0.declare_sample_delay(0)
        self.fir_filter_xxx_1 = filter.fir_filter_fff(5, firdes.low_pass(1.0,240000,15e3,2e3))
        self.fir_filter_xxx_1.declare_sample_delay(0)
        self.fir_filter_xxx_0 = filter.fir_filter_fcc(1, pilot_taps)
        self.fir_filter_xxx_0.declare_sample_delay(0)
        self.digital_symbol_sync_xx_0 = digital.symbol_sync_cc(
            digital.TED_ZERO_CROSSING,
            16,
            0.01,
            1.0,
            1.0,
            0.1,
            1,
            digital.constellation_bpsk().base(),
            digital.IR_MMSE_8TAP,
            128,
            [])
        self.digital_diff_decoder_bb_0 = digital.diff_decoder_bb(2, digital.DIFF_DIFFERENTIAL)
        self.digital_constellation_receiver_cb_0 = digital.constellation_receiver_cb(digital.constellation_bpsk().base(), (2*math.pi / 100), (-0.002), 0.002)
        self.blocks_sub_xx_0 = blocks.sub_ff(1)
        self.blocks_multiply_xx_1 = blocks.multiply_vff(1)
        self.blocks_multiply_xx_0 = blocks.multiply_vcc(1)
        self.blocks_multiply_const_vxx_0_0 = blocks.multiply_const_ff((10**(1.*(volume)/10)))
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_ff((10**(1.*(volume)/10)))
        self.blocks_interleaved_short_to_complex_0 = blocks.interleaved_short_to_complex(False, False,2**15-1)
        self.blocks_file_source_0_0 = blocks.file_source(gr.sizeof_short*1, '/dev/stdin', True, 0, 0)
        self.blocks_file_source_0_0.set_begin_tag(pmt.PMT_NIL)
        self.blocks_delay_0 = blocks.delay(gr.sizeof_float*1, ((len(pilot_taps) - 1) // 2))
        self.blocks_complex_to_imag_0 = blocks.complex_to_imag(1)
        self.blocks_add_xx_0 = blocks.add_vff(1)
        self.audio_sink_0 = audio.sink(48000, '', True)
        self.analog_quadrature_demod_cf_0 = analog.quadrature_demod_cf(((samp_rate / decimation) / (2*math.pi*75000)))
        self.analog_pll_refout_cc_0 = analog.pll_refout_cc(0.001, (2 * math.pi * 19020 / 240000), (2 * math.pi * 18980 / 240000))
        self.analog_fm_deemph_0_0_0 = analog.fm_deemph(fs=48000, tau=(50e-6))
        self.analog_fm_deemph_0_0 = analog.fm_deemph(fs=48000, tau=(50e-6))
        self.analog_agc_xx_0 = analog.agc_cc((2e-3), 0.585, 53, 1000)


        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.rds_decoder_0, 'out'), (self.rds_parser_0, 'in'))
        self.msg_connect((self.rds_parser_0, 'out'), (self.rds_panel_0, 'in'))
        self.connect((self.analog_agc_xx_0, 0), (self.digital_symbol_sync_xx_0, 0))
        self.connect((self.analog_fm_deemph_0_0, 0), (self.blocks_multiply_const_vxx_0_0, 0))
        self.connect((self.analog_fm_deemph_0_0_0, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.analog_pll_refout_cc_0, 0), (self.blocks_multiply_xx_0, 0))
        self.connect((self.analog_pll_refout_cc_0, 0), (self.blocks_multiply_xx_0, 1))
        self.connect((self.analog_quadrature_demod_cf_0, 0), (self.freq_xlating_fir_filter_xxx_1_0, 0))
        self.connect((self.analog_quadrature_demod_cf_0, 0), (self.peak_tagger_mpx, 0))
        self.connect((self.peak_tagger_mpx, 0), (self.qtgui_freq_sink_x_0, 0))
        self.connect((self.analog_quadrature_demod_cf_0, 0), (self.rational_resampler_xxx_0, 0))
        self.connect((self.blocks_add_xx_0, 0), (self.analog_fm_deemph_0_0_0, 0))
        self.connect((self.blocks_complex_to_imag_0, 0), (self.blocks_multiply_xx_1, 1))
        self.connect((self.blocks_delay_0, 0), (self.blocks_multiply_xx_1, 0))
        self.connect((self.blocks_delay_0, 0), (self.fir_filter_xxx_1, 0))
        self.connect((self.blocks_file_source_0_0, 0), (self.blocks_interleaved_short_to_complex_0, 0))
        self.connect((self.blocks_interleaved_short_to_complex_0, 0), (self.analog_quadrature_demod_cf_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.audio_sink_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.qtgui_freq_sink_x_1, 1))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.qtgui_time_sink_x_0, 1))
        self.connect((self.blocks_multiply_const_vxx_0_0, 0), (self.audio_sink_0, 1))
        self.connect((self.blocks_multiply_const_vxx_0_0, 0), (self.peak_tagger_audio, 0))
        self.connect((self.peak_tagger_audio, 0), (self.qtgui_freq_sink_x_1, 0))
        self.connect((self.blocks_multiply_const_vxx_0_0, 0), (self.qtgui_time_sink_x_0, 0))
        self.connect((self.blocks_multiply_xx_0, 0), (self.blocks_complex_to_imag_0, 0))
        self.connect((self.blocks_multiply_xx_1, 0), (self.fir_filter_xxx_1_0, 0))
        self.connect((self.blocks_sub_xx_0, 0), (self.analog_fm_deemph_0_0, 0))
        self.connect((self.digital_constellation_receiver_cb_0, 0), (self.digital_diff_decoder_bb_0, 0))
        self.connect((self.digital_diff_decoder_bb_0, 0), (self.rds_decoder_0, 0))
        self.connect((self.digital_symbol_sync_xx_0, 0), (self.digital_constellation_receiver_cb_0, 0))
        self.connect((self.fir_filter_xxx_0, 0), (self.analog_pll_refout_cc_0, 0))
        self.connect((self.fir_filter_xxx_1, 0), (self.blocks_add_xx_0, 0))
        self.connect((self.fir_filter_xxx_1, 0), (self.blocks_sub_xx_0, 0))
        self.connect((self.fir_filter_xxx_1_0, 0), (self.blocks_add_xx_0, 1))
        self.connect((self.fir_filter_xxx_1_0, 0), (self.blocks_sub_xx_0, 1))
        self.connect((self.fir_filter_xxx_2, 0), (self.analog_agc_xx_0, 0))
        self.connect((self.freq_xlating_fir_filter_xxx_1_0, 0), (self.rational_resampler_xxx_1, 0))
        self.connect((self.rational_resampler_xxx_0, 0), (self.blocks_delay_0, 0))
        self.connect((self.rational_resampler_xxx_0, 0), (self.fir_filter_xxx_0, 0))
        self.connect((self.rational_resampler_xxx_1, 0), (self.fir_filter_xxx_2, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "rds_rx")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()

        event.accept()

    def get_rrc_taps(self):
        return self.rrc_taps

    def set_rrc_taps(self, rrc_taps):
        self.rrc_taps = rrc_taps
        self.set_rrc_taps_manchester([self.rrc_taps[n] - self.rrc_taps[n+8] for n in range(len(self.rrc_taps)-8)])

    def get_freq_offset(self):
        return self.freq_offset

    def set_freq_offset(self, freq_offset):
        self.freq_offset = freq_offset
        self.set_freq_tune(self.freq*1e6 - self.freq_offset)

    def get_freq(self):
        return self.freq

    def set_freq(self, freq):
        self.freq = freq
        self.set_freq_tune(self.freq*1e6 - self.freq_offset)
        self.rds_panel_0.set_frequency(self.freq)

    def get_volume(self):
        return self.volume

    def set_volume(self, volume):
        self.volume = volume
        self.blocks_multiply_const_vxx_0.set_k((10**(1.*(self.volume)/10)))
        self.blocks_multiply_const_vxx_0_0.set_k((10**(1.*(self.volume)/10)))

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.analog_quadrature_demod_cf_0.set_gain(((self.samp_rate / self.decimation) / (2*math.pi*75000)))
        self.freq_xlating_fir_filter_xxx_1_0.set_taps(firdes.low_pass(1.0, self.samp_rate / self.decimation, 7.5e3, 5e3))
        self.qtgui_freq_sink_x_0.set_frequency_range(0, (self.samp_rate / self.decimation))

    def get_rrc_taps_manchester(self):
        return self.rrc_taps_manchester

    def set_rrc_taps_manchester(self, rrc_taps_manchester):
        self.rrc_taps_manchester = rrc_taps_manchester
        self.fir_filter_xxx_2.set_taps(self.rrc_taps_manchester)

    def get_pilot_taps(self):
        return self.pilot_taps

    def set_pilot_taps(self, pilot_taps):
        self.pilot_taps = pilot_taps
        self.blocks_delay_0.set_dly(int(((len(self.pilot_taps) - 1) // 2)))
        self.fir_filter_xxx_0.set_taps(self.pilot_taps)

    def get_gain(self):
        return self.gain

    def set_gain(self, gain):
        self.gain = gain

    def get_freq_tune(self):
        return self.freq_tune

    def set_freq_tune(self, freq_tune):
        self.freq_tune = freq_tune

    def get_decimation(self):
        return self.decimation

    def set_decimation(self, decimation):
        self.decimation = decimation
        self.analog_quadrature_demod_cf_0.set_gain(((self.samp_rate / self.decimation) / (2*math.pi*75000)))
        self.freq_xlating_fir_filter_xxx_1_0.set_taps(firdes.low_pass(1.0, self.samp_rate / self.decimation, 7.5e3, 5e3))
        self.qtgui_freq_sink_x_0.set_frequency_range(0, (self.samp_rate / self.decimation))




def main(top_block_cls=rds_rx, options=None):

    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()

    tb.start()

    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec_()

if __name__ == '__main__':
    main()
