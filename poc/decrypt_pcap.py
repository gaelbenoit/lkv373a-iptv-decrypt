#!/usr/bin/env python3
"""Offline proof of concept: decrypt a captured stream (.pcapng) into a clear
MPEG-TS file playable by any player / ffmpeg.

    python poc/decrypt_pcap.py capture.pcapng clear.ts
    python poc/decrypt_pcap.py capture.pcapng clear.ts --port 5004 --key <hex32>

The .pcapng is any capture of the multicast UDP stream (e.g. made with Wireshark
or `tcpdump -w`). Only IPv4/UDP packets to the stream port are used.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lkviptv import StreamDecryptor, DEFAULT_KEY, pcapng_packets, udp_payload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pcapng', help='input capture (.pcapng)')
    ap.add_argument('out_ts', help='output clear MPEG-TS file (.ts)')
    ap.add_argument('--port', type=int, default=5004, help='UDP destination port (default 5004)')
    ap.add_argument('--key', default=DEFAULT_KEY.hex(), help='AES-128 key, 32 hex chars')
    args = ap.parse_args()

    dec = StreamDecryptor(key=bytes.fromhex(args.key))
    with open(args.out_ts, 'wb') as fo:
        for linktype, frame in pcapng_packets(args.pcapng):
            payload = udp_payload(linktype, frame, dst_port=args.port)
            if payload:
                fo.write(dec.process_payload(payload))

    s = dec.stats
    print(f"{s['packets']} TS packets, {s['frames']} frames, "
          f"{s['decrypted']} packets decrypted -> {args.out_ts}")


if __name__ == '__main__':
    main()
