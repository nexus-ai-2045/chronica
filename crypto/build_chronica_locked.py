# -*- coding: utf-8 -*-
"""Chronica ビューアをパスフレーズ付き単一 HTML に暗号化する。

chronica.html + chronica-data.js を結合し、AES-GCM(256) で暗号化した
配布用ファイル chronica-locked.html を生成する。
鍵導出は PBKDF2-SHA256 310,000 回。パスフレーズはファイルに保存しない。

使い方:
  python build_chronica_locked.py            # パスフレーズを自動生成して表示
  python build_chronica_locked.py <合言葉>   # 指定した合言葉を使う
"""
import base64
import hashlib
import json
import os
import secrets
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
VIEWER = os.path.join(BASE, "chronica.html")
DATA = os.path.join(BASE, "chronica-data.js")
OUT = os.path.join(BASE, "chronica-locked.html")

PBKDF2_ITER = 310_000


def bundle_html() -> str:
    """ビューア HTML にデータ JS をインライン化した自己完結 HTML を返す。"""
    html = open(VIEWER, encoding="utf-8").read()
    data_js = open(DATA, encoding="utf-8").read()
    tag = '<script src="./%s"></script>' % os.path.basename(DATA)
    inline = "<script>\n" + data_js + "\n</script>"
    if tag in html:
        return html.replace(tag, inline)
    # src タグが見つからない場合は </head> 直前に挿入 (フォールバック)
    return html.replace("</head>", inline + "\n</head>")


def encrypt(plaintext: bytes, passphrase: str) -> str:
    """AES-256-GCM で暗号化し base64(salt|iv|ct) を返す。標準ライブラリのみ使用不可のため
    cryptography が無い環境では PBKDF2+AES を hashlib/hmac で組めないので、
    Python 3.13 標準の hashlib.pbkdf2_hmac + 外部無しの AES 実装は避け、
    cryptography パッケージを優先し、無ければ純粋実装へフォールバックする。"""
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(12)
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, PBKDF2_ITER, dklen=32)
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        ct = AESGCM(key).encrypt(iv, plaintext, None)
    except ImportError:
        ct = _aesgcm_encrypt_pure(key, iv, plaintext)
    return base64.b64encode(salt + iv + ct).decode("ascii")


