# Firmware: container format and key location

## Where the key lives

The AES key is a **constant embedded in the encoder firmware** (and, on receivers,
in the decoder firmware). It is the same 16 bytes on every revision of the product
line, so any one firmware image is enough to obtain it.

```
KEY = 32451689c72c5ab3140364d3b3d42024
```

In the decompressed encoder image it sits in a constants table, framed like this
(the trailing `04 c1 1d b7` is the CRC-32 polynomial that follows in the table):

```
... ff ff ff ff  de 70 00 00  de 60 00 00  00 00 00 01
    32 45 16 89  c7 2c 5a b3  14 03 64 d3  b3 d4 20 24     <- KEY
    00 00 00 01  00 00 00 00  04 c1 1d b7 ...
```

## Container format

The vendor firmware files (`.bin` for TX, `.PKG` / `.ROM` for RX) are **packed**
containers, so the key is not visible in the raw file — you must decompress first.

* Outer wrapper: `SMEDIA02` (ITE chips); RX packages add an `ITEPKG03` wrapper.
* Payload: a chain of **`SMAZ`** chunks, each compressed with **UCL NRV2E, 8-bit
  variant**. After each decompressed chunk a 32-bit flag (≠0 ⇒ another chunk
  follows, with its size; 0 ⇒ end).

[`tools/smaz.py`](../tools/smaz.py) is a clean-room, pure-Python decompressor for
this format. [`tools/find_key.py`](../tools/find_key.py) and
[`tools/recover_key.py`](../tools/recover_key.py) call it automatically — just pass
the packed container.

```bash
# locate the key in a packed TX firmware
python tools/find_key.py LKV373A_TX_ENCODE_20160722.bin

# recover it from scratch (capture + firmware), no key assumed
python tools/recover_key.py capture.pcapng LKV373A_TX_ENCODE_20160722.bin
```

## Cross-version confirmation

Same key, byte-for-byte, in every image checked. The absolute offset moves between
versions only because the code size differs.

**Encoder (TX) firmware — SMAZ @ `0x200b48`:**

| firmware | key offset (decompressed) |
|----------|---------------------------|
| LKV373A_TX_20151028            | 0x1f851c |
| LKV373A_TX_V3.0_20151130       | 0x1f851c |
| LKV373A_TX_V3.0b_20160218      | 0x1fe8c8 |
| LKV373A_TX_ENCODE_20160722     | 0x1fe8d4 |
| TX_V1.3c_20161104              | 0x1fe5f8 |
| LKV373A_TX_V3.0c_d_20161116    | 0x1fe5f8 |

**Decoder (RX) firmware — ITEPKG03 → SMAZ @ ~`0xbb0`:** key present in the
`LKV373A_RX` / `RX_V1.3c` line (2015–2016), stored in a key record prefixed
`40 06 00 00 00 f9 15 00`. (The later "IPTV_RX" v0.5 / "IPTV_MCU_RX" v0.2.4
variants are a different product line and do not carry the key in firmware.)

## CPU / image details (for the reverse)

* Encoder (`jedi`-type image): **OpenRISC `or1k`, big-endian**, mapped at base
  `0x80100` (so a file offset `f` is memory address `f + 0x80100`).
* The AES is software **mbedTLS**, not a hardware crypto block (despite the "DPU"
  and "jiami chip" strings present in the firmware — those serve other functions).

See [`reversing.md`](reversing.md) for how the scheme was extracted from this code.
