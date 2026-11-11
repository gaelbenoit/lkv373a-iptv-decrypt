# Project state & handoff

Snapshot of where this project stands, written so it can be continued — including
**in another language** (C/C++/Rust/Go/...). The Python code here is a reference
implementation; the spec below is language-agnostic.

Last updated: 2026-11-11.

## 1. What is solved

The ITE / HDbitT (LKV373A / HHD264) encrypted IPTV stream is **fully decryptable**.
Two findings:

1. **Factory AES-128 key** (constant in the encoder firmware, same across the
   whole product line): `32451689c72c5ab3140364d3b3d42024`.
2. **Selective encryption**: only the **first 8 TS packets of each frame** are
   encrypted; the rest of the frame is clear.

Verified end to end: a real capture decodes to clean H.264 (0 crypto-caused MB
errors), and the Python player shows live video **and** audio in real time.

## 2. Reimplementation spec (language-agnostic)

Input: the raw UDP multicast MPEG-TS stream (default `239.255.42.42:5004`), or a
capture of it. Output: a standard clear MPEG-TS.

Constants:
- `KEY` = 16 bytes `32 45 16 89 c7 2c 5a b3 14 03 64 d3 b3 d4 20 24`
- `VIDEO_PID = 0x07D1`, `AUDIO_PID = 0x07D2`
- `ENCRYPTED_PACKETS_PER_FRAME = 8`

Per 188-byte TS packet, in arrival order:
```
sync must be 0x47 (byte 0)
pid  = ((byte1 & 0x1F) << 8) | byte2
pusi = (byte1 >> 6) & 1
afc  = (byte3 >> 4) & 3              # adaptation_field_control

if pid not in {VIDEO_PID, AUDIO_PID}:   emit unchanged   # PSI, null, etc.

payload_offset = (afc & 2) ? (5 + byte4) : 4             # byte4 = AF length

if pusi == 1:                            # first packet of a frame
    pd = transport_private_data(packet)  # 16 bytes, see below
    R  = AES128_ECB_decrypt(KEY, pd)     # keystream block
    packet_index = 0                     # reset per frame, per pid

if R is known and packet_index < 8 and payload_offset < 188:
    for n in [payload_offset, 188):
        packet[n] ^= R[(n - payload_offset) & 15]        # period-16 XOR, phase reset

packet_index += 1
emit packet
```

`transport_private_data(packet)` — parse the adaptation field of a PUSI packet:
```
if (byte3 >> 4) & 2 == 0: return none          # no adaptation field
aflen = byte4; if aflen == 0: return none
flags = byte5; o = 6
if (flags & 0x02) == 0: return none            # transport_private_data_flag
if flags & 0x10: o += 6                         # PCR
if flags & 0x08: o += 6                         # OPCR
if flags & 0x04: o += 1                         # splice_countdown
pdlen = byte[o]; o += 1
if pdlen < 16: return none
return bytes[o : o+16]
```

Notes:
- Track video and audio **independently** (separate `R` and `packet_index`).
- The nonce `R` is the AES-decryption of the private data; on the encoder side it
  is a random 16-byte value and `private_data = AES_ECB_encrypt(KEY, R)`.
- Known-plaintext anchor (for re-deriving the key from a capture + firmware, or for
  sanity checks): a video PES payload starts with
  `00 00 01 E0 ?? ?? 84 80 05 <PTS:5> 00 00 00 01 09 50 ...`.

Stream quirks to handle in a real player/relay:
- ~1/4 of UDP datagrams are **empty** (skip them; a strict relay re-inserts timing).
- Audio is MPEG-1 Layer II, 48 kHz, stereo. Video is H.264 1728x1080, Constrained
  Baseline, level 4.0.

## 3. What is implemented (Python reference)

- `lkviptv/` — library: `StreamDecryptor` (the spec above), pcap/TS helpers,
  pure-Python AES (pycryptodome fast path).
- `tools/` — `smaz.py` (SMEDIA02/SMAZ = UCL NRV2E decompressor), `find_key.py`
  (locate key in firmware), `recover_key.py` (recover key from capture + firmware),
  `or1k.py` (OpenRISC disassembler used for the reverse).
- `poc/` — `decrypt_pcap.py` (offline), `capture_live.py` (live → .ts).
- `player/` — `player.py` v2: live multicast → decrypt → PyAV decode (video+audio)
  → sounddevice audio + OpenCV window, audio-master A/V sync, keyboard controls.
  (`player_qt.py` adds a Qt UI over the same core.)

## 4. What is NOT done / next steps

- **Player UI polish** — a proper GUI (Qt done as `player_qt.py`; libmpv is an
  alternative). Transport bar, channel info, nicer scaling.
- **A/V sync tuning** — the audio-master sync works; buffer sizes / live
  `probesize` may need tuning per machine/network.
- **Real-time relay** — re-emit a clean stream (e.g. local UDP/RTP) for any player,
  re-inserting the dropped empty datagrams for strict timing.
- **Audio in the relay/poc** — currently audio is decrypted into recordings but the
  relay path is video-centric; a relay should carry both PIDs (it already does, as
  it passes all packets through).

## 5. Porting guide (if rewriting in another language)

The hard part (the crypto/selective-encryption logic) is ~50 lines and trivial to
port. Everything else is "use an existing library", never write it yourself:

| Need | Don't write it — use |
|------|----------------------|
| AES-128-ECB (single block) | any crypto lib (OpenSSL, libgcrypt, Rust `aes`, Go `crypto/aes`) |
| H.264 / MPEG audio decode | **libav/FFmpeg** (C API), GStreamer, or a hardware decoder |
| MPEG-TS demux | libav/FFmpeg, or parse 188-byte packets yourself (simple) |
| UDP multicast receive | OS sockets (join group on the right interface) |
| Audio output | OS audio API / SDL / miniaudio / PortAudio |
| Video display | SDL2, a GUI toolkit, or the platform's compositor |

Recommended native stack for a finished app: **C++ or Rust + FFmpeg libraries +
SDL2** (ffplay-style), or **libmpv** (feed it the decrypted TS via a custom stream
callback and it does demux+decode+A/V sync+output). Put the decryption between the
socket and the decoder; never write your own H.264 decoder.

## 6. Pointers

- `README.md` — overview and quick start.
- `docs/encryption.md` — the scheme in detail.
- `docs/firmware.md` — container format, key location, cross-version table.
- `docs/reversing.md` — the OpenRISC reverse (how the 8-packet guard was found).
- `CREDITS.md` — attributions (danman, smaeul, v3l0c1r4pt0r, UCL, FFmpeg, ...).

## 7. Open question recorded

Whether to ship the hardcoded key or require recovering it via `recover_key.py`.
Current choice: ship it (it is a public firmware constant, and the recovery tool is
included anyway). The repo contains no captured content or firmware. Keep the
"Responsible use" framing.