def _aesgcm_encrypt_pure(key: bytes, iv: bytes, pt: bytes) -> bytes:
    """cryptography 不在時のフォールバック (純Python AES-GCM)。速度は遅いが1回きりの生成用。"""
    # --- AES-256 block cipher ---
    sbox = [0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
            0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
            0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
            0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
            0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
            0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
            0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
            0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
            0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
            0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
            0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
            0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
            0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
            0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
            0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
            0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16]
    rcon = [0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36,0x6c,0xd8,0xab,0x4d]

    def xt(a):
        a <<= 1
        if a & 0x100:
            a = (a ^ 0x1b) & 0xff
        return a

    def expand_key(k):
        nk = len(k) // 4
        w = [list(k[4*i:4*i+4]) for i in range(nk)]
        for i in range(nk, 4 * (nk + 7)):
            t = list(w[i-1])
            if i % nk == 0:
                t = t[1:] + t[:1]
                t = [sbox[b] for b in t]
                t[0] ^= rcon[i // nk - 1]
            elif nk > 6 and i % nk == 4:
                t = [sbox[b] for b in t]
            w.append([w[i-nk][j] ^ t[j] for j in range(4)])
        return w

    def encrypt_block(block, w, rounds=14):
        s = [list(block[i::4]) for i in range(4)]
        def add_rk(rd):
            for c in range(4):
                for r in range(4):
                    s[r][c] ^= w[rd*4+c][r]
        add_rk(0)
        for rd in range(1, rounds):
            for r in range(4):
                for c in range(4):
                    s[r][c] = sbox[s[r][c]]
            for r in range(1, 4):
                s[r] = s[r][r:] + s[r][:r]
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = xt(a[0]) ^ (xt(a[1]) ^ a[1]) ^ a[2] ^ a[3]
                s[1][c] = a[0] ^ xt(a[1]) ^ (xt(a[2]) ^ a[2]) ^ a[3]
                s[2][c] = a[0] ^ a[1] ^ xt(a[2]) ^ (xt(a[3]) ^ a[3])
                s[3][c] = (xt(a[0]) ^ a[0]) ^ a[1] ^ a[2] ^ xt(a[3])
            add_rk(rd)
        for r in range(4):
            for c in range(4):
                s[r][c] = sbox[s[r][c]]
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        add_rk(rounds)
        return bytes(s[r][c] for c in range(4) for r in range(4))

    w = expand_key(key)

    def aes(b):
        return encrypt_block(b, w)

    # --- GCM ---
    def gf_mult(x, y):
        z, v = 0, y
        for i in range(127, -1, -1):
            if (x >> i) & 1:
                z ^= v
            if v & 1:
                v = (v >> 1) ^ (0xe1 << 120)
            else:
                v >>= 1
        return z

    h = int.from_bytes(aes(b"\x00" * 16), "big")

    def ghash(data):
        y = 0
        for i in range(0, len(data), 16):
            blk = data[i:i+16].ljust(16, b"\x00")
            y = gf_mult(y ^ int.from_bytes(blk, "big"), h)
        return y

    def inc32(cb):
        n = int.from_bytes(cb[12:], "big")
        return cb[:12] + ((n + 1) & 0xffffffff).to_bytes(4, "big")

    j0 = iv + b"\x00\x00\x00\x01"
    cb = inc32(j0)
    ct = bytearray()
    for i in range(0, len(pt), 16):
        ks = aes(cb)
        chunk = pt[i:i+16]
        ct.extend(bytes(a ^ b for a, b in zip(chunk, ks)))
        cb = inc32(cb)
    lens = (0).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
    s = ghash(bytes(ct).ljust((len(ct) + 15) // 16 * 16, b"\x00") + lens)
    tag = bytes(a ^ b for a, b in zip(s.to_bytes(16, "big"), aes(j0)))
    return bytes(ct) + tag


LOCK_PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Chronica — 合言葉</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #0d0d0d; color: #fff;
    font-family: system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic UI", sans-serif;
  }}
  @media (prefers-color-scheme: light) {{ body {{ background: #f9f9f7; color: #0b0b0b; }} }}
  .card {{
    width: min(92vw, 380px); padding: 30px 28px; border-radius: 14px;
    background: rgba(128,128,128,0.07); border: 1px solid rgba(128,128,128,0.25);
  }}
  .mark {{ width: 26px; height: 26px; border-radius: 7px; background: #9085e9; position: relative; margin-bottom: 14px; }}
  .mark::after {{ content: ""; position: absolute; left: 5px; right: 5px; top: 12px; height: 2px; background: #0d0d0d; border-radius: 2px; }}
  h1 {{ font-size: 17px; margin: 0 0 4px; }}
  p {{ font-size: 12.5px; opacity: .65; margin: 0 0 18px; }}
  input {{
    width: 100%; box-sizing: border-box; font-size: 15px; padding: 10px 12px;
    border-radius: 8px; border: 1px solid rgba(128,128,128,0.4);
    background: transparent; color: inherit;
  }}
  button {{
    width: 100%; margin-top: 10px; padding: 10px; font-size: 14px; font-weight: 600;
    border-radius: 8px; border: none; background: #9085e9; color: #0d0d0d; cursor: pointer;
  }}
  #err {{ color: #e66767; font-size: 12.5px; min-height: 18px; margin-top: 8px; }}
</style>
</head>
<body>
<form class="card" id="f">
  <div class="mark" aria-hidden="true"></div>
  <h1>Chronica</h1>
  <p>このページは暗号化されています。共有された合言葉を入力してください。</p>
  <input type="password" id="pw" autocomplete="off" placeholder="合言葉" autofocus>
  <button type="submit">開く</button>
  <div id="err" role="alert"></div>
</form>
<script>
const PAYLOAD = "{payload}";
const ITER = {iter};
const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
document.getElementById("f").addEventListener("submit", async e => {{
  e.preventDefault();
  const err = document.getElementById("err");
  err.textContent = "";
  try {{
    const raw = b64(PAYLOAD);
    const salt = raw.slice(0, 16), iv = raw.slice(16, 28), ct = raw.slice(28);
    const pw = new TextEncoder().encode(document.getElementById("pw").value);
    const km = await crypto.subtle.importKey("raw", pw, "PBKDF2", false, ["deriveKey"]);
    const key = await crypto.subtle.deriveKey(
      {{ name: "PBKDF2", salt, iterations: ITER, hash: "SHA-256" }},
      km, {{ name: "AES-GCM", length: 256 }}, false, ["decrypt"]);
    const pt = await crypto.subtle.decrypt({{ name: "AES-GCM", iv }}, key, ct);
    const html = new TextDecoder().decode(pt);
    document.open(); document.write(html); document.close();
  }} catch (_) {{
    err.textContent = "合言葉が違います";
  }}
}});
</script>
</body>
</html>
"""


def main():
    # 使い方: build_chronica_locked.py [合言葉] [viewer.html] [data.js] [out.html]
    global VIEWER, DATA, OUT
    passphrase = sys.argv[1] if len(sys.argv) > 1 else secrets.token_urlsafe(12)
    if len(sys.argv) > 2: VIEWER = os.path.join(BASE, sys.argv[2])
    if len(sys.argv) > 3: DATA = os.path.join(BASE, sys.argv[3])
    if len(sys.argv) > 4: OUT = os.path.join(BASE, sys.argv[4])
    html = bundle_html()
    payload = encrypt(html.encode("utf-8"), passphrase)
    out = LOCK_PAGE.format(payload=payload, iter=PBKDF2_ITER)
    open(OUT, "w", encoding="utf-8").write(out)
    print("output:", OUT)
    print("size:", len(out), "bytes (payload", len(payload), ")")
    print("passphrase:", passphrase)


if __name__ == "__main__":
    main()
