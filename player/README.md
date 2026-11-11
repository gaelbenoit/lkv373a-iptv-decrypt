# Standalone player

A self-contained live player: multicast → decrypt (`lkviptv`) → decode **video +
audio** (PyAV / libav, in-process) → audio out via sounddevice, with
**audio-master A/V sync** (the ffplay model). No external ffmpeg or VLC process.

Two front-ends share the same engine:

* **`player.py`** — minimal, OpenCV window (lightest deps).
* **`player_qt.py`** — Qt UI with a transport bar (play/pause, mute, volume
  slider, fullscreen) and status line; needs `pip install PySide6`.

```bash
pip install -r player/requirements.txt
# minimal (OpenCV window)
python player/player.py    --iface 192.168.1.50
# Qt UI
pip install PySide6
python player/player_qt.py --iface 192.168.1.50
# common options (both):
#   --record out.ts     also save the clear stream
#   --file clear.ts     play an existing clear .ts (offline test, no device)
#   --no-audio          video only
```

On Windows run with `py` (not `python`) so you use the interpreter the packages
are installed in — see Troubleshooting below.

`--iface` is the IP of the local NIC that sees the encoder; set it on multi-homed
machines.

Keys: `q`/`Esc` quit · `space` pause · `m` mute · `f` fullscreen · `+`/`-` volume.

## How it works

Three threads around the clear MPEG-TS stream:

* **decode** — PyAV demuxes the TS and decodes H.264 video (→ BGR frames) and MPEG
  audio (→ resampled to 48 kHz / 16-bit stereo), pushing both into bounded queues;
* **audio** — writes PCM to a `sounddevice` output stream; this is the **master
  clock** (seconds of audio actually played);
* **display** (main thread) — shows each video frame when the master clock reaches
  its PTS, dropping frames if it falls behind, and handles the keyboard.

libav is linked in-process via PyAV, so the whole thing is one Python application.

Audio format on this stream: MPEG-1 Layer II, 48 kHz, stereo.

## Tested vs. needs a live run

* **Verified headless:** the full decode + audio-resample path (PyAV demux of both
  streams, H.264 → BGR, MPEG audio → int16/48k stereo, PTS/clock arithmetic).
* **Needs your machine (not testable without the device + display + speakers):**
  actual audio output, the window, and live multicast reception. If A/V drift
  appears on your setup, the knobs are the queue sizes and the live `probesize` /
  buffer; say the word and we tune them.

## Troubleshooting

**`ModuleNotFoundError: No module named 'av'` even after installing the
requirements** — you installed into one Python but are running another. This is
common on Windows when a venv (or the Microsoft Store Python) comes first on
`PATH`. Run the player with the *same* interpreter you installed into. The Windows
`py` launcher targets your default real Python:

```bash
py -c "import av, cv2, sounddevice; print('OK')"      # must print OK
py player/player.py --iface <your-NIC-ip>
```

Or call the interpreter explicitly, e.g.
`C:\...\Python312\python.exe player\player.py ...`. Using `python -m pip install
-r player/requirements.txt` (instead of a bare `pip`) also guarantees the install
lands in the interpreter you then run.

## Status

The Qt front-end (`player_qt.py`) was validated headless (imports, window
construction, signal wiring, controls, frame display). Live audio output, the
on-screen window and multicast reception need a run on your machine. Possible
further polish: an embedded libmpv surface, channel/EPG info, a seek-in-buffer
bar.
