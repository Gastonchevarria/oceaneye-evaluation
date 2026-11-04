#!/usr/bin/env python3
"""
Generate the two deterministic 512x512 (8x nearest upsampled from 64x64) Dual-Pol SAR
chips referenced in `sam_masks.example.json`:
  - `sam_chips/fx-mile201-sar-tgt-001.png` (window x0=388, y0=48, 64x64)
  - `sam_chips/fx-mile201-empty-sea-300-300.png` (window x0=268, y0=268, 64x64)
And mirror them byte-for-byte across `frontend/data/sam_chips/`, `docs/data/sam_chips/`,
and `data/sam_chips/`.
"""

import os
import shutil
import sys
import numpy as np
from PIL import Image

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))

from app.ingestion.sentinel_fixtures import load_mile201_fixture  # noqa: E402


def render_chip_png(vv: np.ndarray, vh: np.ndarray, x0: int, y0: int, w: int, h: int, upsample: int, out_path: str) -> None:
    vv_sub = vv[y0 : y0 + h, x0 : x0 + w]
    vh_sub = vh[y0 : y0 + h, x0 : x0 + w]
    fused_sub = 0.75 * vh_sub + 0.25 * vv_sub

    vv_db = 10.0 * np.log10(np.maximum(vv_sub, 1e-6))
    vh_db = 10.0 * np.log10(np.maximum(vh_sub, 1e-6))
    fused_db = 10.0 * np.log10(np.maximum(fused_sub, 1e-6))

    lo = float(np.percentile(fused_db, 1.0))
    hi = max(float(np.percentile(fused_db, 99.5)), lo + 6.0)

    def scale_u8(arr: np.ndarray, c_lo: float, c_hi: float) -> np.ndarray:
        return np.round(np.clip((arr - c_lo) / max(c_hi - c_lo, 1e-6), 0.0, 1.0) * 255.0).astype(np.uint8)

    r_ch = scale_u8(vh_db, lo - 3.0, hi)
    g_ch = scale_u8(fused_db, lo, hi)
    b_ch = scale_u8(vv_db, lo, hi + 3.0)
    rgb = np.stack([r_ch, g_ch, b_ch], axis=-1)

    im = Image.fromarray(rgb, mode="RGB").resize((w * upsample, h * upsample), resample=Image.Resampling.NEAREST)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    im.save(out_path, format="PNG")


def main() -> None:
    vv, vh, _ = load_mile201_fixture()

    chips = [
        ("fx-mile201-sar-tgt-001.png", 388, 48, 64, 64, 8),
        ("fx-mile201-empty-sea-300-300.png", 268, 268, 64, 64, 8),
    ]
    base_dir = os.path.join(REPO_ROOT, "frontend/data/sam_chips")
    os.makedirs(base_dir, exist_ok=True)

    for fname, x0, y0, w, h, up in chips:
        src_path = os.path.join(base_dir, fname)
        render_chip_png(vv, vh, x0, y0, w, h, up, src_path)
        for mirror in ("docs/data/sam_chips", "data/sam_chips"):
            m_dir = os.path.join(REPO_ROOT, mirror)
            os.makedirs(m_dir, exist_ok=True)
            shutil.copyfile(src_path, os.path.join(m_dir, fname))
        print(f"Generated and mirrored {fname} ({w*up}x{h*up})")


if __name__ == "__main__":
    main()
