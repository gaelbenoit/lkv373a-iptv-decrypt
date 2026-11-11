#!/usr/bin/env python3
"""Standalone live player (v2) for the LKV373A / HHD264 encrypted IPTV stream.

Joins the encoder's multicast group, decrypts on the fly (lkviptv), decodes H.264
video + MPEG audio with PyAV (libav, in-process), plays audio through sounddevice
and shows the video in an OpenCV window with audio-master A/V sync -- the classic
ffplay model. No external ffmpeg or VLC process.

    python player/player.py --iface 192.168.1.50
    python player/player.py --iface 192.168.1.50 --record out.ts
    python player/player.py --file clear.ts           # offline test (no device)
    python player/player.py --iface 192.168.1.50 --no-audio

Keys:  q / Esc = quit    space = pause    m = mute    f = fullscreen    + / - = volume

Dependencies:  pip install -r player/requirements.txt   (av, opencv-python, sounddevice)
"""
import argparse
import os
import queue
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lkviptv import StreamDecryptor, DEFAULT_KEY

try:
    import av
except ImportError:
    sys.exit("PyAV is required: pip install av")
try:
    import cv2
except ImportError:
    sys.exit("OpenCV is required: pip install opencv-python")
try:
    import numpy as np
except ImportError:
    sys.exit("NumPy is required: pip install numpy")

AUDIO_RATE = 48000
AUDIO_CH = 2


