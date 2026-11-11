# The encryption scheme

## Stream overview

The encoder emits a raw UDP multicast MPEG-TS stream:

* multicast group / port: typically `239.255.42.42:5004`
* each UDP datagram = 7 TS packets of 188 bytes (1316-byte datagrams)
* PIDs:

  | PID | content |
  |-----|---------|
  | `0x0000` | PAT |
  | `0x1000` | PMT (PCR carried on `0x07D1`) |
  | `0x07D1` | **video**, H.264 (stream type `0x1B`) |
  | `0x07D2` | **audio**, MPEG (stream type `0x04`) |
  | `0x1FFF` | null / stuffing |

* known quirk: ~1/4 of the UDP datagrams are **empty**; naive readers think the
  stream ended. Players tolerate it; a strict real-time relay should re-insert them.

Only the **video and audio payloads** are scrambled. PSI (PAT/PMT/…) and null
packets are already clear.

## Keystream

Per frame, the encoder draws a random 16-byte nonce `R`. It is never sent in the
clear. Instead the **first TS packet of the frame** (payload-unit-start-indicator
`PUSI = 1`) carries, inside its adaptation field, a 16-byte field
`transport_private_data`:

```
transport_private_data = AES-128-ECB-encrypt(KEY, R)
```

The payload is then scrambled with a plain byte-wise XOR against `R`, repeated with
a period of 16 bytes, the phase reset to `R[0]` at the start of each TS packet's
payload:

```
cipher[n] = plain[n] XOR R[(n - payload_start) mod 16]
```

`KEY` is a 16-byte factory constant (see [`firmware.md`](firmware.md)):

```
KEY = 32451689c72c5ab3140364d3b3d42024
```

## Selective encryption — the key subtlety

The encoder only scrambles the **first 8 TS packets of each frame**. Every packet
after that is emitted **in the clear**. This is enforced in the firmware by a
single comparison (`if packet_index > 7: skip the XOR`; see
[`reversing.md`](reversing.md)).

Consequence for decryption: you must XOR **only the first 8 packets** of each
frame. XOR-ing all packets re-scrambles the clear tail and destroys the picture
from the top few macroblock rows onward — the trap that made this stream look
much harder than it is.

## Decryption algorithm

```
for each TS packet in arrival order:
    if PID not in {0x07D1, 0x07D2}:        # PSI, null, etc.
        emit unchanged
    else:
        if PUSI == 1:                      # first packet of a frame
            pd = transport_private_data(packet)      # 16 bytes from the AF
            R  = AES-128-ECB-decrypt(KEY, pd)
            packet_index = 0
        if R is known and packet_index < 8:
            o = payload_offset(packet)               # after header + AF
            for n in [o, 188):
                packet[n] ^= R[(n - o) mod 16]
        packet_index += 1
        emit packet
```

Video and audio are tracked independently (separate `R` and `packet_index`).

This is exactly what [`lkviptv/decrypt.py`](../lkviptv/decrypt.py) implements.

## Known-plaintext anchor

The scheme was first confirmed (and the key recovered — see
[`../tools/recover_key.py`](../tools/recover_key.py)) using the known clear start
of every video PES payload:

```
00 00 01 E0 ?? ?? 84 80 05 <PTS:5 bytes> 00 00 00 01 09 50 ...
└─ PES start code + stream_id ┘           └ H.264 AUD ┘
```

So 9 of the first 16 keystream bytes are known per frame (positions 0–3, 6–8,
14–15), which uniquely identifies the key among all 16-byte windows of the
firmware.

## Verification

Decrypting a real 82-frame capture with this algorithm yields a clean H.264
stream: all key frames decode with **0 macroblock errors** (a single MB error
across the whole stream corresponds to one lost UDP datagram in the capture, not
a decryption fault). The decoded picture is pixel-accurate.
