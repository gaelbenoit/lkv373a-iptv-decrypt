"""Décompresseur SMAZ (ITE SMEDIA02) : blocs UCL NRV2E 8 bits, en pur Python.
usage: python smaz.py <fichier> <offset_SMAZ_hex> <sortie>
"""
import struct, sys

def nrv2e_d8(src, ilen=0):
    dst = bytearray()
    bb = 0
    last_m_off = 1

    def getbit():
        nonlocal bb, ilen
        if bb & 0x7f:
            bb = (bb * 2) & 0x1ff
        else:
            bb = src[ilen] * 2 + 1
            ilen += 1
        return (bb >> 8) & 1

    while True:
        while getbit():
            dst.append(src[ilen]); ilen += 1
        m_off = 1
        while True:
            m_off = m_off * 2 + getbit()
            if getbit():
                break
            m_off = (m_off - 1) * 2 + getbit()
        if m_off == 2:
            m_off = last_m_off
            m_len = getbit()
        else:
            m_off = ((m_off - 3) * 256 + src[ilen]) & 0xffffffff; ilen += 1
            if m_off == 0xffffffff:
                break
            m_len = (m_off ^ 0xffffffff) & 1
            m_off >>= 1
            m_off += 1
            last_m_off = m_off
        if m_len:
            m_len = 1 + getbit()
        elif getbit():
            m_len = 3 + getbit()
        else:
            m_len += 1
            while True:
                m_len = m_len * 2 + getbit()
                if getbit():
                    break
            m_len += 3
        m_len += (m_off > 0x500)
        if m_off > len(dst):
            raise ValueError(f'offset {m_off} hors tampon ({len(dst)}) à ilen={ilen}')
        p = len(dst) - m_off
        for _ in range(m_len + 1):
            dst.append(dst[p]); p += 1
    return bytes(dst), ilen

def smaz(data, off):
    magic, u1, u2, size = struct.unpack_from('>4sIII', data, off)
    assert magic == b'SMAZ', magic
    print(f'SMAZ @0x{off:x} u1=0x{u1:x} u2=0x{u2:x} taille=0x{size:x}', file=sys.stderr)
    pos = off + 16
    out = bytearray()
    n = 0
    while True:
        chunk, used = nrv2e_d8(data[pos:pos + size])
        out += chunk; n += 1
        print(f'  bloc {n}: 0x{size:x} -> 0x{len(chunk):x} (consommé 0x{used:x})', file=sys.stderr)
        pos += size
        flags, = struct.unpack_from('>I', data, pos); pos += 4
        if flags == 0:
            break
        size, = struct.unpack_from('>I', data, pos); pos += 4
    print(f'total 0x{len(out):x} octets, fin à 0x{pos:x}', file=sys.stderr)
    return bytes(out)

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    out = smaz(d, int(sys.argv[2], 16))
    open(sys.argv[3], 'wb').write(out)
