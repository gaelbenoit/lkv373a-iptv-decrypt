"""Minimal, dependency-free helpers: read a pcapng file and pull out UDP payloads,
and iterate 188-byte MPEG-TS packets.

Only the subset needed for this project is implemented (pcapng Section Header /
Interface Description / Enhanced Packet blocks; Ethernet or raw IPv4; IPv4/UDP).
"""
import struct

TS_SIZE = 188
TS_SYNC = 0x47


def pcapng_packets(path):
    """Yield (linktype, frame_bytes) for each captured packet in a .pcapng file."""
    data = open(path, 'rb').read()
    off = 0
    endian = '<'
    links = []
    while off + 12 <= len(data):
        bt, bl = struct.unpack_from(endian + 'II', data, off)
        if bt == 0x0A0D0D0A:  # Section Header Block
            bom = struct.unpack_from('<I', data, off + 8)[0]
            endian = '<' if bom == 0x1A2B3C4D else '>'
            bt, bl = struct.unpack_from(endian + 'II', data, off)
            links = []
        elif bt == 1:  # Interface Description Block
            links.append(struct.unpack_from(endian + 'H', data, off + 8)[0])
        elif bt == 6:  # Enhanced Packet Block
            iface, _th, _tl, caplen, _orig = struct.unpack_from(endian + 'IIIII', data, off + 8)
            yield (links[iface] if iface < len(links) else 1), data[off + 28:off + 28 + caplen]
        if bl < 12:
            break
        off += bl


def udp_payload(linktype, frame, dst_port=5004):
    """Return the UDP payload of `frame` if it is IPv4/UDP to `dst_port`, else None.
    linktype 1 = Ethernet (strip 14-byte header); anything else is treated as raw IPv4."""
    ip = frame[14:] if linktype == 1 else frame
    if len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 17:  # IPv4 + proto UDP(17)
        return None
    ihl = (ip[0] & 15) * 4
    dp = struct.unpack_from('>H', ip, ihl + 2)[0]
    ulen = struct.unpack_from('>H', ip, ihl + 4)[0]
    if dst_port is not None and dp != dst_port:
        return None
    return bytes(ip[ihl + 8:ihl + ulen])


def iter_ts_packets(payload):
    """Split a UDP payload into aligned 188-byte TS packets (as memory slices)."""
    for i in range(0, len(payload) - TS_SIZE + 1, TS_SIZE):
        if payload[i] == TS_SYNC:
            yield payload[i:i + TS_SIZE]
