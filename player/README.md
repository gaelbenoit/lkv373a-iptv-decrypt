# Standalone player (v1)

A self-contained live player: multicast → decrypt (`lkviptv`) → decode (PyAV /
libav, in-process) → OpenCV window. No external ffmpeg or VLC process.

```bash
pip install -r player/requirements.txt
python player/player.py --iface 192.168.1.50        # live
python player/player.py --iface 192.168.1.50 --record out.ts
python player/player.py --file clear.ts             # offline test (no device)
```

`--iface` is the IP of the local NIC that sees the encoder; set it on multi-homed
machines. Keys: `q` / `Esc` to quit.

## How it works

`DecryptingMulticastSource` is a readable file-like object. A background thread
joins the group, decrypts each datagram with `lkviptv.StreamDecryptor`, and
appends the clear MPEG-TS bytes to a bounded buffer. PyAV opens that object as an
`mpegts` input and decodes H.264; frames are shown with OpenCV. libav is linked
in-process (via PyAV), so the whole thing is one Python application.

## Scope / limitations (v1)

* **Video only** — audio (MPEG, PID `0x7D2`) is decrypted into the recorded `.ts`
  but not played; A/V output + sync is the next step.
* Display is a plain OpenCV window (no transport controls yet).
* Timing follows decode order; a proper jitter buffer / PTS pacing can be added.

A future version can swap the OpenCV window for an embedded Qt or libmpv surface
and add audio — the network/decrypt/decode core stays the same.
