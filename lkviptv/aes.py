"""AES-128 ECB in pure Python (no dependency), with a pycryptodome fast path.

Only the single-block primitives are needed here:
  * aes_decrypt_block(key16, block16) -> 16 bytes   (used for decryption)
  * aes_encrypt_block(key16, block16) -> 16 bytes   (used to verify / re-encrypt)

If `pycryptodome` is installed it is used transparently (much faster); otherwise
the pure-Python implementation below is used. The pure version is correct but slow
(~fine for stream decryption, slow for the 2M-window key brute force -> install
pycryptodome for `tools/recover_key.py`).
"""

try:
    from Crypto.Cipher import AES as _AES  # pycryptodome

    def aes_decrypt_block(key: bytes, block: bytes) -> bytes:
        return _AES.new(key, _AES.MODE_ECB).decrypt(block)

    def aes_encrypt_block(key: bytes, block: bytes) -> bytes:
        return _AES.new(key, _AES.MODE_ECB).encrypt(block)

    HAVE_PYCRYPTODOME = True
except Exception:  # pragma: no cover - fallback path
    HAVE_PYCRYPTODOME = False

    _sbox = []
    _inv = []

    def _init_sbox():
        p = q = 1
        sb = [0] * 256
        sb[0] = 0x63
        while True:
            p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
            q ^= q << 1
            q ^= q << 2
            q ^= q << 4
            q &= 0xFF
            if q & 0x80:
                q ^= 0x09
            x = (q ^ ((q << 1) | (q >> 7)) & 0xFF ^ ((q << 2) | (q >> 6)) & 0xFF
                 ^ ((q << 3) | (q >> 5)) & 0xFF ^ ((q << 4) | (q >> 4)) & 0xFF)
            sb[p] = (x ^ 0x63) & 0xFF
            if p == 1:
                break
        _sbox[:] = sb
        inv = [0] * 256
        for i, v in enumerate(sb):
            inv[v] = i
        _inv[:] = inv

    _init_sbox()
    _rcon = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]

    def _xtime(a):
        return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1)

    def _mul(a, b):
        r = 0
        for _ in range(8):
            if b & 1:
                r ^= a
            hi = a & 0x80
            a = (a << 1) & 0xFF
            if hi:
                a ^= 0x1B
            b >>= 1
        return r

    def _expand(key):
        w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
        for i in range(4, 44):
            t = list(w[i - 1])
            if i % 4 == 0:
                t = t[1:] + t[:1]
                t = [_sbox[b] for b in t]
                t[0] ^= _rcon[i // 4 - 1]
            w.append([w[i - 4][j] ^ t[j] for j in range(4)])
        return w

    def _addrk(s, w, r):
        for c in range(4):
            for row in range(4):
                s[row][c] ^= w[r * 4 + c][row]

    def aes_decrypt_block(key: bytes, blk: bytes) -> bytes:
        w = _expand(key)
        s = [[blk[r + 4 * c] for c in range(4)] for r in range(4)]
        _addrk(s, w, 10)
        for rnd in range(9, 0, -1):
            for row in range(1, 4):
                s[row] = s[row][-row:] + s[row][:-row]
            for r in range(4):
                for c in range(4):
                    s[r][c] = _inv[s[r][c]]
            _addrk(s, w, rnd)
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _mul(a[0], 14) ^ _mul(a[1], 11) ^ _mul(a[2], 13) ^ _mul(a[3], 9)
                s[1][c] = _mul(a[0], 9) ^ _mul(a[1], 14) ^ _mul(a[2], 11) ^ _mul(a[3], 13)
                s[2][c] = _mul(a[0], 13) ^ _mul(a[1], 9) ^ _mul(a[2], 14) ^ _mul(a[3], 11)
                s[3][c] = _mul(a[0], 11) ^ _mul(a[1], 13) ^ _mul(a[2], 9) ^ _mul(a[3], 14)
        for row in range(1, 4):
            s[row] = s[row][-row:] + s[row][:-row]
        for r in range(4):
            for c in range(4):
                s[r][c] = _inv[s[r][c]]
        _addrk(s, w, 0)
        return bytes(s[r][c] for c in range(4) for r in range(4))

    def aes_encrypt_block(key: bytes, blk: bytes) -> bytes:
        w = _expand(key)
        s = [[blk[r + 4 * c] for c in range(4)] for r in range(4)]
        _addrk(s, w, 0)
        for rnd in range(1, 10):
            for r in range(4):
                for c in range(4):
                    s[r][c] = _sbox[s[r][c]]
            for row in range(1, 4):
                s[row] = s[row][row:] + s[row][:row]
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _xtime(a[0]) ^ (_xtime(a[1]) ^ a[1]) ^ a[2] ^ a[3]
                s[1][c] = a[0] ^ _xtime(a[1]) ^ (_xtime(a[2]) ^ a[2]) ^ a[3]
                s[2][c] = a[0] ^ a[1] ^ _xtime(a[2]) ^ (_xtime(a[3]) ^ a[3])
                s[3][c] = (_xtime(a[0]) ^ a[0]) ^ a[1] ^ a[2] ^ _xtime(a[3])
            _addrk(s, w, rnd)
        for r in range(4):
            for c in range(4):
                s[r][c] = _sbox[s[r][c]]
        for row in range(1, 4):
            s[row] = s[row][row:] + s[row][:row]
        _addrk(s, w, 10)
        return bytes(s[r][c] for c in range(4) for r in range(4))


def _selftest():
    k = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
    pt = bytes.fromhex('00112233445566778899aabbccddeeff')
    ct = bytes.fromhex('69c4e0d86a7b0430d8cdb78070b4c55a')
    assert aes_encrypt_block(k, pt) == ct
    assert aes_decrypt_block(k, ct) == pt


_selftest()
