#!/usr/bin/env python3
"""Standalone live player (v3) with a Qt UI.

Same engine as player.py (multicast -> decrypt -> PyAV decode of video+audio ->
sounddevice audio, audio-master A/V sync), but rendered in a proper Qt window with
a transport bar (play/pause, mute, volume, fullscreen) and a status line.

    python player/player_qt.py --iface 192.168.1.50
    python player/player_qt.py --file clear.ts
    python player/player_qt.py --iface 192.168.1.50 --record out.ts --no-audio

Dependencies:  pip install -r player/requirements.txt  +  pip install PySide6
(On Windows, run with `py` so you use the interpreter the packages are in.)
"""
import argparse
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))               # player/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from lkviptv import DEFAULT_KEY
# reuse the already-working network/decrypt source and helpers from player.py
from player import DecryptingMulticastSource, MasterClock, AUDIO_RATE, AUDIO_CH, _as_list

try:
    import av
except ImportError:
    sys.exit("PyAV is required: pip install av")
try:
    import numpy as np
except ImportError:
    sys.exit("NumPy is required: pip install numpy")
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    sys.exit("PySide6 is required for the Qt UI: pip install PySide6")


# --------------------------------------------------------------------------- #
# Engine: decode + audio + pacing, emits QImage frames to the GUI
# --------------------------------------------------------------------------- #
class Engine(QtCore.QObject):
    frame_ready = QtCore.Signal(QtGui.QImage)
    status = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, source, is_live=True, want_audio=True):
        super().__init__()
        self.source, self.is_live, self.want_audio = source, is_live, want_audio
        self.clock = MasterClock()
        self.audio_q = queue.Queue(maxsize=48)
        self.video_q = queue.Queue(maxsize=24)
        self._paused = threading.Event()
        self._muted = False
        self._volume = 1.0
        self._stop = threading.Event()
        self._t0 = None

    # controls (called from the GUI thread) ---------------------------------
    def set_paused(self, p): self._paused.set() if p else self._paused.clear()
    def set_muted(self, m): self._muted = bool(m)
    def set_volume(self, v): self._volume = max(0.0, min(2.0, v))
    def stop(self): self._stop.set()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    # decode + audio (same model as player.py) ------------------------------
    def _put(self, q, item):
        while not self._stop.is_set():
            try:
                q.put(item, timeout=0.25); return
            except queue.Full:
                continue

    def _decode_loop(self, container, vstream, astream):
        resampler = av.AudioResampler(format='s16', layout='stereo', rate=AUDIO_RATE) if astream else None
        streams = [s for s in (vstream, astream) if s is not None]
        try:
            for packet in container.demux(*streams):
                if self._stop.is_set():
                    break
                for frame in packet.decode():
                    if frame.pts is None:
                        continue
                    pts = float(frame.pts * packet.stream.time_base)
                    if packet.stream.type == 'video':
                        self._put(self.video_q, (pts, frame.to_ndarray(format='rgb24')))
                    elif packet.stream.type == 'audio' and self.want_audio:
                        for out in _as_list(resampler.resample(frame)):
                            self._put(self.audio_q, (pts, out.to_ndarray().reshape(-1, AUDIO_CH)))
        except (av.AVError, StopIteration):
            pass
        finally:
            self._put(self.video_q, None)
            self._put(self.audio_q, None)

    def _audio_loop(self):
        import sounddevice as sd
        try:
            stream = sd.OutputStream(samplerate=AUDIO_RATE, channels=AUDIO_CH, dtype='int16')
            stream.start()
        except Exception as e:
            self.status.emit(f"audio disabled: {e}")
            while not self._stop.is_set():
                if self.audio_q.get() is None:
                    break
            return
        try:
            while not self._stop.is_set():
                item = self.audio_q.get()
                if item is None:
                    break
                pts, pcm = item
                if self._t0 is None:
                    self._t0 = pts
                while self._paused.is_set() and not self._stop.is_set():
                    time.sleep(0.02)
                out = pcm
                if self._muted:
                    out = np.zeros_like(pcm)
                elif self._volume != 1.0:
                    out = np.clip(pcm.astype(np.int32) * self._volume, -32768, 32767).astype(np.int16)
                stream.write(out)
                self.clock.add_samples(len(pcm))
        finally:
            stream.stop(); stream.close()

    def _run(self):
        opts = {}
        if self.is_live:
            opts = {'fflags': 'nobuffer', 'flags': 'low_delay',
                    'probesize': '1000000', 'analyzeduration': '2000000'}
        try:
            container = av.open(self.source, format='mpegts', options=opts)
        except Exception as e:
            self.status.emit(f"open failed: {e}"); self.finished.emit(); return
        vstream = next((s for s in container.streams if s.type == 'video'), None)
        astream = next((s for s in container.streams if s.type == 'audio'), None)
        if vstream is None:
            self.status.emit("no video stream"); self.finished.emit(); return
        vstream.thread_type = 'AUTO'

        threading.Thread(target=self._decode_loop, args=(container, vstream, astream), daemon=True).start()
        audio_on = self.want_audio and astream is not None
        if audio_on:
            threading.Thread(target=self._audio_loop, daemon=True).start()

        start_wall = time.time()
        shown = 0
        while not self._stop.is_set():
            item = self.video_q.get()
            if item is None:
                break
            vpts, rgb = item
            if audio_on:
                self.clock.started.wait(timeout=5.0)
                if self._t0 is not None:
                    self._wait_until(lambda: self.clock.now(), vpts - self._t0)
            else:
                if shown == 0:
                    self._t0 = vpts; start_wall = time.time()
                self._wait_until(lambda: time.time() - start_wall, vpts - (self._t0 or vpts))

            h, w, _ = rgb.shape
            buf = np.ascontiguousarray(rgb)
            qimg = QtGui.QImage(buf.data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy()
            self.frame_ready.emit(qimg)
            shown += 1
            if shown % 30 == 0:
                st = 'paused' if self._paused.is_set() else ('muted' if self._muted else 'live')
                self.status.emit(f"{w}x{h}   {st}   vol {int(self._volume*100)}%")
        container.close()
        self.finished.emit()

    def _wait_until(self, clock_fn, target):
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.02); continue
            diff = target - clock_fn()
            if diff <= 0:
                return
            time.sleep(min(diff, 0.02))


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class VideoLabel(QtWidgets.QLabel):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 180)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setStyleSheet("background:#000;")
        self._pix = None

    def set_image(self, qimg):
        self._pix = QtGui.QPixmap.fromImage(qimg)
        self._rescale()

    def resizeEvent(self, e):
        self._rescale(); super().resizeEvent(e)

    def _rescale(self):
        if self._pix:
            self.setPixmap(self._pix.scaled(self.size(), QtCore.Qt.KeepAspectRatio,
                                            QtCore.Qt.SmoothTransformation))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self.setWindowTitle("LKV373A IPTV")
        self.resize(1280, 760)

        self.video = VideoLabel()
        self.btn_pause = QtWidgets.QPushButton("Pause")
        self.btn_mute = QtWidgets.QPushButton("Mute")
        self.btn_full = QtWidgets.QPushButton("Fullscreen")
        self.vol = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.vol.setRange(0, 200); self.vol.setValue(100); self.vol.setFixedWidth(140)
        self.status_lbl = QtWidgets.QLabel("connecting...")

        bar = QtWidgets.QHBoxLayout()
        for w in (self.btn_pause, self.btn_mute):
            bar.addWidget(w)
        bar.addWidget(QtWidgets.QLabel("Vol")); bar.addWidget(self.vol)
        bar.addStretch(1); bar.addWidget(self.status_lbl); bar.addStretch(1)
        bar.addWidget(self.btn_full)

        central = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video, 1)
        barw = QtWidgets.QWidget(); barw.setLayout(bar)
        lay.addWidget(barw)
        self.setCentralWidget(central)

        self._paused = False
        self._muted = False
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_mute.clicked.connect(self._toggle_mute)
        self.btn_full.clicked.connect(self._toggle_full)
        self.vol.valueChanged.connect(lambda v: self.engine.set_volume(v / 100.0))

        engine.frame_ready.connect(self.video.set_image)
        engine.status.connect(self.status_lbl.setText)
        engine.finished.connect(self.close)

        for key, fn in ((QtCore.Qt.Key_Space, self._toggle_pause),
                        (QtCore.Qt.Key_M, self._toggle_mute),
                        (QtCore.Qt.Key_F, self._toggle_full),
                        (QtCore.Qt.Key_Q, self.close)):
            QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=fn)

    def _toggle_pause(self):
        self._paused = not self._paused
        self.engine.set_paused(self._paused)
        self.btn_pause.setText("Play" if self._paused else "Pause")

    def _toggle_mute(self):
        self._muted = not self._muted
        self.engine.set_muted(self._muted)
        self.btn_mute.setText("Unmute" if self._muted else "Mute")

    def _toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self, e):
        self.engine.stop()
        super().closeEvent(e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--group', default='239.255.42.42')
    ap.add_argument('--port', type=int, default=5004)
    ap.add_argument('--iface', default=None, help='local interface IP to join on (recommended)')
    ap.add_argument('--key', default=DEFAULT_KEY.hex())
    ap.add_argument('--record', default=None, help='also write the clear stream to this .ts')
    ap.add_argument('--file', default=None, help='play an existing clear .ts instead of the network')
    ap.add_argument('--no-audio', action='store_true')
    args = ap.parse_args()

    app = QtWidgets.QApplication(sys.argv)

    src = None
    if args.file:
        source, is_live = args.file, False
    else:
        src = DecryptingMulticastSource(args.group, args.port, args.iface,
                                        bytes.fromhex(args.key), record_path=args.record).start()
        source, is_live = src, True

    engine = Engine(source, is_live=is_live, want_audio=not args.no_audio)
    win = MainWindow(engine)
    win.show()
    engine.start()
    try:
        code = app.exec()
    finally:
        engine.stop()
        if src is not None:
            src.close()
    sys.exit(code)


if __name__ == '__main__':
    main()
