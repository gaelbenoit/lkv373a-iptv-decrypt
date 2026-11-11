#!/usr/bin/env python3
"""Reproduce the key discovery: recover the AES key from scratch, using only a
capture of the stream + the encoder firmware (no prior knowledge of the key).

Method
------
1. From the capture, collect "cribs": for each video frame (PID 0x7D1, PUSI=1)
   we know the clear start of the payload (MPEG-TS PES header + H.264 AUD):

       00 00 01 E0 ?? ?? 84 80 05 <PTS:5> 00 00 00 01 ...

   so 9 of the first 16 keystream bytes are known: R = cipher XOR plain at the
   known positions. R = AES-128-ECB-decrypt(KEY, transport_private_data).
2. Decompress the firmware and try EVERY 16-byte window as a candidate KEY,
   keeping the one for which AES-dec(window, private_data) reproduces the known
   keystream bytes on all cribs. A 9-byte match across many frames is unique.

    python tools/recover_key.py capture.pcapng LKV373A_TX_ENCODE_20160722.bin
    python tools/recover_key.py capture.pcapng decompressed.bin --raw

Install pycryptodome first (brute force does millions of AES ops):  pip install pycryptodome
"""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))         # tools/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
import smaz  # noqa: E402
from lkviptv.aes import aes_decrypt_block, HAVE_PYCRYPTODOME  # noqa: E402
from lkviptv.pcap import pcapng_packets, udp_payload  # noqa: E402
from lkviptv.decrypt import transport_private_data, payload_offset, VIDEO_PID  # noqa: E402

# Known clear bytes at the start of a video PES payload (positions within [0,16)).
KNOWN = {0: 0x00, 1: 0x00, 2: 0x01, 3: 0xE0, 6: 0x84, 7: 0x80, 8: 0x05, 14: 0x00, 15: 0x00}
_POS = sorted(KNOWN)


def extract_cribs(path, port=5004, want=200):
    """Return list of (private_data[16], known_keystream dict pos->byte)."""
    cribs = []
    for linktype, frame in pcapng_packets(path):
        payload = udp_payload(linktype, frame, dst_port=port)
        if not payload:
            continue
        for i in range(0, len(payload) - 188 + 1, 188):
            p = payload[i:i + 188]
            if p[0] != 0x47:
                continue
            pid = ((p[1] & 0x1F) << 8) | p[2]
            pusi = (p[1] >> 6) & 1
            if pid != VIDEO_PID or not pusi:
                continue
            pd = transport_private_data(p)
            if not pd:
                continue
            o = payload_offset(p)
            cipher = p[o:o + 16]
            if len(cipher) < 16:
                continue
            ks = {pos: cipher[pos] ^ KNOWN[pos] for pos in _POS}  # keystream R at known pos
            cribs.append((pd, ks))
            if len(cribs) >= want:
                return cribs
    return cribs


def smaz_images(data):
    out, i = [], 0
    offs = []
    while True:
        j = data.find(b'SMAZ', i)
        if j < 0:
            break
        offs.append(j)
        i = j + 1
    for off in offs:
        try:
            magic, _u1, _u2, size = struct.unpack_from('>4sIII', data, off)
        except struct.error:
            continue
        if magic != b'SMAZ' or not (0 < size < len(data)):
            continue
        try:
            out.append((off, smaz.smaz(data, off)))
        except Exception:
            pass
    return out


def brute_force(blob, cribs):
    pd0, ks0 = cribs[0]
    p0 = _POS[0]
    for off in range(0, len(blob) - 16 + 1):
        key = blob[off:off + 16]
        R = aes_decrypt_block(key, pd0)
        # cheap pre-filter on the first known position
        if R[p0] != ks0[p0]:
            continue
        if any(R[pos] != ks0[pos] for pos in _POS):
            continue
        # confirm against every crib
        if all(all(aes_decrypt_block(key, pd)[pos] == ks[pos] for pos in _POS)
               for pd, ks in cribs):
            return off, key
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pcapng', help='capture of the encrypted stream (.pcapng)')
    ap.add_argument('firmware', help='encoder firmware (packed container or --raw image)')
    ap.add_argument('--raw', action='store_true', help='firmware is already decompressed')
    ap.add_argument('--port', type=int, default=5004)
    args = ap.parse_args()

    if not HAVE_PYCRYPTODOME:
        print("WARNING: pycryptodome not installed; brute force will be very slow.\n"
              "         pip install pycryptodome", file=sys.stderr)

    cribs = extract_cribs(args.pcapng, port=args.port)
    print(f"{len(cribs)} video cribs extracted from {os.path.basename(args.pcapng)}")
    if len(cribs) < 3:
        print("Not enough cribs (need video PID 0x7D1 PUSI packets with private_data).")
        sys.exit(1)

    data = open(args.firmware, 'rb').read()
    images = [(None, data)] if args.raw else (smaz_images(data) or [(None, data)])

    t0 = time.time()
    for off, blob in images:
        where = 'raw' if off is None else f'SMAZ@0x{off:x}'
        print(f"scanning {where} ({len(blob)} bytes, {len(blob) - 15} windows)...")
        hit = brute_force(blob, cribs)
        if hit:
            koff, key = hit
            print(f"\n  *** KEY RECOVERED ***  {key.hex()}")
            print(f"      firmware image {where}, offset 0x{koff:x}, "
                  f"validated on {len(cribs)} frames in {time.time() - t0:.1f}s")
            return
    print("No key found in the provided firmware image(s).")
    sys.exit(1)


if __name__ == '__main__':
    main()
