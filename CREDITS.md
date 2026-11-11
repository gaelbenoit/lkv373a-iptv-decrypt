# Credits & sources

This project stands on a decade of community reverse-engineering of the ITE /
HDbitT HDMI-over-IP platform (LKV373A and its many rebrands). It would not exist
without the work listed below. Where we reused a format, an algorithm or a tool,
it is called out explicitly.

## Prior reverse-engineering work

### danman — Daniel Kučera
- **LKV373A protocol & firmware reverse engineering**, and the shared firmware
  archive (TX / RX images, 2015–2016) that we decompressed and scanned to locate
  and cross-verify the AES key across firmware revisions.
- The "stream dump" reference material (a plaintext elementary stream and
  encrypt-with-known-key samples) that helped confirm the MPEG-TS / H.264 framing
  and the known-plaintext anchor.
- GitHub: https://github.com/danman/lkv373a — Blog: https://blog.danman.eu/
  (search "LKV373A").

### smaeul — Samuel Holland
- **`ghidra-or1k`**, the OpenRISC (big-endian, with delay slots) processor module
  for Ghidra, and the related or1k toolchain notes. These established the correct
  CPU, endianness and disassembly that made the encoder firmware readable.
- GitHub: https://github.com/smaeul/ghidra-or1k

### v3l0c1r4pt0r
- **`ittk`** (ITE package tooling) and the published notes describing the
  `SMEDIA02` / `ITEPKG03` container format and the `SMAZ` compressed chunks. Our
  pure-Python decompressor (`tools/smaz.py`) is a clean-room reimplementation based
  on this documentation and on the UCL NRV2E algorithm (below).
- GitHub: https://github.com/v3l0c1r4pt0r/ittk

## Algorithms & standards

- **UCL / NRV2E** — Markus F.X.J. Oberhumer. The `SMAZ` firmware chunks are
  compressed with the NRV2E (8-bit) variant of the UCL library; `tools/smaz.py`
  reimplements the NRV2E decompressor. http://www.oberhumer.com/opensource/ucl/
- **MPEG-2 Transport Stream** — ISO/IEC 13818-1 / ITU-T H.222.0. Used to parse TS
  packets, adaptation fields and `transport_private_data`.
- **H.264 / AVC** — ITU-T H.264 / ISO/IEC 14496-10. The PES + Access Unit
  Delimiter header gives the known-plaintext anchor used to recover the key.
- **AES-128** — FIPS 197. The scrambling uses AES-128-ECB (software, via mbedTLS
  in the firmware) over a per-frame nonce.
- **mbedTLS** — identified as the firmware's software AES implementation (by its
  self-test strings); not used by this project, credited for accuracy.
  https://www.trustedfirmware.org/projects/mbed-tls/

## Software used by this project

- **FFmpeg / libav** — used to decode and verify the recovered stream, and the
  intended decoder for the standalone player. https://ffmpeg.org/
- **PyCryptodome** — optional fast AES-128 used by the tools and library
  (a pure-Python fallback is bundled). https://www.pycryptodome.org/
- **Python** standard library (sockets, struct) for the capture and parsing code.

## What is original to this repository

To be precise about the boundary between prior art and this project's own
contribution, the following were determined here:

- the specific **factory AES-128 key value** and its location/constancy across the
  TX and RX firmware line;
- the **selective-encryption behaviour** — that only the first 8 TS packets of
  each frame are encrypted (the `l.sfgtui r24,7` guard in the packetizer), which is
  what previously made the stream appear unbreakable;
- the end-to-end **offline and live decryption** (capture / multicast → clear
  MPEG-TS) built on the above.

If you believe your work should be credited here and is missing, please open an
issue or a pull request.
