#!/usr/bin/env python3
"""Live proof of concept: join the encoder's multicast group, decrypt on the fly,
and record a clear MPEG-TS file.

    python poc/capture_live.py out.ts
    python poc/capture_live.py out.ts --group 239.255.42.42 --port 5004 --iface 192.168.1.50
    python poc/capture_live.py out.ts --seconds 20

Then play the result with any player:  ffplay out.ts   (or VLC, mpv, ...)

Notes
-----
* `--iface` is the IP of the local network card that sees the encoder. On a
  multi-homed Windows PC you almost always need to set it, otherwise the OS may
  join the group on the wrong interface and receive nothing.
* The stream has a known quirk: ~1/4 of the UDP datagrams are empty. They are
  simply skipped here; players tolerate it. (A real-time relay would re-insert
  them to keep strict timing.)
* Stop with Ctrl+C; the file is flushed and closed cleanly.
"""
import argparse
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lkviptv import StreamDecryptor, DEFAULT_KEY


def open_multicast(group, port, iface_ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(('', port))
    mreq = struct.pack('4s4s', socket.inet_aton(group),
                       socket.inet_aton(iface_ip or '0.0.0.0'))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(1.0)
    return sock


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out_ts', help='output clear MPEG-TS file (.ts)')
    ap.add_argument('--group', default='239.255.42.42', help='multicast group (default 239.255.42.42)')
    ap.add_argument('--port', type=int, default=5004, help='UDP port (default 5004)')
    ap.add_argument('--iface', default=None, help='local interface IP to join on (recommended)')
    ap.add_argument('--seconds', type=float, default=0, help='stop after N seconds (0 = until Ctrl+C)')
    ap.add_argument('--key', default=DEFAULT_KEY.hex(), help='AES-128 key, 32 hex chars')
    args = ap.parse_args()

    dec = StreamDecryptor(key=bytes.fromhex(args.key))
    sock = open_multicast(args.group, args.port, args.iface)
    print(f"Joined {args.group}:{args.port}"
          + (f" on {args.iface}" if args.iface else "") + f" -> {args.out_ts}")
    print("Recording... (Ctrl+C to stop)")

    t0 = time.time()
    datagrams = 0
    try:
        with open(args.out_ts, 'wb') as fo:
            while True:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    if args.seconds and time.time() - t0 >= args.seconds:
                        break
                    continue
                if data:
                    fo.write(dec.process_payload(data))
                    datagrams += 1
                if args.seconds and time.time() - t0 >= args.seconds:
                    break
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()

    s = dec.stats
    dt = time.time() - t0
    print(f"{datagrams} datagrams, {s['packets']} TS packets, {s['frames']} frames, "
          f"{s['decrypted']} packets decrypted in {dt:.1f}s -> {args.out_ts}")


if __name__ == '__main__':
    main()
