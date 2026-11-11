# LKV373A / HHD264 IPTV stream decryption

Decrypt the "encrypted" MPEG-TS video stream produced by the cheap HDMI-over-IP
encoders built on the **ITE / HDbitT** platform — sold as **LKV373A**, **HHD264**
and many rebrands. These boxes take HDMI in and emit an H.264 multicast stream
(typically `239.255.42.42:5004`) that is lightly scrambled so third-party players
show only garbage.

This project documents the scrambling, recovers the key from the firmware, and
ships a small dependency-light Python library + proof-of-concept tools that turn
the stream back into a normal `.ts` any player can read.

## TL;DR — how the "encryption" works

Two findings, both verified against a real capture and by reading the encoder's
OpenRISC firmware:

1. **The AES key is a factory constant baked into the firmware.** It is *not*
   per-device, not derived, not exchanged on the network:

   ```
   AES-128 key = 32451689c72c5ab3140364d3b3d42024
   ```

   The same 16 bytes appear in every firmware revision of the whole product line
   (7 TX / encoder + 6 RX / decoder images, 2015–2016). It never travels over the
   wire, which is why network sniffing never found it.

2. **Only the first 8 TS packets of each frame are encrypted** (selective /
   partial encryption). Everything after packet 8 of a frame is sent in the clear.
   Decrypting *every* packet (the naive assumption) actually corrupts the clear
   tail — which is what made this stream look "unbreakable".

### Decryption recipe

For each video (PID `0x7D1`) / audio (PID `0x7D2`) frame:

* the first packet (PUSI=1) carries a 16-byte `transport_private_data` in its
  adaptation field — this is `AES-ECB-encrypt(key, R)` for a random nonce `R`;
* recover `R = AES-ECB-decrypt(key, transport_private_data)`;
* XOR **only the first 8 TS packets** of the frame with `R` (tiled with period 16,
  phase reset to `R[0]` at each packet's payload start);
* leave packets 9.. untouched.

Full details and the firmware evidence: [`docs/encryption.md`](docs/encryption.md).

## Repository layout

```
lkviptv/            reusable library
  decrypt.py          the selective-decryption core (StreamDecryptor)
  pcap.py             minimal pcapng + IPv4/UDP + TS helpers (no deps)
  aes.py              AES-128 (pure Python, uses pycryptodome if available)
tools/
  smaz.py             firmware decompressor (SMEDIA02 / SMAZ = UCL NRV2E 8-bit)
  find_key.py         decompress a firmware and locate the key constant
  recover_key.py      recover the key from scratch (capture + firmware)
  or1k.py             OpenRISC or1k disassembler (used for the reverse)
poc/
  decrypt_pcap.py     offline:  capture.pcapng -> clear .ts
  capture_live.py     live:     join multicast, decrypt on the fly, record .ts
player/
  player.py           standalone live player (multicast -> decrypt -> on-screen video)
docs/
  encryption.md       the scrambling scheme, in detail
  firmware.md         firmware container format + how to extract the key
  reversing.md        the OpenRISC reverse engineering (how it was found)
```

## Quick start

Requires Python 3.8+. `pip install pycryptodome` is recommended (faster AES; the
library falls back to a pure-Python AES if it is missing).

Decrypt a capture you made with Wireshark / `tcpdump`:

```bash
python poc/decrypt_pcap.py capture.pcapng clear.ts
ffplay clear.ts        # or VLC / mpv
```

Record the live stream straight to a clear file:

```bash
python poc/capture_live.py out.ts --iface 192.168.1.50 --seconds 20
```

Watch the live stream in a standalone window (no external player):

```bash
pip install -r player/requirements.txt       # av + opencv-python
python player/player.py --iface 192.168.1.50
```

Prove where the key lives / recover it yourself from firmware:

```bash
python tools/find_key.py    LKV373A_TX_ENCODE_20160722.bin
python tools/recover_key.py capture.pcapng LKV373A_TX_ENCODE_20160722.bin
```

## Firmware

Firmware images are **not** included here. They are third-party binaries; get them
from the LKV373A reverse-engineering community (e.g. daniel "danman" Kučera's
shared archive). Both tools accept the packed container directly — decompression
(SMEDIA02 / SMAZ) is automatic.

## Credits

This project builds on a decade of community reverse-engineering of the LKV373A /
ITE platform. Full attributions and links are in [`CREDITS.md`](CREDITS.md); in
short:

* **danman (Daniel Kučera)** — LKV373A protocol/firmware reverse engineering and the
  shared firmware archive we decompressed and scanned.
  [github.com/danman/lkv373a](https://github.com/danman/lkv373a)
* **smaeul (Samuel Holland)** — `ghidra-or1k`, the OpenRISC big-endian/delay-slot
  Ghidra processor module that made the encoder readable.
  [github.com/smaeul/ghidra-or1k](https://github.com/smaeul/ghidra-or1k)
* **v3l0c1r4pt0r** — `ittk` and the SMEDIA02/ITEPKG03/SMAZ container documentation;
  our `tools/smaz.py` is a clean-room reimplementation from it.
  [github.com/v3l0c1r4pt0r/ittk](https://github.com/v3l0c1r4pt0r/ittk)
* **Markus F.X.J. Oberhumer — UCL/NRV2E** (the firmware's compression algorithm),
  **FFmpeg** (decode/verify), **PyCryptodome** (AES), and the **MPEG-TS / H.264 /
  AES** standards. See [`CREDITS.md`](CREDITS.md).

Original to this repository: the specific factory key value and its constancy
across the firmware line, the selective-encryption finding (only the first 8 TS
packets per frame are encrypted), and the end-to-end offline/live decryption.

## Responsible use

This is an interoperability / security-research tool for hardware you own: it lets
you read the stream your own encoder produces. Do not use it to capture or
redistribute content you are not entitled to. The key here was recovered from
publicly available firmware and is a manufacturer constant, not a secret belonging
to any user.

## Status

* [x] Encryption scheme fully reverse-engineered and documented
* [x] Key recovery from firmware
* [x] Offline decryption (pcap -> clear .ts) and live capture PoC
* [x] Standalone Python player, v2 (video + audio, audio-master A/V sync)
* [ ] Player UI polish: embedded Qt / libmpv surface, transport bar

Licensed under the MIT License — see [`LICENSE`](LICENSE).