# --------------------------------------------------------------------------- #
# Live source: multicast -> decrypt -> clear MPEG-TS (readable file-like)
# --------------------------------------------------------------------------- #
class DecryptingMulticastSource:
    def __init__(self, group, port, iface_ip, key, record_path=None, max_buffer=4 << 20):
        self._dec = StreamDecryptor(key=key)
        self._group, self._port, self._iface = group, port, iface_ip
        self._buf = bytearray()
        self._cv = threading.Condition()
        self._stopped = False
        self._max_buffer = max_buffer
        self._record = open(record_path, 'wb') if record_path else None
        self._thread = threading.Thread(target=self._rx_loop, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _open_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(('', self._port))
        mreq = struct.pack('4s4s', socket.inet_aton(self._group),
                           socket.inet_aton(self._iface or '0.0.0.0'))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.settimeout(1.0)
        return s

    def _rx_loop(self):
        sock = self._open_socket()
        try:
            while not self._stopped:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                if not data:
                    continue
                clear = self._dec.process_payload(data)
                if self._record:
                    self._record.write(clear)
                with self._cv:
                    self._buf += clear
                    if len(self._buf) > self._max_buffer:
                        drop = len(self._buf) - self._max_buffer
                        del self._buf[:drop - (drop % 188)]
                    self._cv.notify_all()
        finally:
            sock.close()
            if self._record:
                self._record.close()

    def read(self, n):
        with self._cv:
            while not self._buf and not self._stopped:
                self._cv.wait(0.5)
            if not self._buf and self._stopped:
                return b''
            take = min(n, len(self._buf))
            chunk = bytes(self._buf[:take])
            del self._buf[:take]
            return chunk

    def close(self):
        self._stopped = True
        with self._cv:
            self._cv.notify_all()


# --------------------------------------------------------------------------- #
# Master clock (audio-driven)
# --------------------------------------------------------------------------- #
class MasterClock:
    """Audio is the master: `now()` returns seconds of audio actually played,
    relative to the first audio sample. Video is paced against it."""
    def __init__(self):
        self._samples = 0
        self._lock = threading.Lock()
        self.started = threading.Event()

    def add_samples(self, n):
        with self._lock:
            self._samples += n
            if not self.started.is_set():
                self.started.set()

    def now(self):
        with self._lock:
            return self._samples / AUDIO_RATE


# --------------------------------------------------------------------------- #
# Player
# --------------------------------------------------------------------------- #
class Player:
    def __init__(self, source, title='LKV373A IPTV', is_live=True, want_audio=True):
        self.source = source
        self.title = title
        self.is_live = is_live
        self.want_audio = want_audio
        self.clock = MasterClock()
        self.audio_q = queue.Queue(maxsize=48)
        self.video_q = queue.Queue(maxsize=24)
        self.paused = threading.Event()
        self.muted = False
        self.volume = 1.0
        self.fullscreen = False
        self._stop = threading.Event()
        self._t0_audio = None      # pts (s) of first audio frame -> shared time origin

    # ---- decode thread: demux + decode both streams ---- #
    def _decode_loop(self, container, vstream, astream):
        resampler = None
        if astream is not None:
            resampler = av.AudioResampler(format='s16', layout='stereo', rate=AUDIO_RATE)
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
                        img = frame.to_ndarray(format='bgr24')
                        self._put(self.video_q, (pts, img))
                    elif packet.stream.type == 'audio' and self.want_audio:
                        for out in _as_list(resampler.resample(frame)):
                            pcm = out.to_ndarray().reshape(-1, AUDIO_CH)
                            self._put(self.audio_q, (pts, pcm))
        except (av.AVError, StopIteration):
            pass
        finally:
            self._put(self.video_q, None)
            self._put(self.audio_q, None)

    def _put(self, q, item):
        while not self._stop.is_set():
            try:
                q.put(item, timeout=0.25)
                return
            except queue.Full:
                continue

    # ---- audio thread: play PCM, drive the master clock ---- #
    def _audio_loop(self):
        import sounddevice as sd
        try:
            stream = sd.OutputStream(samplerate=AUDIO_RATE, channels=AUDIO_CH, dtype='int16')
            stream.start()
        except Exception as e:
            print(f"[audio disabled] {e}", file=sys.stderr)
            # drain the queue so decode doesn't block; video still plays (clock via fallback)
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
                if self._t0_audio is None:
                    self._t0_audio = pts
                while self.paused.is_set() and not self._stop.is_set():
                    time.sleep(0.02)
                out = pcm
                if self.muted:
                    out = np.zeros_like(pcm)
                elif self.volume != 1.0:
                    out = np.clip(pcm.astype(np.int32) * self.volume, -32768, 32767).astype(np.int16)
                stream.write(out)
                self.clock.add_samples(len(pcm))
        finally:
            stream.stop(); stream.close()

    # ---- main thread: display video paced by the master clock ---- #
    def run(self):
        opts = {}
        if self.is_live:
            opts = {'fflags': 'nobuffer', 'flags': 'low_delay',
                    'probesize': '1000000', 'analyzeduration': '2000000'}
        container = av.open(self.source, format='mpegts', options=opts)
        vstream = next((s for s in container.streams if s.type == 'video'), None)
        astream = next((s for s in container.streams if s.type == 'audio'), None)
        if vstream is None:
            sys.exit("No video stream found.")
        vstream.thread_type = 'AUTO'

        threading.Thread(target=self._decode_loop, args=(container, vstream, astream),
                         daemon=True).start()
        audio_on = self.want_audio and astream is not None
        if audio_on:
            threading.Thread(target=self._audio_loop, daemon=True).start()

        cv2.namedWindow(self.title, cv2.WINDOW_NORMAL)
        start_wall = time.time()
        shown = 0
        try:
            while not self._stop.is_set():
                item = self.video_q.get()
                if item is None:
                    break
                vpts, img = item

                # pace against the master clock (audio) or wall clock (no audio)
                if audio_on:
                    if not self.clock.started.wait(timeout=5.0):
                        pass  # audio never started; fall through to wall-clock pacing
                    if self._t0_audio is not None:
                        target = vpts - self._t0_audio
                        self._pace(lambda: self.clock.now(), target)
                else:
                    if shown == 0:
                        self._t0_audio = vpts
                        start_wall = time.time()
                    self._pace(lambda: time.time() - start_wall, vpts - self._t0_audio)

                self._overlay(img, vpts, audio_on)
                cv2.imshow(self.title, img)
                shown += 1
                if not self._handle_keys():
                    break
        finally:
            self._stop.set()
            container.close()
            cv2.destroyAllWindows()

    def _pace(self, clock_fn, target):
        # wait until the clock reaches the frame's time; drop if we're very late
        while not self._stop.is_set():
            if self.paused.is_set():
                self._handle_keys(); time.sleep(0.02); continue
            diff = target - clock_fn()
            if diff <= 0.0:
                return
            if diff > 1.0:
                diff = 1.0
            time.sleep(min(diff, 0.02))
            if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                self._stop.set(); return

    def _overlay(self, img, vpts, audio_on):
        clk = self.clock.now() if audio_on else 0.0
        state = 'PAUSE' if self.paused.is_set() else ('MUTE' if self.muted else 'LIVE')
        txt = f"{img.shape[1]}x{img.shape[0]}  {state}  vol {int(self.volume*100)}%"
        cv2.putText(img, txt, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(img, txt, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)

    def _handle_keys(self):
        k = cv2.waitKey(1) & 0xFF
        if k == 255:
            return True
        if k in (ord('q'), 27):
            return False
        if k == ord(' '):
            (self.paused.clear if self.paused.is_set() else self.paused.set)()
        elif k == ord('m'):
            self.muted = not self.muted
        elif k == ord('f'):
            self.fullscreen = not self.fullscreen
            cv2.setWindowProperty(self.title, cv2.WND_PROP_FULLSCREEN,
                                  cv2.WINDOW_FULLSCREEN if self.fullscreen else cv2.WINDOW_NORMAL)
        elif k in (ord('+'), ord('=')):
            self.volume = min(2.0, self.volume + 0.1)
        elif k == ord('-'):
            self.volume = max(0.0, self.volume - 0.1)
        return True

    def stop(self):
        self._stop.set()


def _as_list(x):
    return x if isinstance(x, (list, tuple)) else [x]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--group', default='239.255.42.42')
    ap.add_argument('--port', type=int, default=5004)
    ap.add_argument('--iface', default=None, help='local interface IP to join on (recommended)')
    ap.add_argument('--key', default=DEFAULT_KEY.hex())
    ap.add_argument('--record', default=None, help='also write the clear stream to this .ts')
    ap.add_argument('--file', default=None, help='play an existing clear .ts instead of the network')
    ap.add_argument('--no-audio', action='store_true', help='video only')
    args = ap.parse_args()

    if args.file:
        print(f"Playing {args.file}  (q/Esc quit, space pause, m mute, f fullscreen)")
        p = Player(args.file, is_live=False, want_audio=not args.no_audio)
        p.run()
        return

    src = DecryptingMulticastSource(args.group, args.port, args.iface,
                                    bytes.fromhex(args.key), record_path=args.record).start()
    print(f"Joined {args.group}:{args.port}" + (f" on {args.iface}" if args.iface else "")
          + "  (q/Esc quit, space pause, m mute, f fullscreen)")
    p = Player(src, is_live=True, want_audio=not args.no_audio)
    try:
        p.run()
    finally:
        src.close()


if __name__ == '__main__':
    main()
