# Reverse engineering notes (OpenRISC encoder firmware)

How the scheme in [`encryption.md`](encryption.md) was extracted from the encoder
firmware. The goal here is reproducibility, not a full teardown.

## Setup

* Decompress the firmware (`tools/smaz.py`) to get the raw encoder image
  (`jedi.bin`-type, ~2.4 MB).
* CPU: **OpenRISC `or1k`, 32-bit, big-endian**, image base `0x80100`
  (memory address = file offset + `0x80100`).
* Disassembler: [`tools/or1k.py`](../tools/or1k.py) (pure Python), or Ghidra with
  the `smaeul/ghidra-or1k` big-endian/delay-slot processor module.

```bash
python tools/or1k.py jedi.bin dis 0x190b6c 0x191400   # packetizer / cipher
python tools/or1k.py jedi.bin dis 0x1915ec 0x191760   # payload XOR routine
python tools/or1k.py jedi.bin xref 0x1fe170           # refs to the key constant
```

## Relevant functions

| address (mem) | role |
|---------------|------|
| `0x190b6c` | packetizer + cipher driver (builds TS packets, applies encryption) |
| `0x1915ec` | XOR payload with a 16-byte key block, tiled period 16 |
| `0x191b78` | `mbedtls_aes_setkey_enc` |
| `0x192ffc` | `mbedtls_aes_crypt_cbc` (used with IV=0, one block ⇒ ECB) |
| `0x1a4eec` | random/fill helper (generates the nonce R) |

Encryption state lives in a per-stream context: `ctx[0x4fc]` is the
encryption-enabled flag, and the AES key is copied into the context at `+0x50A`.

## The nonce and transport_private_data

In `0x190b6c`, when encryption is enabled, the encoder:

1. fills a 16-byte stack buffer with random bytes → the nonce `R`
   (`l.jal 0x1a4eec`);
2. `mbedtls_aes_setkey_enc(ctx, key=ctx+0x50A, 128)` then
   `mbedtls_aes_crypt_cbc(ctx, ENCRYPT, 16, IV=0, R)` → `AES-ECB(key, R)`, which is
   written into the packet's adaptation field as `transport_private_data`;
3. XORs the payload with `R` via `0x1915ec`.

`0x1915ec` loads the 16 key-block bytes once, then XORs the payload 16 bytes at a
time — i.e. a fixed period-16 keystream, phase reset per call (per packet).

## The selective-encryption guard (the crux)

Inside the per-packet emission loop of `0x190b6c`, right before the XOR call:

```
191118: l.lwz    r3,1276(r2)   ; r3 = ctx[0x4fc]  (encryption enabled?)
19111c: l.sfeq   r3,r0
191120: l.bf     0x191140      ; if disabled -> skip XOR
191124: l.sfgtui r24,7         ; if packet_index (r24) > 7
191128: l.bf     0x191140      ; -> SKIP the XOR   <-- selective encryption
19112c: l.addi   r28,r24,1     ; else packet_index + 1
191130: l.addi   r3,r1,36      ; R (stack nonce)
191138: l.jal    0x1915ec      ; XOR(R, payload, 184)
```

`r24` is the per-frame packet counter: it is reset to 0 at the PES-header packet
(`0x190e48`), the first packet is XORed at `0x190eac` (setting `r24 = 1`), and the
loop keeps XOR-ing while `r24 <= 7`. So exactly **8 packets per frame** are
encrypted; the rest are emitted untouched.

This single `l.sfgtui r24,7` is the whole reason the stream resisted earlier
attempts: anyone assuming full-frame encryption decrypts the first 8 packets
correctly and then re-corrupts the clear remainder, which looks like a broken /
re-keyed keystream rather than "stop decrypting here".
