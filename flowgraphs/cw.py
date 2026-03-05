#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Not titled yet
# Author: franc
# GNU Radio version: 3.10.8.0

from PyQt5 import Qt
from gnuradio import qtgui
from gnuradio import analog
from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.fft import window
import sys
import signal
from PyQt5 import Qt
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from gnuradio import iio
from gnuradio.qtgui import Range, RangeWidget
from PyQt5 import QtCore
import math



class cw(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "Not titled yet", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("Not titled yet")
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

        self.settings = Qt.QSettings("GNU Radio", "cw")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        ##################################################
        # Variables
        ##################################################
        self.samp_rate = samp_rate = 912000
        self.maxdev = maxdev = 15000
        self.lev = lev = 1
        self.freq = freq = 99
        self.att = att = 0

        ##################################################
        # Blocks
        ##################################################

        self._maxdev_range = Range(0, 2000000, 1000, 15000, 200)
        self._maxdev_win = RangeWidget(self._maxdev_range, self.set_maxdev, "Deviation", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_layout.addWidget(self._maxdev_win)
        self._lev_range = Range(0, 1, 0.01, 1, 200)
        self._lev_win = RangeWidget(self._lev_range, self.set_lev, "Level", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_layout.addWidget(self._lev_win)
        self._freq_range = Range(88, 107.9, 0.1, 99, 200)
        self._freq_win = RangeWidget(self._freq_range, self.set_freq, "freq", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_layout.addWidget(self._freq_win)
        self._att_range = Range(0, 40, 1, 0, 200)
        self._att_win = RangeWidget(self._att_range, self.set_att, "att", "counter_slider", float, QtCore.Qt.Horizontal)
        self.top_layout.addWidget(self._att_win)
        self.iio_pluto_sink_0 = iio.fmcomms2_sink_fc32('ip:192.168.2.1' if 'ip:192.168.2.1' else iio.get_pluto_uri(), [True, True], 32768, False)
        self.iio_pluto_sink_0.set_len_tag_key('')
        self.iio_pluto_sink_0.set_bandwidth(200000)
        self.iio_pluto_sink_0.set_frequency((int(freq*1e6)))
        self.iio_pluto_sink_0.set_samplerate(samp_rate)
        self.iio_pluto_sink_0.set_attenuation(0, att)
        self.iio_pluto_sink_0.set_filter_params('Auto', '', 200000, 240000)
        self.analog_sig_source_x_0 = analog.sig_source_f(samp_rate, analog.GR_COS_WAVE, 1000, lev, 0, 0)
        self.analog_frequency_modulator_fc_0 = analog.frequency_modulator_fc((2*math.pi*maxdev/samp_rate))


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_frequency_modulator_fc_0, 0), (self.iio_pluto_sink_0, 0))
        self.connect((self.analog_sig_source_x_0, 0), (self.analog_frequency_modulator_fc_0, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "cw")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()

        event.accept()

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.analog_frequency_modulator_fc_0.set_sensitivity((2*math.pi*self.maxdev/self.samp_rate))
        self.analog_sig_source_x_0.set_sampling_freq(self.samp_rate)
        self.iio_pluto_sink_0.set_samplerate(self.samp_rate)

    def get_maxdev(self):
        return self.maxdev

    def set_maxdev(self, maxdev):
        self.maxdev = maxdev
        self.analog_frequency_modulator_fc_0.set_sensitivity((2*math.pi*self.maxdev/self.samp_rate))

    def get_lev(self):
        return self.lev

    def set_lev(self, lev):
        self.lev = lev
        self.analog_sig_source_x_0.set_amplitude(self.lev)

    def get_freq(self):
        return self.freq

    def set_freq(self, freq):
        self.freq = freq
        self.iio_pluto_sink_0.set_frequency((int(self.freq*1e6)))

    def get_att(self):
        return self.att

    def set_att(self, att):
        self.att = att
        self.iio_pluto_sink_0.set_attenuation(0,self.att)




def main(top_block_cls=cw, options=None):

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
