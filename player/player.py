#!/usr/bin/env python3
"""Standalone live player for the LKV373A / HHD264 encrypted IPTV stream.

Joins the encoder's multicast group, decrypts on the fly (lkviptv), decodes the
H.264 with PyAV (libav embedded in-process -- no external ffmpeg/VLC), and shows
the video in an OpenCV window.

    python player/player.py --iface 192.168.1.50
    python player/player.py --group 239.255.42.42 --port 5004 --iface 192.168.1.50
    python player/player.py --record out.ts          # also save the clear stream
    python player/player.py --file clear.ts          # play an existing clear .ts (offline test)

Keys:  q or Esc = quit.

Dependencies:  pip install av opencv-python   (both bundle / use ffmpeg's libav).
Audio is intentionally left out of this first version (video only).
"""
import argparse
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lkviptv import StreamDecryptor, DEFAULT_KEY

try:
    import av  # PyAV (libav)
except ImportError:
    sys.exit("PyAV is required: pip install av")
try:
    import cv2
except ImportError:
    sys.exit("OpenCV is required: pip install opencv-python")


class DecryptingMulticastSource:
    """Readable file-like object that yields a *clear* MPEG-TS byte stream.

    A background thread joins the multicast group, decrypts each datagram with
    lkviptv.StreamDecryptor and appends the clear TS bytes to an internal buffer.
    `read(n)` blocks until data is available (or the source is stopped), which is
    exactly what PyAV/libav expects from a custom I/O source.
    """

    def __init__(self, group, port, iface_ip, key, record_path=None, max_buffer=4 << 20):
        self._dec = StreamDecryptor(key=key)
        self._group, self._port, self._iface = group, port, iface_ip
        self._buf = bytearray()
        self._lock = threading.Condition()
        self._stopped = False
        self._max_buffer = max_buffer
        self._record = open(record_path, 'wb') if record_path else None
        self.datagrams = 0
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
                with self._lock:
                    self._buf += clear
                    if len(self._buf) > self._max_buffer:        # keep latency bounded
                        drop = len(self._buf) - self._max_buffer
                        del self._buf[:drop - (drop % 188)]       # stay TS-aligned
                    self.datagrams += 1
                    self._lock.notify_all()
        finally:
            sock.close()
            if self._record:
                self._record.close()

    # --- file-like API used by PyAV ---
    def read(self, n):
        with self._lock:
            while not self._buf and not self._stopped:
                self._lock.wait(0.5)
            if not self._buf and self._stopped:
                return b''
            take = min(n, len(self._buf))
            chunk = bytes(self._buf[:take])
            del self._buf[:take]
            return chunk

    def close(self):
        self._stopped = True
        with self._lock:
            self._lock.notify_all()


def play(source, title='LKV373A IPTV', is_live=True):
    opts = {}
    if is_live:
        opts = {'fflags': 'nobuffer', 'flags': 'low_delay',
                'probesize': '1000000', 'analyzeduration': '1000000'}
    container = av.open(source, format='mpegts', options=opts)
    vstream = next((s for s in container.streams if s.type == 'video'), None)
    if vstream is None:
        sys.exit("No video stream found.")
    vstream.thread_type = 'AUTO'

    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    n, t0 = 0, time.time()
    try:
        for frame in container.decode(vstream):
            img = frame.to_ndarray(format='bgr24')
            cv2.imshow(title, img)
            n += 1
            if n % 30 == 0:
                fps = n / (time.time() - t0)
                cv2.setWindowTitle(title, f"{title}  -  {img.shape[1]}x{img.shape[0]}  {fps:.1f} fps")
            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):   # q or Esc
                break
    except (av.AVError, StopIteration):
        pass
    finally:
        container.close()
        cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--group', default='239.255.42.42', help='multicast group')
    ap.add_argument('--port', type=int, default=5004, help='UDP port')
    ap.add_argument('--iface', default=None, help='local interface IP to join on (recommended)')
    ap.add_argument('--key', default=DEFAULT_KEY.hex(), help='AES-128 key (32 hex chars)')
    ap.add_argument('--record', default=None, help='also write the clear stream to this .ts')
    ap.add_argument('--file', default=None, help='play an existing clear .ts instead of the network')
    args = ap.parse_args()

    if args.file:
        print(f"Playing file {args.file} (q/Esc to quit)")
        play(args.file, is_live=False)
        return

    src = DecryptingMulticastSource(args.group, args.port, args.iface,
                                    bytes.fromhex(args.key), record_path=args.record).start()
    print(f"Joined {args.group}:{args.port}" + (f" on {args.iface}" if args.iface else "")
          + " - decoding... (q/Esc to quit)")
    try:
        play(src, is_live=True)
    finally:
        src.close()


if __name__ == '__main__':
    main()
