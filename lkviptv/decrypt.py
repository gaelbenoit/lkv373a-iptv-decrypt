"""Core decryptor for the ITE / HDbitT (LKV373A family, "HHD264") encrypted
MPEG-TS stream.

Encryption scheme (recovered by firmware reverse engineering, see docs/):

  * Per video/audio frame, the encoder picks a random 16-byte nonce R.
  * The first TS packet of the frame (PUSI=1) carries, in its adaptation field,
    `transport_private_data` = AES-128-ECB-encrypt(KEY, R).
  * The payload is encrypted as `cipher = plain XOR R`, R tiled with period 16,
    the keystream phase reset to R[0] at the start of each TS packet payload.
  * SELECTIVE ENCRYPTION: only the first 8 TS packets of each frame are XORed;
    every following packet of that frame is sent in the clear.

Decryption therefore is:

  * R = AES-128-ECB-decrypt(KEY, transport_private_data)   (from the PUSI packet)
  * XOR only the first 8 packets of the frame with R (tiled, phase reset per packet)
  * leave packets 9.. untouched.

KEY is a factory constant baked into the encoder firmware, identical across the
whole product line. `tools/recover_key.py` reproduces its discovery from a capture
+ firmware; `tools/find_key.py` locates it inside a firmware image.
"""
from .aes import aes_decrypt_block

# Factory AES-128 key (constant in the LKV373A/HHD264 encoder firmware).
DEFAULT_KEY = bytes.fromhex('32451689c72c5ab3140364d3b3d42024')

TS_SIZE = 188
VIDEO_PID = 0x07D1
AUDIO_PID = 0x07D2
ENCRYPTED_PACKETS_PER_FRAME = 8  # the `l.sfgtui r24,7` guard in the firmware


def transport_private_data(pkt):
    """Return the 16-byte transport_private_data from a TS packet's adaptation
    field, or None if absent/too short."""
    if (pkt[3] >> 4) & 2 == 0:       # adaptation_field_control has no AF
        return None
    aflen = pkt[4]
    if aflen == 0:
        return None
    flags = pkt[5]
    o = 6
    if not (flags & 0x02):           # transport_private_data_flag
        return None
    if flags & 0x10:
        o += 6                       # PCR
    if flags & 0x08:
        o += 6                       # OPCR
    if flags & 0x04:
        o += 1                       # splice_countdown
    pdlen = pkt[o]
    o += 1
    if pdlen < 16:
        return None
    return bytes(pkt[o:o + 16])


def payload_offset(pkt):
    """Byte offset where the TS payload starts (after header + adaptation field)."""
    afc = (pkt[3] >> 4) & 3
    if afc & 2:
        return 5 + pkt[4]
    return 4


class StreamDecryptor:
    """Stateful decryptor: feed it raw 188-byte TS packets in order, get clear ones.

    Handles video (0x7D1) and audio (0x7D2); all other PIDs (PAT/PMT/NIT/SDT/null)
    pass through unchanged. Keeps per-PID state (current R + packet index), so it
    works on a live stream as well as on a capture.
    """

    def __init__(self, key: bytes = DEFAULT_KEY, pids=(VIDEO_PID, AUDIO_PID),
                 encrypted_packets=ENCRYPTED_PACKETS_PER_FRAME):
        self.key = key
        self.pids = set(pids)
        self.nenc = encrypted_packets
        self._R = {pid: None for pid in self.pids}      # current keystream block
        self._idx = {pid: 0 for pid in self.pids}       # packet index within frame
        self.stats = {'packets': 0, 'decrypted': 0, 'frames': 0}

    def process_packet(self, pkt) -> bytes:
        self.stats['packets'] += 1
        if len(pkt) != TS_SIZE or pkt[0] != 0x47:
            return bytes(pkt)
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid not in self.pids:
            return bytes(pkt)
        pusi = (pkt[1] >> 6) & 1
        if pusi:
            pd = transport_private_data(pkt)
            self._R[pid] = aes_decrypt_block(self.key, pd) if pd else None
            self._idx[pid] = 0
            if pd:
                self.stats['frames'] += 1

        R = self._R[pid]
        idx = self._idx[pid]
        out = bytes(pkt)
        if R is not None and idx < self.nenc:
            o = payload_offset(pkt)
            if o < TS_SIZE:
                buf = bytearray(pkt)
                for j in range(o, TS_SIZE):
                    buf[j] ^= R[(j - o) & 15]
                out = bytes(buf)
                self.stats['decrypted'] += 1
        self._idx[pid] = idx + 1
        return out

    def process_payload(self, udp_payload) -> bytes:
        """Decrypt all aligned TS packets inside one UDP datagram payload."""
        out = bytearray()
        n = len(udp_payload)
        i = 0
        while i + TS_SIZE <= n:
            out += self.process_packet(udp_payload[i:i + TS_SIZE])
            i += TS_SIZE
        if i < n:                      # trailing bytes (shouldn't happen on this stream)
            out += bytes(udp_payload[i:])
        return bytes(out)
