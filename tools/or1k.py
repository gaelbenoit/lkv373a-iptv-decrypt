"""Désassembleur OpenRISC or1k minimal (32 bits, big-endian) + recherche de xrefs.
Le firmware jedi.bin est chargé à l'adresse BASE en mémoire.
"""
import struct, sys

BASE = 0x80100

def s(v, bits):
    m = 1 << (bits - 1)
    return (v ^ m) - m

def dis(w, pc):
    op = w >> 26
    D = (w >> 21) & 0x1f
    A = (w >> 16) & 0x1f
    B = (w >> 11) & 0x1f
    i16 = w & 0xffff
    si = s(i16, 16)
    def tgt():
        return (pc + (s(w & 0x3ffffff, 26) << 2)) & 0xffffffff
    if op == 0x00: return f"l.j      0x{tgt():x}", tgt()
    if op == 0x01: return f"l.jal    0x{tgt():x}", None
    if op == 0x03: return f"l.bnf    0x{tgt():x}", None
    if op == 0x04: return f"l.bf     0x{tgt():x}", None
    if op == 0x05: return "l.nop", None
    if op == 0x06:
        if w & 0x10000: return f"l.macrc  r{D}", None
        return f"l.movhi  r{D},0x{i16:x}", None
    if op == 0x08: return f"l.sys    0x{w&0xffff:x}", None
    if op == 0x09: return "l.rfe", None
    if op == 0x11: return f"l.jr     r{B}", None
    if op == 0x12: return f"l.jalr   r{B}", None
    if op == 0x1a: return f"l.lf     ...", None
    if op == 0x1b: return f"l.lwa    r{D},{si}(r{A})", None
    if op == 0x21: return f"l.lwz    r{D},{si}(r{A})", None
    if op == 0x22: return f"l.lws    r{D},{si}(r{A})", None
    if op == 0x23: return f"l.lbz    r{D},{si}(r{A})", None
    if op == 0x24: return f"l.lbs    r{D},{si}(r{A})", None
    if op == 0x25: return f"l.lhz    r{D},{si}(r{A})", None
    if op == 0x26: return f"l.lhs    r{D},{si}(r{A})", None
    if op == 0x27: return f"l.addi   r{D},r{A},{si}", None
    if op == 0x28: return f"l.addic  r{D},r{A},{si}", None
    if op == 0x29: return f"l.andi   r{D},r{A},0x{i16:x}", None
    if op == 0x2a: return f"l.ori    r{D},r{A},0x{i16:x}", None
    if op == 0x2b: return f"l.xori   r{D},r{A},{si}", None
    if op == 0x2c: return f"l.muli   r{D},r{A},{si}", None
    if op == 0x2d: return f"l.mfspr  r{D},r{A},0x{i16:x}", None
    if op == 0x2e:
        sh = w & 0x3f; t = (w >> 6) & 3
        nm = {0:'l.slli',1:'l.srli',2:'l.srai',3:'l.rori'}.get(t,'l.sh?')
        return f"{nm}  r{D},r{A},{sh}", None
    if op == 0x2f:
        cc = {0:'eq',1:'ne',2:'gtu',3:'geu',4:'ltu',5:'leu',10:'gts',11:'ges',12:'lts',13:'les'}.get(D,f'?{D}')
        return f"l.sf{cc}i r{A},{si}", None
    if op == 0x30:
        imm = ((w >> 10) & 0xf800) | (w & 0x7ff)
        return f"l.mtspr  r{A},r{B},0x{imm:x}", None
    if op == 0x35:
        imm = s(((w >> 10) & 0xf800) | (w & 0x7ff), 16)
        return f"l.sw     {imm}(r{A}),r{B}", None
    if op == 0x36:
        imm = s(((w >> 10) & 0xf800) | (w & 0x7ff), 16)
        return f"l.sb     {imm}(r{A}),r{B}", None
    if op == 0x37:
        imm = s(((w >> 10) & 0xf800) | (w & 0x7ff), 16)
        return f"l.sh     {imm}(r{A}),r{B}", None
    if op == 0x38:
        o = w & 0xf; o2 = (w >> 8) & 0x3; shf = (w >> 6) & 0x3
        base = {0:'l.add',1:'l.addc',2:'l.sub',3:'l.and',4:'l.or',5:'l.xor',
                0xe:'l.cmov',0xf:'l.ff1'}
        if o == 8:
            nm = {0:'l.sll',1:'l.srl',2:'l.sra',3:'l.ror'}[shf]
        elif o2 == 3 and o in (6,9,0xa,0xb):
            nm = {6:'l.mul',9:'l.div',0xa:'l.divu',0xb:'l.mulu'}[o]
        else:
            nm = base.get(o)
        if nm: return f"{nm:8} r{D},r{A},r{B}", None
        return f"l.alu.{o:x}.{o2} r{D},r{A},r{B}", None
    if op == 0x39:
        cc = {0:'eq',1:'ne',2:'gtu',3:'geu',4:'ltu',5:'leu',10:'gts',11:'ges',12:'lts',13:'les'}.get(D,f'?{D}')
        return f"l.sf{cc}  r{A},r{B}", None
    return f".word 0x{w:08x}", None

def load(path):
    return open(path, 'rb').read()

def disrange(data, start, end):
    out = []
    for a in range(start, end, 4):
        w = struct.unpack_from('>I', data, a - BASE)[0]
        txt, _ = dis(w, a)
        out.append(f"{a:06x}: {w:08x}  {txt}")
    return '\n'.join(out)

def find_xrefs(data, target):
    """Trouve movhi/ori|addi construisant l'adresse target."""
    hi = (target >> 16) & 0xffff
    lo = target & 0xffff
    # l'addi/lwz utilisent un imm signé : si lo>=0x8000, hi doit valoir hi+1
    refs = []
    n = len(data) // 4
    movhi = {}  # reg -> (addr, imm)
    for i in range(n):
        a = BASE + i * 4
        w = struct.unpack_from('>I', data, i * 4)[0]
        op = w >> 26
        D = (w >> 21) & 0x1f; A = (w >> 16) & 0x1f; i16 = w & 0xffff
        if op == 0x06 and not (w & 0x10000):
            movhi[D] = (a, i16)
        elif op == 0x2a:  # ori rD,rA,imm
            if A in movhi and movhi[A][1] == hi and i16 == lo:
                refs.append((movhi[A][0], a))
        elif op in (0x27, 0x21, 0x23, 0x25, 0x35, 0x36):  # addi/lwz/lbz/lhz/sw/sb (imm signé)
            himatch = hi if lo < 0x8000 else (hi + 1) & 0xffff
            if A in movhi and movhi[A][1] == himatch and (i16 & 0xffff) == lo:
                refs.append((movhi[A][0], a))
    return refs

if __name__ == '__main__':
    data = load(sys.argv[1])
    cmd = sys.argv[2]
    if cmd == 'dis':
        print(disrange(data, int(sys.argv[3], 16), int(sys.argv[4], 16)))
    elif cmd == 'xref':
        for m, u in find_xrefs(data, int(sys.argv[3], 16)):
            print(f"movhi@{m:x} use@{u:x}")
