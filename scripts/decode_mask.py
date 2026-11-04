#!/usr/bin/env python3
"""Reference decoder, polygon tracer and consistency checker for SAM 3.1 masks.

Stdlib only. Run with:  python3 -I decode_mask.py --selftest
                        python3 -I decode_mask.py --verify docs/data/sam_masks.json

WHAT IS DOCUMENTED AND WHAT IS NOT (read before trusting this file)

Official docs (https://dev.meta.ai/docs/sam/reading-segmentation and the public
protocol page https://github.com/meta-models/meta-sam/blob/main/protocol/sam3.md)
specify: tag grammar, base85 alphabet, "H,W,<marker><payload>", marker "~" =
lossless and "!" = one_bit, a 5-character base85 length prefix, row-major
output, mask x/y always 0, the mask shares the record's box bounds (x2/y2
inclusive), lossless threshold 129. The docs never say that H,W equal the box
size: 11 of the 12 legacy records do, SAR-TGT-007 does not (mask 188x1024, inclusive
box 189x1027). verify_sam_masks() therefore accepts a mismatch only when the file
declares mask.covers = "dimension_mismatch_VERIFY" and draws no polygon for it.

The docs do NOT specify the arithmetic coder inside the payload. The coder below
(carry-less range coder, adaptive context models) was reconstructed from a
model-mediated summary of upstream meta_sam_parser/_mask_codec.py, not from a
byte-exact reading of that file. Every constant marked [VERIFY] could not be
confirmed against official prose. Evidence available when this was written:
  - two real captured lossless vectors from Meta's conformance suite
    (2x3 and 11x11) must decode to the published rasters (see --selftest);
  - the one_bit vector printed in the protocol page (5x5) must decode and pass
    the canonical re-encode check;
  - the length prefix of the 12 legacy payloads matches their stored length.
Count-halving thresholds are NOT exercised by those vectors. Production code
MUST treat meta_sam_parser.decode_mask_to_raster as authoritative and use this
module only as a cross-check (see verify_against_reference()).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Errors and constants
# ---------------------------------------------------------------------------


class MaskDecodeError(ValueError):
    """Invalid or unsupported mask payload (mirrors invalid_mask_payload)."""


# Documented alphabet: printable ASCII 0x21..0x7E minus the delimiters
# " \ , ; < > |  -> 87 symbols, the first 85 are digits 0..84.
_DELIMS = set('"\\,;<>|')
ALPHABET = "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) not in _DELIMS)[:85]
DOCUMENTED_ALPHABET = (
    "!#$%&'()*+-./0123456789:=?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[]^_`abcdefghijklmnopqrstuvwxyz{"
)
_DIGIT = {ch: i for i, ch in enumerate(ALPHABET)}
RADIX = 85
M32 = 0xFFFFFFFF
TOP = 1 << 24
BOTTOM = 1 << 16
LOSSLESS_THRESHOLD = 129  # documented: pixel >= 129 is foreground

# [VERIFY] upstream halves "with a minimum of 1"; floor-then-clamp is assumed.
# Flip to True to try ceil(c/2) instead if a real payload fails to decode.
HALVE_CEIL = False
# [VERIFY] lossless order-1/order-2 context uses the previous residual in
# raster order. At the start of a row, is that the last residual of the
# previous row (True, assumed) or 0 (False)? The available vectors do not
# discriminate; see selftest output.
PREV_RESIDUAL_ACROSS_ROWS = True


def _halve_min1(c: int) -> int:
    return max(1, (c + 1) >> 1) if HALVE_CEIL else max(1, c >> 1)


# ---------------------------------------------------------------------------
# Base85 layer (documented alphabet, 4 bytes <-> 5 digits big-endian,
# 5-digit big-endian length prefix = number of decoded bytes)
# ---------------------------------------------------------------------------


def b85_encode_blob(blob: bytes) -> str:
    n = len(blob)
    out = [_digits5(n)]
    full = n // 4
    for i in range(full):
        out.append(_digits5(int.from_bytes(blob[4 * i : 4 * i + 4], "big")))
    r = n % 4
    if r:
        tail = blob[4 * full :] + b"\x00" * (4 - r)
        out.append(_digits5(int.from_bytes(tail, "big"))[: r + 1])
    return "".join(out)


def _digits5(value: int) -> str:
    d = []
    for _ in range(5):
        d.append(ALPHABET[value % RADIX])
        value //= RADIX
    return "".join(reversed(d))


def b85_decode_blob(s: str) -> bytes:
    if len(s) < 5:
        raise MaskDecodeError("payload shorter than the 5-char length prefix")
    try:
        n = 0
        for ch in s[:5]:
            n = n * RADIX + _DIGIT[ch]
    except KeyError as e:
        raise MaskDecodeError(f"character outside base85 alphabet: {e.args[0]!r}")
    expected_chars = 5 + (n // 4) * 5 + (0 if n % 4 == 0 else n % 4 + 1)
    if len(s) != expected_chars:
        raise MaskDecodeError(
            f"length prefix says {n} bytes ({expected_chars} chars) but payload has {len(s)} chars"
        )
    body = s[5:]
    out = bytearray()
    full = n // 4
    try:
        for i in range(full):
            v = 0
            for ch in body[5 * i : 5 * i + 5]:
                v = v * RADIX + _DIGIT[ch]
            if v > M32:
                raise MaskDecodeError("base85 group out of 32-bit range")
            out += v.to_bytes(4, "big")
        r = n % 4
        if r:
            tail = body[5 * full :]
            v = 0
            for ch in tail + ALPHABET[RADIX - 1] * (5 - len(tail)):
                v = v * RADIX + _DIGIT[ch]
            if v > M32:
                raise MaskDecodeError("base85 tail out of 32-bit range")
            out += v.to_bytes(4, "big")[:r]
    except KeyError as e:
        raise MaskDecodeError(f"character outside base85 alphabet: {e.args[0]!r}")
    if b85_encode_blob(bytes(out)) != s:
        raise MaskDecodeError("non-canonical base85 spelling")
    return bytes(out)


# ---------------------------------------------------------------------------
# Range coder (carry-less, 32-bit, TOP=2^24, BOTTOM=2^16)  [VERIFY]
# ---------------------------------------------------------------------------


class RangeEncoder:
    def __init__(self) -> None:
        self.low = 0
        self.range = M32
        self.out = bytearray()

    def encode(self, cum: int, freq: int, total: int) -> None:
        scaled = self.range // total
        self.low = (self.low + scaled * cum) & M32
        self.range = (scaled * freq) & M32
        while True:
            if (self.low ^ ((self.low + self.range) & M32)) < TOP:
                pass
            elif self.range < BOTTOM:
                self.range = (-self.low) & (BOTTOM - 1)
            else:
                break
            self.out.append(self.low >> 24)
            self.low = (self.low << 8) & M32
            self.range = (self.range << 8) & M32

    def finish(self) -> bytes:
        for _ in range(4):
            self.out.append(self.low >> 24)
            self.low = (self.low << 8) & M32
        return bytes(self.out)


class RangeDecoder:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos
        self.low = 0
        self.range = M32
        self.code = 0
        self.scaled = 0
        for _ in range(4):
            self.code = (self.code << 8) | self._byte()

    def _byte(self) -> int:
        if self.pos >= len(self.data):
            raise MaskDecodeError("range coder read past end of payload")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def get_freq(self, total: int) -> int:
        self.scaled = self.range // total
        if self.scaled == 0:
            raise MaskDecodeError("range coder underflow")
        v = ((self.code - self.low) & M32) // self.scaled
        return min(v, total - 1)

    def consume(self, cum: int, freq: int) -> None:
        self.low = (self.low + self.scaled * cum) & M32
        self.range = (self.scaled * freq) & M32
        while True:
            if (self.low ^ ((self.low + self.range) & M32)) < TOP:
                pass
            elif self.range < BOTTOM:
                self.range = (-self.low) & (BOTTOM - 1)
            else:
                break
            self.code = ((self.code << 8) & M32) | self._byte()
            self.low = (self.low << 8) & M32
            self.range = (self.range << 8) & M32


# ---------------------------------------------------------------------------
# one_bit model: 12 causal neighbours (2 left, 5 above x-2..x+2, 5 two rows up),
# counts start (1,1), +14 per coded bit, halve (min 1) when sum >= 4096  [VERIFY]
# ---------------------------------------------------------------------------

_OB_INC = 14
_OB_LIMIT = 4096


def _one_bit_contexts(h: int, w: int):
    pad = [[0] * (w + 4) for _ in range(h + 2)]
    return pad


def _ob_ctx(pad, y: int, x: int) -> int:
    r0 = pad[y + 2]
    r1 = pad[y + 1]
    r2 = pad[y]
    c = x + 2
    return (
        (r0[c - 1] << 11) | (r0[c - 2] << 10)
        | (r1[c - 2] << 9) | (r1[c - 1] << 8) | (r1[c] << 7) | (r1[c + 1] << 6) | (r1[c + 2] << 5)
        | (r2[c - 2] << 4) | (r2[c - 1] << 3) | (r2[c] << 2) | (r2[c + 1] << 1) | r2[c + 2]
    )


def _ob_update(c0: List[int], c1: List[int], ctx: int, bit: int) -> None:
    if bit:
        c1[ctx] += _OB_INC
    else:
        c0[ctx] += _OB_INC
    if c0[ctx] + c1[ctx] >= _OB_LIMIT:
        c0[ctx] = _halve_min1(c0[ctx])
        c1[ctx] = _halve_min1(c1[ctx])


def encode_one_bit(bits: Sequence[int], w: int, h: int) -> str:
    """Encode a row-major 0/1 raster into a full '!'-prefixed payload string."""
    if len(bits) != w * h:
        raise ValueError("raster size mismatch")
    pad = _one_bit_contexts(h, w)
    c0 = [1] * 4096
    c1 = [1] * 4096
    enc = RangeEncoder()
    for y in range(h):
        for x in range(w):
            b = 1 if bits[y * w + x] else 0
            ctx = _ob_ctx(pad, y, x)
            a, bb = c0[ctx], c1[ctx]
            if b:
                enc.encode(a, bb, a + bb)
            else:
                enc.encode(0, a, a + bb)
            _ob_update(c0, c1, ctx, b)
            pad[y + 2][x + 2] = b
    return "!" + b85_encode_blob(enc.finish())


def _decode_one_bit(blob: bytes, w: int, h: int) -> bytearray:
    pad = _one_bit_contexts(h, w)
    c0 = [1] * 4096
    c1 = [1] * 4096
    dec = RangeDecoder(blob)
    out = bytearray(w * h)
    for y in range(h):
        row = pad[y + 2]
        for x in range(w):
            ctx = _ob_ctx(pad, y, x)
            a, bb = c0[ctx], c1[ctx]
            v = dec.get_freq(a + bb)
            if v < a:
                b = 0
                dec.consume(0, a)
            else:
                b = 1
                dec.consume(a, bb)
            _ob_update(c0, c1, ctx, b)
            row[x + 2] = b
            out[y * w + x] = b
    if dec.pos != len(blob):
        raise MaskDecodeError("one_bit payload has unread trailing bytes")
    return out


# ---------------------------------------------------------------------------
# lossless model: predictor byte + zero-flag model + 3 mixed symbol tables  [VERIFY]
# ---------------------------------------------------------------------------


def _predict(mode: int, l: int, u: int, ul: int) -> int:
    if mode == 0:  # Paeth, ties L then U then UL
        est = l + u - ul
        dl, du, dul = abs(est - l), abs(est - u), abs(est - ul)
        if dl <= du and dl <= dul:
            return l
        return u if du <= dul else ul
    if mode == 1:
        return u
    if mode == 2:
        return l
    if mode == 3:
        return (l + u) >> 1
    if mode == 4:  # median edge detector
        if ul >= max(l, u):
            return min(l, u)
        if ul <= min(l, u):
            return max(l, u)
        return l + u - ul
    raise MaskDecodeError(f"unsupported predictor {mode}")


def _decode_lossless(blob: bytes, w: int, h: int) -> bytearray:
    if len(blob) < 5:
        raise MaskDecodeError("lossless payload too short")
    mode = blob[0]
    if mode > 4:
        raise MaskDecodeError(f"invalid predictor selector {mode}")
    dec = RangeDecoder(blob, 1)
    zc = [1] * 16
    nz = [1] * 16
    o2: Dict[Tuple[int, int], List[int]] = {}
    o1 = [[1] * 256 + [256] for _ in range(256)]
    o0 = [2] * 256 + [512]
    pix = [bytearray(w) for _ in range(h)]
    res = [[0] * w for _ in range(h)]
    prev = 0
    for y in range(h):
        if not PREV_RESIDUAL_ACROSS_ROWS:
            prev = 0
        for x in range(w):
            l = pix[y][x - 1] if x > 0 else 0
            u = pix[y - 1][x] if y > 0 else 0
            ul = pix[y - 1][x - 1] if (x > 0 and y > 0) else 0
            pred = _predict(mode, l, u, ul) & 0xFF if mode != 0 else _predict(0, l, u, ul)
            # zero-flag context: residual at left, up, up-left, up-right was zero (OOB counts as zero)
            ctx = 0
            ctx |= 1 if (x == 0 or res[y][x - 1] == 0) else 0
            ctx |= (1 if (y == 0 or res[y - 1][x] == 0) else 0) << 1
            ctx |= (1 if (x == 0 or y == 0 or res[y - 1][x - 1] == 0) else 0) << 2
            ctx |= (1 if (y == 0 or x == w - 1 or res[y - 1][x + 1] == 0) else 0) << 3
            a, b = zc[ctx], nz[ctx]
            v = dec.get_freq(a + b)
            if v < a:
                flag = 0
                dec.consume(0, a)
            else:
                flag = 1
                dec.consume(a, b)
            if flag:
                nz[ctx] += 32
            else:
                zc[ctx] += 32
            if zc[ctx] + nz[ctx] >= 16384:
                zc[ctx] = _halve_min1(zc[ctx])
                nz[ctx] = _halve_min1(nz[ctx])
            sym = 0
            if flag:
                up = res[y - 1][x] if y > 0 else 0
                t2 = o2.get((prev, up))
                if t2 is None:
                    t2 = [0] * 256 + [0]
                    o2[(prev, up)] = t2
                t1 = o1[prev]
                total = t2[256] + t1[256] + o0[256] - (t2[0] + t1[0] + o0[0])
                v = dec.get_freq(total)
                cum = 0
                for s in range(1, 256):
                    f = t2[s] + t1[s] + o0[s]
                    if v < cum + f:
                        sym = s
                        break
                    cum += f
                else:
                    raise MaskDecodeError("lossless symbol search overran")
                dec.consume(cum, f)
                t2[sym] += 616
                t2[256] += 616
                if t2[256] >= 50176:
                    for i in range(256):
                        t2[i] >>= 1
                    t2[256] = sum(t2[:256])
                t1[sym] += 56
                t1[256] += 56
                if t1[256] >= 8192:
                    for i in range(256):
                        t1[i] = _halve_min1(t1[i])
                    t1[256] = sum(t1[:256])
                o0[sym] += 32
                o0[256] += 32
                if o0[256] >= 4096:
                    for i in range(256):
                        o0[i] >>= 1
                    o0[256] = sum(o0[:256])
            delta = 0 if sym == 0 else (sym // 2 if sym % 2 == 0 else -((sym + 1) // 2))
            pix[y][x] = (pred + delta) & 0xFF
            res[y][x] = sym
            prev = sym
    flat = bytearray()
    for row in pix:
        flat += row
    return flat


# ---------------------------------------------------------------------------
# Public decode API
# ---------------------------------------------------------------------------


def parse_mask_data(data: str) -> Tuple[int, int, str]:
    """Split 'H,W,<marker><payload>' (the value of the data= field)."""
    parts = data.split(",", 2)
    if len(parts) != 3:
        raise MaskDecodeError("mask data must be 'H,W,payload'")
    try:
        h, w = int(parts[0]), int(parts[1])
    except ValueError:
        raise MaskDecodeError("mask H and W must be integers")
    if h <= 0 or w <= 0:
        raise MaskDecodeError("mask H and W must be positive")
    return h, w, parts[2]


def decode_mask(data: str, strict_canonical: bool = True) -> Tuple[int, int, str, bytes]:
    """Decode a mask 'data' string -> (h, w, encoding, raster).

    raster is row-major, one byte per pixel with values 0 or 1, len == h*w.
    encoding is 'one_bit' (marker '!') or 'lossless' (marker '~').
    With strict_canonical the one_bit payload must re-encode to the same string.
    """
    h, w, payload = parse_mask_data(data)
    if not payload or payload[0] not in "!~":
        raise MaskDecodeError("unknown encoding marker")
    marker, body = payload[0], payload[1:]
    blob = b85_decode_blob(body)
    if marker == "!":
        raster = bytes(_decode_one_bit(blob, w, h))
        if strict_canonical and encode_one_bit(raster, w, h) != payload:
            raise MaskDecodeError("one_bit payload is not the canonical encoding of its raster")
        return h, w, "one_bit", raster
    gray = _decode_lossless(blob, w, h)
    return h, w, "lossless", bytes(1 if v >= LOSSLESS_THRESHOLD else 0 for v in gray)


def verify_against_reference(data: str, reference: Optional[Callable] = None) -> bool:
    """Cross-check this decoder with meta_sam_parser.decode_mask_to_raster.

    `reference` takes the mask 'data' string (adapt the call to the installed
    meta-sam-parser version; its public signature is [VERIFY], docs only name
    decode_mask_to_raster). Returns True when rasters are byte-identical.
    """
    h, w, _enc, mine = decode_mask(data)
    if reference is None:
        from meta_sam_parser import decode_mask_to_raster  # type: ignore

        reference = decode_mask_to_raster
    return bytes(reference(data)) == mine


def raster_sha256(raster: bytes) -> str:
    return hashlib.sha256(raster).hexdigest()


# ---------------------------------------------------------------------------
# SAM text / SSE parsing (key-based; tolerant of c= confidence fields)
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"<(?P<f>\d+)f>|(?P<id>\d+)(?=<\|)|<\|(?P<kind>box|mask|point);(?P<body>[^|]*)\|>")


def _fields(body: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in body.split(";"):
        k, sep, v = part.partition("=")
        if sep:
            out[k] = v
    return out


def parse_sam_text(text: str) -> List[dict]:
    """Parse '<Nf>id<|box;..|><|mask;..|>,...' into records.

    Returns [{frame, object_id, box: {x1,y1,x2,y2,w,h,c}|None,
              mask: {x,y,c,data}|None}]. Missing c is None (it does not mean 0).
    """
    records: Dict[Tuple[int, int], dict] = {}
    order: List[Tuple[int, int]] = []
    frame, obj = 0, -1
    for m in _TOKEN.finditer(text):
        if m.group("f") is not None:
            frame = int(m.group("f"))
        elif m.group("id") is not None:
            obj = int(m.group("id"))
        else:
            key = (frame, obj)
            if key not in records:
                records[key] = {"frame": frame, "object_id": obj, "box": None, "mask": None}
                order.append(key)
            f = _fields(m.group("body"))
            c = float(f["c"]) if "c" in f else None
            if m.group("kind") == "box":
                records[key]["box"] = {
                    **{k: int(f[k]) for k in ("x1", "y1", "x2", "y2", "w", "h")},
                    "c": c,
                }
            elif m.group("kind") == "mask":
                data = m.group("body").split("data=", 1)[1]
                records[key]["mask"] = {"x": int(f["x"]), "y": int(f["y"]), "c": c, "data": data}
    return [records[k] for k in order]


def parse_sse(sse_text: str) -> dict:
    """Join response.output_text.delta events; return text, response id, model served, usage."""
    deltas: List[str] = []
    info = {"response_id": None, "model_served": None, "usage": None}
    for line in sse_text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            evt = json.loads(payload)
        except ValueError:
            continue
        resp = evt.get("response") if isinstance(evt.get("response"), dict) else None
        if resp:
            info["response_id"] = resp.get("id", info["response_id"])
            info["model_served"] = resp.get("model", info["model_served"])
            if resp.get("usage") is not None:
                info["usage"] = resp["usage"]
        if evt.get("type") == "response.output_text.delta":
            deltas.append(evt.get("delta", ""))
    info["text"] = "".join(deltas)
    return info


def build_box_prompt(objects: Dict[int, Tuple[int, int, int, int]], w: int, h: int) -> str:
    """Build the input_text for box prompts. Boxes are inclusive (x1,y1,x2,y2) in pixels."""
    parts = []
    for oid, (x1, y1, x2, y2) in objects.items():
        if not (0 <= x1 <= x2 < w and 0 <= y1 <= y2 < h):
            raise ValueError(f"box {oid} outside the {w}x{h} image or inverted")
        parts.append(f"{oid}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h}|>")
    if not 1 <= len(parts) <= 16:
        raise ValueError("between 1 and 16 prompted objects per request")
    return "<0f>" + ",".join(parts)


# ---------------------------------------------------------------------------
# Raster -> polygons (exact pixel-edge boundary tracing + Douglas-Peucker)
# Vertex (x, y) is a pixel CORNER; pixel (i, j) spans [i, i+1] x [j, j+1].
# ---------------------------------------------------------------------------

Point = Tuple[int, int]


def trace_rings(raster: bytes, h: int, w: int) -> List[List[Point]]:
    """Closed rings along pixel edges; outer rings have positive shoelace area
    (clockwise on screen, y down), holes negative. Foreground is 4-connected."""
    nxt: Dict[Point, List[Point]] = {}

    def add(a: Point, b: Point) -> None:
        nxt.setdefault(a, []).append(b)

    for r in range(h):
        base = r * w
        for c in range(w):
            if not raster[base + c]:
                continue
            if r == 0 or not raster[base - w + c]:
                add((c, r), (c + 1, r))
            if c == w - 1 or not raster[base + c + 1]:
                add((c + 1, r), (c + 1, r + 1))
            if r == h - 1 or not raster[base + w + c]:
                add((c + 1, r + 1), (c, r + 1))
            if c == 0 or not raster[base + c - 1]:
                add((c, r + 1), (c, r))
    rings: List[List[Point]] = []
    for start in list(nxt.keys()):
        while nxt.get(start):
            ring = [start]
            cur = nxt[start].pop()
            prev = start
            while cur != start:
                ring.append(cur)
                outs = nxt[cur]
                dx, dy = cur[0] - prev[0], cur[1] - prev[1]
                choice = None
                for d in ((-dy, dx), (dx, dy), (dy, -dx)):  # right, straight, left
                    cand = (cur[0] + d[0], cur[1] + d[1])
                    if cand in outs:
                        choice = cand
                        break
                if choice is None:
                    raise MaskDecodeError("boundary tracing failed (open contour)")
                outs.remove(choice)
                prev, cur = cur, choice
            rings.append(_drop_collinear(ring))
    return rings


def _drop_collinear(ring: List[Point]) -> List[Point]:
    n = len(ring)
    keep = []
    for i in range(n):
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) != 0:
            keep.append(b)
    return keep


def ring_area(ring: Sequence[Sequence[float]]) -> float:
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _dp_open(pts: List[Point], eps: float) -> List[Point]:
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (x1, y1), (x2, y2) = pts[i], pts[j]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)
        best, idx = -1.0, -1
        for k in range(i + 1, j):
            px, py = pts[k]
            d = (math.hypot(px - x1, py - y1) if norm == 0
                 else abs(dy * (px - x1) - dx * (py - y1)) / norm)
            if d > best:
                best, idx = d, k
        if best > eps:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(pts, keep) if k]


def simplify_ring(ring: List[Point], eps: float) -> List[Point]:
    """Douglas-Peucker on a closed ring (anchored at vertex 0 and the vertex farthest from it)."""
    if eps <= 0 or len(ring) <= 4:
        return ring
    far = max(range(len(ring)), key=lambda k: (ring[k][0] - ring[0][0]) ** 2 + (ring[k][1] - ring[0][1]) ** 2)
    a = _dp_open(ring[: far + 1], eps)
    b = _dp_open(ring[far:] + [ring[0]], eps)
    out = a[:-1] + b[:-1]
    return out if len(out) >= 3 else ring


def mask_to_polygons(raster: bytes, h: int, w: int, offset: Tuple[int, int] = (0, 0),
                     epsilon: float = 0.75) -> List[dict]:
    """Polygons in the coordinates of the image the mask was placed on.

    offset = (box.x1, box.y1). Returns [{'role': 'outer'|'hole', 'points': [[x, y], ...]}]
    sorted by decreasing |area|. Draw all rings in ONE path with the even-odd rule.
    """
    ox, oy = offset
    out = []
    for ring in trace_rings(raster, h, w):
        area = ring_area(ring)
        simp = simplify_ring(ring, epsilon)
        out.append((abs(area), {"role": "outer" if area > 0 else "hole",
                                "points": [[x + ox, y + oy] for x, y in simp]}))
    out.sort(key=lambda t: -t[0])
    return [p for _a, p in out]


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------


def place_mask(raster: bytes, h: int, w: int, x1: int, y1: int, img_w: int, img_h: int) -> bytearray:
    """Paste the box-sized mask into a full img_w x img_h raster (clipped)."""
    out = bytearray(img_w * img_h)
    for r in range(h):
        y = y1 + r
        if not 0 <= y < img_h:
            continue
        for c in range(w):
            x = x1 + c
            if 0 <= x < img_w and raster[r * w + c]:
                out[y * img_w + x] = 1
    return out


def rasterize_rings(rings: Iterable[Sequence[Sequence[float]]], img_w: int, img_h: int) -> bytearray:
    """Even-odd fill sampled at pixel centres (x+0.5, y+0.5). Corner-coordinate convention."""
    out = bytearray(img_w * img_h)
    rings = [list(r) for r in rings]
    for y in range(img_h):
        yc = y + 0.5
        xs: List[float] = []
        for ring in rings:
            n = len(ring)
            for i in range(n):
                (xa, ya), (xb, yb) = ring[i], ring[(i + 1) % n]
                if (ya <= yc < yb) or (yb <= yc < ya):
                    xs.append(xa + (yc - ya) * (xb - xa) / (yb - ya))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            lo = max(0, math.ceil(xs[i] - 0.5))
            hi = min(img_w - 1, math.ceil(xs[i + 1] - 0.5) - 1)
            for x in range(lo, hi + 1):
                out[y * img_w + x] = 1
    return out


def iou(a: bytes, b: bytes) -> Tuple[float, int, int]:
    """(IoU, intersection_px, union_px). Both rasters must have the same size."""
    if len(a) != len(b):
        raise ValueError("raster size mismatch")
    inter = sum(1 for p, q in zip(a, b) if p and q)
    union = sum(1 for p, q in zip(a, b) if p or q)
    return (inter / union if union else 0.0), inter, union


def principal_axes_m(raster: bytes, h: int, w: int, pixel_spacing_m: float) -> dict:
    """Length/beam of a mask with the SAME estimator as morphology.estimate_inertial_dimensions
    (4*sqrt(eigenvalue) of the pixel-coordinate covariance, no floors) plus Feret length
    along the major axis. pixel_spacing_m is the spacing of the raster the mask lives on."""
    pts = [(r + 0.5, c + 0.5) for r in range(h) for c in range(w) if raster[r * w + c]]
    n = len(pts)
    if n < 2:
        return {"pca_length_m": None, "pca_beam_m": None, "feret_length_m": None, "heading_deg": None}
    rb = sum(p[0] for p in pts) / n
    cb = sum(p[1] for p in pts) / n
    m20 = sum((p[0] - rb) ** 2 for p in pts) / n
    m02 = sum((p[1] - cb) ** 2 for p in pts) / n
    m11 = sum((p[0] - rb) * (p[1] - cb) for p in pts) / n
    tr, det = m20 + m02, m20 * m02 - m11 * m11
    disc = math.sqrt(max(tr * tr / 4 - det, 0.0))
    lmaj, lmin = tr / 2 + disc, max(tr / 2 - disc, 0.0)
    # eigenvector of the major eigenvalue in (r, c); heading as in the CFAR code (0 = north, row up)
    if abs(m11) > 1e-12:
        vr, vc = lmaj - m02, m11
    else:
        vr, vc = (1.0, 0.0) if m20 >= m02 else (0.0, 1.0)
    nv = math.hypot(vr, vc)
    vr, vc = vr / nv, vc / nv
    proj = [(p[0] - rb) * vr + (p[1] - cb) * vc for p in pts]
    feret = (max(proj) - min(proj)) + 1.0
    return {
        "pca_length_m": 4.0 * math.sqrt(lmaj) * pixel_spacing_m,
        "pca_beam_m": 4.0 * math.sqrt(lmin) * pixel_spacing_m,
        "feret_length_m": feret * pixel_spacing_m,
        "heading_deg": math.degrees(math.atan2(vc, -vr)) % 360.0,
    }


# ---------------------------------------------------------------------------
# Document-level consistency checks (what JSON Schema cannot express)
# ---------------------------------------------------------------------------


COVERS_MATCH = "inclusive_response_bbox"
COVERS_MISMATCH = "dimension_mismatch_VERIFY"


def mask_box_relation(h: int, w: int, bbox: dict) -> str:
    """COVERS_MATCH when the H x W raster has the inclusive box size, else COVERS_MISMATCH."""
    same = (h, w) == (bbox["y2"] - bbox["y1"] + 1, bbox["x2"] - bbox["x1"] + 1)
    return COVERS_MATCH if same else COVERS_MISMATCH


def verify_sam_masks(doc: dict, warnings: Optional[List[str]] = None) -> List[str]:
    """Consistency checks JSON Schema cannot express. Returns problems; empty means consistent.

    Non-fatal findings (a declared dimension_mismatch_VERIFY mask) are appended to
    `warnings` when a list is passed; they never make the document inconsistent, but
    the geometry derived from that mask is left unchecked and must stay empty.

    Recomputes from mask.raw and ground_truth every derived number in a measured chip
    (area, centroid, raster hash, polygons, IoU, PCA length) and compares it with what the
    file claims. Run it after validating the document against sam_masks.schema.json.
    """
    errs: List[str] = []
    run = doc.get("run", {})
    cost = run.get("cost") or {}
    price = (run.get("price_source") or {}).get("sam_usd_per_1000_images")
    if cost.get("estimated_usd") is not None and price is not None:
        exp = cost["billed_images"] * price / 1000.0
        if abs(exp - cost["estimated_usd"]) > 1e-9:
            errs.append(f"run.cost.estimated_usd {cost['estimated_usd']} != billed_images*price/1000 = {exp}")
    chips = doc.get("chips", [])
    ids = [c["chip_id"] for c in chips]
    if len(ids) != len(set(ids)):
        errs.append("duplicate chip_id")
    answered = sum(1 for c in chips if c.get("sam_response"))
    n_dets = sum(len((c.get("sam_response") or {}).get("detections", [])) for c in chips)
    if run.get("mode") == "live":
        if cost.get("billed_images", 0) < answered:
            errs.append(f"run.cost.billed_images {cost.get('billed_images')} < chips with a response ({answered})")
        parsing = run.get("parsing") or {}
        if parsing.get("rasters_decoded") != n_dets:
            errs.append(f"run.parsing.rasters_decoded {parsing.get('rasters_decoded')} != detections in file ({n_dets})")
    for chip in chips:
        cid = chip["chip_id"]
        g = chip["geometry"]
        gw, gh = g["width"], g["height"]
        up = g["upsample_factor"]
        if abs(g["chip_pixel_spacing_m"] - g["pixel_spacing_m"] / up) > 1e-9 * max(1.0, g["pixel_spacing_m"]):
            errs.append(f"{cid}: chip_pixel_spacing_m != pixel_spacing_m/upsample_factor")
        win = g["scene_window"]
        if win["width"] * up != gw or win["height"] * up != gh:
            errs.append(f"{cid}: scene_window * upsample_factor != chip size")
        di = chip["display_image"]
        if (di["width"], di["height"]) != (gw, gh):
            errs.append(f"{cid}: display_image size differs from chip size")
        if chip["source"]["synthetic"] != (chip["source"]["kind"] == "synthetic_fixture"):
            errs.append(f"{cid}: source.synthetic inconsistent with source.kind")
        prompt = chip["prompt"]
        if (prompt["media_width"], prompt["media_height"]) != (gw, gh):
            errs.append(f"{cid}: prompt.media_width/height differ from chip size")
        objs = prompt["objects"]
        by_id = {o["object_id"]: o for o in objs}
        if len(by_id) != len(objs):
            errs.append(f"{cid}: duplicate prompt object_id")
        if prompt["type"] == "box":
            boxes = {}
            for o in objs:
                b = o["box_px"]
                if b is None:
                    continue
                if not (0 <= b["x1"] <= b["x2"] < gw and 0 <= b["y1"] <= b["y2"] < gh):
                    errs.append(f"{cid}: prompt box {o['object_id']} outside the {gw}x{gh} chip or inverted")
                boxes[o["object_id"]] = (b["x1"], b["y1"], b["x2"], b["y2"])
            if prompt.get("input_text") is not None and len(boxes) == len(objs):
                if prompt["input_text"] != build_box_prompt(boxes, gw, gh):
                    errs.append(f"{cid}: prompt.input_text differs from build_box_prompt(objects)")

        gt = chip.get("ground_truth") or {"objects": []}
        gt_ras: Dict[str, bytearray] = {}
        for o in gt["objects"]:
            ras_gt = rasterize_rings(o["polygons_chip_px"], gw, gh)
            gt_ras[o["gt_id"]] = ras_gt
            if sum(ras_gt) != o["area_chip_px"]:
                errs.append(f"{cid}: {o['gt_id']} polygons rasterise to {sum(ras_gt)} px, area_chip_px says {o['area_chip_px']}")
            if o["area_chip_px"] != o["footprint_native_px_count"] * up * up:
                errs.append(f"{cid}: {o['gt_id']} area_chip_px != footprint_native_px_count * upsample_factor^2")

        resp = chip.get("sam_response")
        dets = (resp or {}).get("detections", [])
        if chip["status"] == "measured" and resp is None:
            errs.append(f"{cid}: status measured without sam_response")
        if resp and prompt["type"] == "box" and len(dets) > len(objs):
            errs.append(f"{cid}: {len(dets)} detections for {len(objs)} prompted boxes")
        for i, d in enumerate(dets):
            tag = f"{cid}.detections[{i}]"
            m = d["mask"]
            raw = m["raw"]
            if "..." in raw or len(raw) != m["raw_length_chars"]:
                errs.append(f"{tag}: mask.raw truncated or raw_length_chars wrong")
                continue
            if hashlib.sha256(raw.encode("ascii")).hexdigest() != m["raw_sha256"]:
                errs.append(f"{tag}: mask.raw_sha256 mismatch")
            try:
                h, w, enc, ras = decode_mask(raw)
            except MaskDecodeError as e:
                errs.append(f"{tag}: mask does not decode: {e}")
                continue
            b = d["bbox"]
            if not (0 <= b["x1"] <= b["x2"] < gw and 0 <= b["y1"] <= b["y2"] < gh):
                errs.append(f"{tag}: bbox outside the chip or inverted")
                continue
            relation = mask_box_relation(h, w, b)
            if m["covers"] != relation:
                errs.append(f"{tag}: mask {h}x{w} vs inclusive bbox {b['y2']-b['y1']+1}x{b['x2']-b['x1']+1} "
                            f"means mask.covers must be {relation}, file says {m['covers']}")
            if (m["height"], m["width"]) != (h, w):
                errs.append(f"{tag}: mask.height/width != declared raster {h}x{w}")
            if enc != m["encoding"] or m["marker"] != ("!" if enc == "one_bit" else "~"):
                errs.append(f"{tag}: encoding/marker inconsistent with mask.raw ({enc})")
            mismatch = relation == COVERS_MISMATCH
            if resp.get("api_rescaled_to") is None and (b["w"], b["h"]) != (gw, gh):
                errs.append(f"{tag}: bbox.w/h {b['w']}x{b['h']} != chip {gw}x{gh} but api_rescaled_to is null")
            c = d["confidence"]
            exp_score = c["mask"] if c["mask"] is not None else c["box"]
            if d["score"] != exp_score:
                errs.append(f"{tag}: score {d['score']} != confidence.mask else confidence.box ({exp_score})")
            dec = m["decoded"]
            if dec["raster_sha256"] != raster_sha256(ras):
                errs.append(f"{tag}: decoded.raster_sha256 mismatch")
            area = sum(ras)
            if dec["area_px"] != area:
                errs.append(f"{tag}: decoded.area_px {dec['area_px']} != pixel count {area}")
            if abs(dec["area_m2"] - area * g["chip_pixel_spacing_m"] ** 2) > 1e-6 * max(1.0, dec["area_m2"]):
                errs.append(f"{tag}: decoded.area_m2 != area_px * chip_pixel_spacing_m^2")
            if mismatch:
                if warnings is not None:
                    warnings.append(f"{tag}: mask {h}x{w} differs from the inclusive bbox; placement unspecified in the docs, "
                                    "geometry derived from this mask is not checked and area_m2 is only a raster-pixel count times spacing")
                met_ = d["metrics"]
                if dec["polygons"] or dec["centroid_px"] is not None:
                    errs.append(f"{tag}: dimension_mismatch_VERIFY mask must have no polygons and centroid_px null")
                if met_["iou_vs_ground_truth"] is not None or met_["length_vs_cfar"] is not None:
                    errs.append(f"{tag}: dimension_mismatch_VERIFY mask must have iou_vs_ground_truth and length_vs_cfar null")
                continue
            if area:
                cx = b["x1"] + sum(k % w + 0.5 for k in range(h * w) if ras[k]) / area
                cy = b["y1"] + sum(k // w + 0.5 for k in range(h * w) if ras[k]) / area
                cen = dec["centroid_px"]
                if cen is None or abs(cen[0] - cx) > 0.01 or abs(cen[1] - cy) > 0.01:
                    errs.append(f"{tag}: decoded.centroid_px {cen} != recomputed ({cx:.3f}, {cy:.3f})")
            polys = dec["polygons"]
            poly_area = sum(ring_area(p["points"]) for p in polys)
            if dec["polygon_epsilon_px"] == 0:
                if bytes(rasterize_rings([p["points"] for p in polys], gw, gh)) != bytes(place_mask(ras, h, w, b["x1"], b["y1"], gw, gh)):
                    errs.append(f"{tag}: epsilon-0 polygons do not rasterise back to the mask")
            elif abs(poly_area - area) > max(0.15 * area, 2.0):
                errs.append(f"{tag}: simplified polygon area {poly_area:.1f} deviates >15% from area_px {area}")

            met = d["metrics"]
            gid = met["matched_ground_truth_id"]
            claimed = met["iou_vs_ground_truth"]
            if claimed is not None and gid is None:
                errs.append(f"{tag}: iou_vs_ground_truth given without matched_ground_truth_id")
            if gid is not None:
                if gid not in gt_ras:
                    errs.append(f"{tag}: matched_ground_truth_id {gid} not in this chip's ground_truth")
                elif claimed is not None:
                    full = place_mask(ras, h, w, b["x1"], b["y1"], gw, gh)
                    val, inter, union = iou(bytes(full), bytes(gt_ras[gid]))
                    if abs(claimed["value"] - val) > 1e-6 or claimed["intersection_px"] != inter or claimed["union_px"] != union:
                        errs.append(f"{tag}: iou claimed {claimed} but recomputed {val:.6f} ({inter}/{union})")
            lv = met["length_vs_cfar"]
            if lv is not None:
                ax = principal_axes_m(ras, h, w, g["chip_pixel_spacing_m"])
                for key, got in (("from_mask_m", ax["pca_length_m"]), ("beam_from_mask_m", ax["pca_beam_m"]),
                                 ("feret_length_m", ax["feret_length_m"])):
                    if got is None or abs(lv[key] - got) > 1e-6 * max(1.0, abs(got)):
                        errs.append(f"{tag}: length_vs_cfar.{key} {lv[key]} != recomputed {got}")
                po = by_id.get(d["prompt_object_id"])
                cfar_len = po["cfar"]["length_m"] if po else None
                if lv["cfar_m"] != cfar_len:
                    errs.append(f"{tag}: length_vs_cfar.cfar_m {lv['cfar_m']} != prompt object cfar.length_m {cfar_len}")
                elif cfar_len is not None:
                    dm_ = lv["from_mask_m"] - cfar_len
                    if lv["delta_m"] is None or abs(lv["delta_m"] - dm_) > 1e-6 * max(1.0, abs(dm_)):
                        errs.append(f"{tag}: length_vs_cfar.delta_m != from_mask_m - cfar_m")
                    if cfar_len and (lv["delta_pct"] is None or abs(lv["delta_pct"] - 100.0 * dm_ / cfar_len) > 1e-6 * max(1.0, abs(dm_))):
                        errs.append(f"{tag}: length_vs_cfar.delta_pct != 100*delta_m/cfar_m")

        ctl = chip["control"]
        if ctl["is_negative_control"] and ctl.get("observed_detections") is not None:
            thr = ctl.get("confidence_threshold") or 0.0
            seen = sum(1 for d in dets if d["score"] is None or d["score"] >= thr)
            if ctl["observed_detections"] != seen:
                errs.append(f"{cid}: control.observed_detections {ctl['observed_detections']} != {seen} detections at threshold {thr}")
            if ctl.get("passed") is not None and ctl["passed"] != (seen == 0):
                errs.append(f"{cid}: control.passed inconsistent with observed_detections")
        if ctl["is_negative_control"] and gt["objects"]:
            errs.append(f"{cid}: negative control has ground_truth objects")
    return errs


# ---------------------------------------------------------------------------
# Self test
# ---------------------------------------------------------------------------

# Real captured vectors from Meta's public conformance suite
# https://github.com/meta-models/meta-sam/tree/main/conformance/cases
# (compact-api-image-lossless.json and captured-api-image-lossless.json).
_V_2X3 = ("2,3,~!!!!.!0^zlTde]:)]`W", [0, 0, 1, 1, 0, 1])
_V_11X11 = (
    "11,11,~!!!!r!kCq#$.pF[_cZIw.?wO3R8-&NJofiCI$_J0aA.1VC*YyK_'z*(LEG8!+H9evY.82Z-4.@C[.9C@T86o$3E=-&8w3W=]xS.",
    [int(v) for v in (
        "00001100000" "00111111100" "01111111110" "01111111110" "11111111111" "11111111111"
        "01111111111" "01111111111" "01111111110" "00111111110" "00001110000")],
)
# One_bit example printed in the protocol page (raster not published there).
_V_5X5_ONE_BIT = "5,5,!!!!!(QO(0lu8?"


def selftest() -> int:
    fails = 0

    def check(name: str, ok: bool, extra: str = "") -> None:
        nonlocal fails
        print(f"[{'OK' if ok else 'FAIL'}] {name} {extra}".rstrip())
        fails += 0 if ok else 1

    check("alphabet equals documented alphabet", ALPHABET == DOCUMENTED_ALPHABET and len(set(ALPHABET)) == 85)
    for r in range(0, 13):
        blob = bytes((i * 37 + 11) & 0xFF for i in range(r))
        if b85_decode_blob(b85_encode_blob(blob)) != blob:
            check(f"base85 round trip len {r}", False)
            break
    else:
        check("base85 round trip for tail widths 0..12", True)

    for name, (data, expect) in (("official lossless 2x3", _V_2X3), ("official lossless 11x11", _V_11X11)):
        try:
            h, w, enc, ras = decode_mask(data)
            check(name, list(ras) == expect and enc == "lossless", f"(decoded {h}x{w})")
        except MaskDecodeError as e:
            check(name, False, f"-> {e}")
    try:
        h, w, enc, ras = decode_mask(_V_5X5_ONE_BIT)
        rows = ["".join(str(v) for v in ras[r * w:(r + 1) * w]) for r in range(h)]
        canonical = encode_one_bit(ras, w, h) == _V_5X5_ONE_BIT.split(",", 2)[2]
        check("protocol one_bit 5x5 decodes and re-encodes to the same payload (raster not published, so only self-consistent)",
              (h, w) == (5, 5) and enc == "one_bit" and canonical, "rows=" + "/".join(rows))
    except MaskDecodeError as e:
        check("protocol one_bit 5x5 decodes and re-encodes to the same payload", False, f"-> {e}")

    legacy_007 = {"x1": 170, "y1": 703, "x2": 1196, "y2": 891}
    check("mask_box_relation: legacy SAR-TGT-007 (188x1024 vs inclusive 189x1027) is a mismatch",
          mask_box_relation(188, 1024, legacy_007) == COVERS_MISMATCH and mask_box_relation(189, 1027, legacy_007) == COVERS_MATCH)

    # Self-consistency only (my encoder against my decoder; proves nothing about Meta's coder).
    import random
    rnd = random.Random(1982)
    for (w, h) in ((1, 1), (3, 3), (73, 71), (200, 90)):
        bits = bytes(1 if (((x - w / 2) / (w / 2.3)) ** 2 + ((y - h / 2) / (h / 3.1)) ** 2 <= 1) != (rnd.random() < 0.03) else 0
                     for y in range(h) for x in range(w))
        payload = encode_one_bit(bits, w, h)
        ok = decode_mask(f"{h},{w},{payload}")[3] == bits
        check(f"one_bit self round trip {w}x{h} ({len(payload)} chars)", ok)

    # Tracer: filled shape with a hole, diagonal pixels, area equality.
    w = h = 12
    ras = bytearray(w * h)
    for y in range(2, 10):
        for x in range(2, 10):
            ras[y * w + x] = 1
    for y in range(4, 7):
        for x in range(4, 7):
            ras[y * w + x] = 0
    polys = mask_to_polygons(bytes(ras), h, w, epsilon=0)
    areas = [ring_area(p["points"]) for p in polys]
    check("tracer: outer+hole area equals pixel count", sorted(p["role"] for p in polys) == ["hole", "outer"]
          and abs(sum(areas) - sum(ras)) < 1e-9, f"(area={sum(areas)}, px={sum(ras)})")
    diag = bytes([1, 0, 0, 1])
    check("tracer: diagonal pixels stay 4-connected (2 rings)", len(trace_rings(diag, 2, 2)) == 2)
    back = rasterize_rings([p["points"] for p in polys], w, h)
    check("rasterize_rings inverts mask_to_polygons", bytes(back) == bytes(ras))
    ell = [[6 + 4 * math.cos(t / 20 * 2 * math.pi), 6 + 2 * math.sin(t / 20 * 2 * math.pi)] for t in range(20)]
    ras2 = rasterize_rings([ell], w, h)
    check("iou of identical rasters is 1", iou(ras2, ras2)[0] == 1.0)

    tags = ("<0f>7<|box;x1=0;y1=0;x2=2;y2=1;w=3;h=2;c=0.91|><|mask;x=0;y=0;c=0.88;data=2,3,~!!!!.!0^zlTde]:)]`W|>,"
            "9<|box;x1=1;y1=1;x2=1;y2=1;w=3;h=2|>")
    recs = parse_sam_text(tags)
    check("parse_sam_text handles c= and ids", len(recs) == 2 and recs[0]["mask"]["c"] == 0.88
          and recs[0]["box"]["c"] == 0.91 and recs[1]["mask"] is None and recs[1]["box"]["c"] is None)
    check("build_box_prompt", build_box_prompt({1: (4, 5, 9, 12)}, 64, 64)
          == "<0f>1<|box;x1=4;y1=5;x2=9;y2=12;w=64;h=64|>")
    print("SELFTEST", "PASSED" if not fails else f"FAILED ({fails})")
    return 1 if fails else 0


def main(argv: List[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--selftest":
        return selftest()
    if len(argv) >= 3 and argv[1] == "--verify":
        with open(argv[2], "r", encoding="utf-8") as f:
            notes: List[str] = []
            problems = verify_sam_masks(json.load(f), notes)
        for n in notes:
            print("WARNING:", n)
        for p in problems:
            print("PROBLEM:", p)
        print("consistent" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
