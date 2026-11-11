#!/usr/bin/env python3
"""Locate the AES key constant inside an encoder/decoder firmware.

Decompresses every SMEDIA02/SMAZ image found in the firmware container
(.bin / .PKG / .ROM ...) and searches the decompressed code for the known
16-byte key. Reports the file and byte offset of each hit.

    python tools/find_key.py LKV373A_TX_ENCODE_20160722.bin
    python tools/find_key.py some_decompressed_image.bin --raw
    python tools/find_key.py fw.bin --key 32451689c72c5ab3140364d3b3d42024

Firmware images are NOT distributed here; get them from the LKV373A community
(see README). Pass the packed container directly - decompression is automatic.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))         # tools/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
import smaz  # noqa: E402
from lkviptv import DEFAULT_KEY  # noqa: E402


def find_all(hay, needle):
    out, i = [], 0
    while True:
        j = hay.find(needle, i)
        if j < 0:
            break
        out.append(j)
        i = j + 1
    return out


def smaz_images(data):
    """Yield decompressed blobs for every valid SMAZ container in `data`."""
    for off in find_all(data, b'SMAZ'):
        try:
            magic, _u1, _u2, size = struct.unpack_from('>4sIII', data, off)
        except struct.error:
            continue
        if magic != b'SMAZ' or not (0 < size < len(data)):
            continue
        try:
            yield off, smaz.smaz(data, off)
        except Exception as e:
            print(f"  [skip] SMAZ @0x{off:x}: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('firmware', help='firmware container or raw image')
    ap.add_argument('--raw', action='store_true',
                    help='treat input as already-decompressed; do not run SMAZ')
    ap.add_argument('--key', default=DEFAULT_KEY.hex(), help='key to search for (32 hex chars)')
    args = ap.parse_args()

    key = bytes.fromhex(args.key)
    data = open(args.firmware, 'rb').read()
    name = os.path.basename(args.firmware)
    found = False

    if args.raw:
        images = [(None, data)]
    else:
        images = list(smaz_images(data)) or [(None, data)]  # fall back to raw scan

    for off, blob in images:
        tag = f"{name} (raw)" if off is None else f"{name} SMAZ@0x{off:x} ({len(blob)} bytes)"
        hits = find_all(blob, key)
        if hits:
            found = True
            for h in hits:
                ctx = blob[h - 8:h + 24].hex()
                print(f"KEY FOUND: {tag}  @ 0x{h:x}   context={ctx}")
        else:
            print(f"(not in) {tag}")

    if not found:
        print("Key not found. If this is a packed container, make sure it holds an "
              "SMEDIA02/SMAZ encoder image; try --raw on an already-decompressed file.")
        sys.exit(1)


if __name__ == '__main__':
    main()
