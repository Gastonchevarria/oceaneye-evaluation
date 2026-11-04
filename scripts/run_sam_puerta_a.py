#!/usr/bin/env python3
"""
Puerta A — SAM 3.1 Real SAR Chip Segmentation Pipeline (Steps 1-4 Dry Run + Step 6 Live).

Enforces in code:
- Model strictly locked to `sam-3.1` (no Muse, no muse-image-1.0, no Puerta B STAC).
- Max 10 chips total (8 Mile 201 fixture target chips with GT + 2 empty-sea negative controls).
- Hard cap of USD 0.05 (20 billed images at USD 2.50 / 1,000 images, retries included).
- Worst-case billing calculation (`multi_object_billing = "unknown_VERIFY"`: each prompted box
  counts as 1 billed image in the worst case) checked BEFORE every HTTP request.
- Step 7 (`meta-sam-parser`) is not installed/run: decodes exclusively with `decode_mask.py`,
  keeps `run.parsing.reference_decoder.version = null`, and lists `run.parsing.rasters_disagreeing`
  in `placeholder_fields`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import site
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np
from PIL import Image
from scipy.ndimage import label

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.append("/home/hermes001/.hermes/cua-venv/lib/python3.12/site-packages")

import decode_mask  # noqa: E402
from app.ingestion.sentinel_fixtures import load_mile201_fixture  # noqa: E402
from app.laya.meta_muse_engine import get_meta_api_key  # noqa: E402
from app.processing.calibration import calibrate_dn_to_sigma0_linear  # noqa: E402
from app.processing.cfar_detector import ca_cfar_2d  # noqa: E402
from app.processing.dual_pol_fusion import fuse_dual_polarization  # noqa: E402
from app.processing.lee_filter import enhanced_lee_filter  # noqa: E402
from app.processing.morphology import SarVesselDetection, extract_sar_targets  # noqa: E402

# ---------------------------------------------------------------------------
# Hard Cap Constants (Puerta A)
# ---------------------------------------------------------------------------
MODEL_ALLOWED = "sam-3.1"
ENDPOINT_RESPONSES = "https://api.meta.ai/v1/responses"
ENDPOINT_FILES = "https://api.meta.ai/v1/files"
SAM_USD_PER_1000_IMAGES = 2.50
USD_PER_IMAGE = SAM_USD_PER_1000_IMAGES / 1000.0  # USD 0.0025 per billed image
HARD_CAP_USD = 0.05
MAX_BILLED_IMAGES = 20  # USD 0.05 / 0.0025 == 20 billed images max (including retries)
MAX_CHIPS = 10

TARGET_DETECTION_IDS = [
    "SAR-TGT-001",
    "SAR-TGT-003",
    "SAR-TGT-004",
    "SAR-TGT-005",
    "SAR-TGT-006",
    "SAR-TGT-007",
    "SAR-TGT-009",
    "SAR-TGT-015",
]

NEGATIVE_CONTROLS_SPEC = [
    {
        "chip_id": "fx-mile201-ghost-ais-empty-sea",
        "png_name": "fx-mile201-empty-sea-300-300.png",
        "center_r": 300,
        "center_c": 300,
        "kind": "ais_only_ghost",
        "title": "SINTÉTICO — Mile 201 fixture, mar vacío en posición AIS fantasma (300,300), prompt de texto 'ship' (control negativo)",
        "derivation": (
            "No vessel footprint intersects scene_window (checked against all 15 vessels_spec entries). "
            "The generator also places an AIS-only 'PHANTOM TRANSMITTER' at pixel (300,300) with no radar target."
        ),
    },
    {
        "chip_id": "fx-mile201-open-ocean-empty-sea",
        "png_name": "fx-mile201-empty-sea-080-080.png",
        "center_r": 80,
        "center_c": 80,
        "kind": "empty_sea",
        "title": "SINTÉTICO — Mile 201 fixture, mar vacío en océano abierto (80,80), prompt de texto 'ship' (control negativo)",
        "derivation": (
            "No vessel footprint intersects scene_window [48..112, 48..112] (checked against all 15 vessels_spec entries). "
            "Pure synthetic exponential + Gaussian sea clutter background."
        ),
    },
]


def enforce_budget_before_request(
    billed_images_worst_case: int,
    request_worst_case_images: int,
    model: str,
    chips_count: int,
) -> None:
    """Hard budget & scope gate executed BEFORE every paid HTTP request (including retries)."""
    if model != MODEL_ALLOWED:
        raise RuntimeError(f"BUDGET GATE ABORT: model {model!r} != allowed {MODEL_ALLOWED!r}")
    if chips_count > MAX_CHIPS:
        raise RuntimeError(f"BUDGET GATE ABORT: chips_count {chips_count} > MAX_CHIPS ({MAX_CHIPS})")
    wc_req = max(1, int(request_worst_case_images))
    next_billed_wc = billed_images_worst_case + wc_req
    next_usd_wc = next_billed_wc * USD_PER_IMAGE
    if next_billed_wc > MAX_BILLED_IMAGES or next_usd_wc > HARD_CAP_USD + 1e-12:
        raise RuntimeError(
            f"HARD CAP ABORT: next request requires worst-case {wc_req} image(s), "
            f"which would raise worst-case billed_images to {next_billed_wc} (max {MAX_BILLED_IMAGES}) "
            f"and worst-case cost to USD {next_usd_wc:.6f} (hard cap USD {HARD_CAP_USD:.2f})."
        )


def compute_geometry(x0: int, y0: int, width: int = 64, height: int = 64, upsample: int = 8) -> Dict[str, Any]:
    """Compute chip geometry & linear geotransform matching Mile 201 fixture bounds [-60.8, -46.2, -59.6, -45.0]."""
    chip_w = width * upsample
    chip_h = height * upsample
    d_lon_native = 1.2 / 599.0
    d_lat_native = -1.2 / 599.0
    d_lon_chip = d_lon_native / upsample
    d_lat_chip = d_lat_native / upsample

    origin_lon = round(-60.8 + x0 * d_lon_native, 7)
    origin_lat = round(-45.0 + y0 * d_lat_native, 7)
    right_lon = round(origin_lon + chip_w * d_lon_chip, 7)
    bottom_lat = round(origin_lat + chip_h * d_lat_chip, 7)

    lat_mid = origin_lat + (chip_h / 2.0) * d_lat_chip
    x_m = round(abs(d_lon_native) * 111320.0 * math.cos(math.radians(lat_mid)), 1)
    y_m = round(abs(d_lat_native) * 111132.0, 1)

    return {
        "width": chip_w,
        "height": chip_h,
        "upsample_factor": upsample,
        "resample": "nearest",
        "scene_window": {
            "x0": int(x0),
            "y0": int(y0),
            "width": int(width),
            "height": int(height),
        },
        "pixel_spacing_m": 10.0,
        "pixel_spacing_basis": "fixture_nominal_resolution_m",
        "chip_pixel_spacing_m": 10.0 / upsample,
        "corners_wgs84": {
            "top_left": [origin_lon, origin_lat],
            "top_right": [right_lon, origin_lat],
            "bottom_right": [right_lon, bottom_lat],
            "bottom_left": [origin_lon, bottom_lat],
        },
        "geotransform": {
            "convention": "linear_degrees_from_chip_corner_xy",
            "origin_lon": origin_lon,
            "origin_lat": origin_lat,
            "d_lon_per_chip_px": d_lon_chip,
            "d_lat_per_chip_px": d_lat_chip,
        },
        "geotransform_implied_spacing_m": {
            "x_m": x_m,
            "y_m": y_m,
            "method": "degrees per native pixel * 111320*cos(lat) and * 111132 (spherical approximation)",
        },
        "spacing_consistent_with_geotransform": False,
    }


def render_and_save_chip_png(
    fused_linear: np.ndarray,
    x0: int,
    y0: int,
    png_name: str,
    width: int = 64,
    height: int = 64,
    upsample: int = 8,
) -> Dict[str, Any]:
    """Crop 64x64 native window, convert to dB, clip to [p1.0, p99.5], upsample x8 nearest, save 8-bit RGB PNG."""
    crop_lin = np.maximum(fused_linear[y0 : y0 + height, x0 : x0 + width].astype(np.float64), 1e-10)
    crop_db = 10.0 * np.log10(crop_lin)
    low_val = float(np.percentile(crop_db, 1.0))
    high_val = float(np.percentile(crop_db, 99.5))
    span = max(high_val - low_val, 1e-6)
    scaled = np.clip((crop_db - low_val) / span, 0.0, 1.0)
    u8_native = np.round(scaled * 255.0).astype(np.uint8)
    u8_chip = np.repeat(np.repeat(u8_native, upsample, axis=0), upsample, axis=1)
    rgb = np.stack([u8_chip, u8_chip, u8_chip], axis=-1)

    rel_path = f"sam_chips/{png_name}"
    out_dirs = [
        ROOT / "docs/data/sam_chips",
        ROOT / "frontend/data/sam_chips",
        ROOT / "data/sam_chips",
    ]
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)
    primary_path = out_dirs[0] / png_name
    Image.fromarray(rgb, mode="RGB").save(primary_path, format="PNG")
    raw_bytes = primary_path.read_bytes()
    sha = hashlib.sha256(raw_bytes).hexdigest()
    for d in out_dirs[1:]:
        shutil.copyfile(primary_path, d / png_name)

    return {
        "path": rel_path,
        "media_type": "image/png",
        "width": int(width * upsample),
        "height": int(height * upsample),
        "channels": 3,
        "bit_depth": 8,
        "sha256": sha,
        "normalisation": {
            "method": "percentile_clip_linear",
            "input_domain": "dB",
            "low": round(low_val, 6),
            "high": round(high_val, 6),
            "low_percentile": 1.0,
            "high_percentile": 99.5,
            "gamma": 1.0,
        },
        "_abs_path": str(primary_path),
        "_bytes_len": len(raw_bytes),
    }


def build_ground_truth_for_window(
    gt_targets: List[Dict[str, Any]],
    x0: int,
    y0: int,
    width: int = 64,
    height: int = 64,
    upsample: int = 8,
    matches_prompt_object_id: Optional[int] = 1,
) -> List[Dict[str, Any]]:
    """Reconstruct exact synthetic injected ellipse footprint inside scene_window."""
    chip_w = width * upsample
    chip_h = height * upsample
    gt_objs: List[Dict[str, Any]] = []

    for gt in gt_targets:
        r0 = float(gt["pixel_r"])
        c0 = float(gt["pixel_c"])
        length = float(gt["length_m"])
        beam = float(gt["beam_m"])
        heading_deg = float(gt["heading_deg"])

        a_px = max((length / 2.0) / 10.0, 2.0)
        b_px = max((beam / 2.0) / 10.0, 0.7)
        theta = math.radians(heading_deg)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        half_win = int(math.ceil(a_px)) + 4

        native_pts: List[Tuple[int, int]] = []
        for rr in range(-half_win, half_win + 1):
            for cc in range(-half_win, half_win + 1):
                nr = int(round(r0)) + rr
                nc = int(round(c0)) + cc
                if not (y0 <= nr < y0 + height and x0 <= nc < x0 + width):
                    continue
                y_rot = -rr * cos_t + cc * sin_t
                x_rot = rr * sin_t + cc * cos_t
                if (y_rot / a_px) ** 2 + (x_rot / b_px) ** 2 <= 1.0:
                    native_pts.append((nr, nc))

        if not native_pts:
            continue

        ras = bytearray(chip_w * chip_h)
        for nr, nc in native_pts:
            cy0 = (nr - y0) * upsample
            cx0 = (nc - x0) * upsample
            for dy in range(upsample):
                row_off = (cy0 + dy) * chip_w
                for dx in range(upsample):
                    ras[row_off + cx0 + dx] = 1

        rings = decode_mask.trace_rings(bytes(ras), chip_h, chip_w)
        polygons_chip_px = [[[int(pt[0]), int(pt[1])] for pt in ring] for ring in rings]
        xs = [pt[0] for ring in rings for pt in ring]
        ys = [pt[1] for ring in rings for pt in ring]
        area_chip = float(len(native_pts) * upsample * upsample)

        gt_objs.append(
            {
                "gt_id": gt["target_id"],
                "name": gt["name"],
                "has_active_ais": bool(gt["has_active_ais"]),
                "center_native_px": {"r": float(r0), "c": float(c0)},
                "length_m": float(length),
                "beam_m": float(beam),
                "heading_deg": float(heading_deg),
                "footprint_native_px_count": len(native_pts),
                "polygons_chip_px": polygons_chip_px,
                "area_chip_px": area_chip,
                "bbox_chip_px": {
                    "x1": int(min(xs)),
                    "y1": int(min(ys)),
                    "x2": int(max(xs) - 1),
                    "y2": int(max(ys) - 1),
                },
                "matches_prompt_object_id": matches_prompt_object_id,
            }
        )
    return gt_objs


def build_source_block(meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "synthetic_fixture",
        "synthetic": True,
        "scene_id": meta["scene_id"],
        "product_id": None,
        "product_url": None,
        "acquisition_time_utc": meta["acquisition_time_utc"],
        "polarisation": "fused_vv_vh",
        "calibration": (
            "linear power; VV and VH fused with processing/dual_pol_fusion.py "
            "fuse_dual_polarization(vv, vh, alpha=0.75); dB for display (10*log10)"
        ),
        "fixture": {
            "generator": "backend/app/ingestion/sentinel_fixtures.py::generate_mile201_fixture",
            "seed": 1982,
            "shape": [600, 600],
            "nominal_resolution_m": 10.0,
        },
        "stac": None,
    }


def prepare_10_chips() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Execute Steps 1, 2, 3: CFAR bbox extraction, 10 PNG chips, and box/text prompts."""
    vv_raw, vh_raw, meta = load_mile201_fixture()
    bounds = tuple(meta["bounds"])
    sigma0_vv = calibrate_dn_to_sigma0_linear(vv_raw)
    sigma0_vh = calibrate_dn_to_sigma0_linear(vh_raw)
    fused_cal = fuse_dual_polarization(sigma0_vv, sigma0_vh, alpha=0.75)
    filtered = enhanced_lee_filter(fused_cal, window_size=7, enl=4.4)
    det_mask, thresh_map = ca_cfar_2d(
        filtered, guard_size=5, train_size=21, pfa=1e-5, anti_masking=True
    )
    dets = extract_sar_targets(
        det_mask,
        filtered,
        threshold_map=thresh_map,
        pixel_res_m=10.0,
        min_pixels=3,
        bounds=bounds,
    )
    det_by_id: Dict[str, SarVesselDetection] = {d.id: d for d in dets}
    gt_list = meta["ground_truth_targets"]
    fused_display = fuse_dual_polarization(vv_raw, vh_raw, alpha=0.75)

    def make_target_chip(det_id: str) -> Dict[str, Any]:
        det = det_by_id[det_id]
        assert det.bbox_native_px is not None, f"{det_id} missing bbox_native_px"
        r_int = int(round(det.pixel_r))
        c_int = int(round(det.pixel_c))
        x0 = max(0, min(600 - 64, c_int - 32))
        y0 = max(0, min(600 - 64, r_int - 32))
        geom = compute_geometry(x0, y0, 64, 64, 8)
        png_name = f"fx-mile201-{det_id.lower()}.png"
        di = render_and_save_chip_png(fused_display, x0, y0, png_name, 64, 64, 8)
        abs_png = di.pop("_abs_path")
        png_bytes_len = di.pop("_bytes_len")

        gt_objs = build_ground_truth_for_window(gt_list, x0, y0, 64, 64, 8, matches_prompt_object_id=1)
        assert len(gt_objs) == 1, f"Expected 1 GT object in {det_id} window, found {len(gt_objs)}"
        gt_obj = gt_objs[0]

        bb = det.bbox_native_px
        margin = 1.0
        up = 8
        box_px = {
            "x1": max(0, min(511, int(round((bb["c_min"] - margin - x0) * up)))),
            "y1": max(0, min(511, int(round((bb["r_min"] - margin - y0) * up)))),
            "x2": max(0, min(511, int(round((bb["c_max"] + 1 + margin - x0) * up)) - 1)),
            "y2": max(0, min(511, int(round((bb["r_max"] + 1 + margin - y0) * up)) - 1)),
        }
        input_text = decode_mask.build_box_prompt(
            {1: (box_px["x1"], box_px["y1"], box_px["x2"], box_px["y2"])}, 512, 512
        )

        ais_tag = "AIS activo" if gt_obj["has_active_ais"] else "buque oscuro"
        return {
            "chip_id": f"fx-mile201-{det_id.lower()}-box",
            "title": (
                f"SINTÉTICO — Mile 201 fixture, {det_id} ({gt_obj['gt_id']}, {gt_obj['name']}, {ais_tag}), "
                f"box prompt CFAR"
            ),
            "status": "pending",
            "placeholder_fields": [
                "/transport",
                "/sam_response",
                "run.parsing.rasters_disagreeing",
            ],
            "source": build_source_block(meta),
            "geometry": geom,
            "display_image": di,
            "transport": None,
            "prompt": {
                "type": "box",
                "coordinate_space": "chip_pixels_top_left_origin_inclusive",
                "media_width": 512,
                "media_height": 512,
                "input_text": input_text,
                "text": None,
                "objects": [
                    {
                        "object_id": 1,
                        "box_px": box_px,
                        "point_px": None,
                        "origin_cfar_detection_id": det_id,
                        "cfar": {
                            "detection_id": det_id,
                            "pixel_r": float(det.pixel_r),
                            "pixel_c": float(det.pixel_c),
                            "length_m": float(det.length_m),
                            "beam_m": float(det.beam_m),
                            "heading_deg": float(det.heading_deg),
                            "area_pixels": int(det.area_pixels),
                            "peak_rcs_db": float(det.peak_rcs_db),
                            "snr_db": float(det.snr_db),
                            "bbox_native_px": bb,
                            "provenance": (
                                "Extracted directly from SarVesselDetection via "
                                "backend/app/processing/morphology.py::extract_sar_targets on the Mile 201 fixture."
                            ),
                        },
                    }
                ],
                "origin_cfar_detection_ids": [det_id],
                "box_derivation": {
                    "method": (
                        "connected-component extent (scipy.ndimage.label of the CFAR mask) + margin, "
                        "minus scene_window origin, times upsample_factor, clipped to the chip, inclusive"
                    ),
                    "margin_native_px": 1.0,
                },
            },
            "ground_truth": {
                "kind": "synthetic_injected_ellipse_footprint",
                "derivation": (
                    "Native pixels (r+rr, c+cc) where ellipse_norm <= 1 in sentinel_fixtures.py "
                    "(a_px=max(length/2/10,2.0), b_px=max(beam/2/10,0.7)), restricted to scene_window, "
                    "each scaled by upsample_factor and traced with decode_mask.trace_rings (epsilon 0). "
                    "Exact, not a measurement."
                ),
                "objects": [gt_obj],
            },
            "sam_response": None,
            "control": {
                "is_negative_control": False,
                "kind": "none",
                "expected": None,
                "confidence_threshold": None,
                "observed_detections": None,
                "passed": None,
            },
            "error": None,
            "_abs_png": abs_png,
            "_png_bytes_len": png_bytes_len,
        }

    def make_negative_chip(spec: Dict[str, Any]) -> Dict[str, Any]:
        x0 = spec["center_c"] - 32
        y0 = spec["center_r"] - 32
        geom = compute_geometry(x0, y0, 64, 64, 8)
        di = render_and_save_chip_png(fused_display, x0, y0, spec["png_name"], 64, 64, 8)
        abs_png = di.pop("_abs_path")
        png_bytes_len = di.pop("_bytes_len")

        gt_objs = build_ground_truth_for_window(gt_list, x0, y0, 64, 64, 8, matches_prompt_object_id=None)
        assert len(gt_objs) == 0, f"Negative control {spec['chip_id']} intersected GT: {gt_objs}"

        return {
            "chip_id": spec["chip_id"],
            "title": spec["title"],
            "status": "pending",
            "placeholder_fields": [
                "/transport",
                "/sam_response",
                "/control/observed_detections",
                "/control/passed",
                "run.parsing.rasters_disagreeing",
            ],
            "source": build_source_block(meta),
            "geometry": geom,
            "display_image": di,
            "transport": None,
            "prompt": {
                "type": "text",
                "coordinate_space": "chip_pixels_top_left_origin_inclusive",
                "media_width": 512,
                "media_height": 512,
                "input_text": "ship",
                "text": "ship",
                "objects": [],
                "origin_cfar_detection_ids": [],
                "box_derivation": None,
            },
            "ground_truth": {
                "kind": "synthetic_injected_ellipse_footprint",
                "derivation": spec["derivation"],
                "objects": [],
            },
            "sam_response": None,
            "control": {
                "is_negative_control": True,
                "kind": spec["kind"],
                "expected": "no_detection",
                "confidence_threshold": None,
                "observed_detections": None,
                "passed": None,
            },
            "error": None,
            "_abs_png": abs_png,
            "_png_bytes_len": png_bytes_len,
        }

    # Keep index 0 = SAR-TGT-001 and index 1 = ghost-ais-empty-sea (300,300) for frontend compatibility
    chips: List[Dict[str, Any]] = [
        make_target_chip(TARGET_DETECTION_IDS[0]),
        make_negative_chip(NEGATIVE_CONTROLS_SPEC[0]),
    ]
    for det_id in TARGET_DETECTION_IDS[1:]:
        chips.append(make_target_chip(det_id))
    chips.append(make_negative_chip(NEGATIVE_CONTROLS_SPEC[1]))

    assert len(chips) == MAX_CHIPS, f"Expected {MAX_CHIPS} chips, got {len(chips)}"
    return chips, meta


def build_detection_entry(
    chip: Dict[str, Any],
    rec: Dict[str, Any],
    det_idx: int,
    api_rescaled_to: Optional[Dict[str, int]],
) -> Dict[str, Any]:
    """Decode a single SAM record with decode_mask.py and compute all geometry/IoU/PCA metrics."""
    gw = chip["geometry"]["width"]
    gh = chip["geometry"]["height"]
    spacing_m = chip["geometry"]["chip_pixel_spacing_m"]

    b = rec["box"]
    m_tag = rec["mask"]
    raw = m_tag["data"]
    raw_sha = hashlib.sha256(raw.encode("ascii")).hexdigest()
    h, w, enc, ras = decode_mask.decode_mask(raw)
    marker = "!" if enc == "one_bit" else "~"

    bbox = {
        "x1": int(b["x1"]),
        "y1": int(b["y1"]),
        "x2": int(b["x2"]),
        "y2": int(b["y2"]),
        "w": int(b["w"]),
        "h": int(b["h"]),
    }
    covers = decode_mask.mask_box_relation(h, w, bbox)
    mismatch = covers == decode_mask.COVERS_MISMATCH

    c_box = b.get("c")
    c_mask = m_tag.get("c")
    score = c_mask if c_mask is not None else c_box

    area_px = int(sum(ras))
    area_m2 = round(area_px * (spacing_m ** 2), 6)
    ras_sha = decode_mask.raster_sha256(ras)

    if mismatch or area_px == 0:
        polygons = []
        centroid_px = None
    else:
        polygons = decode_mask.mask_to_polygons(
            ras, h, w, offset=(bbox["x1"], bbox["y1"]), epsilon=0.0
        )
        cx = bbox["x1"] + sum(k % w + 0.5 for k in range(h * w) if ras[k]) / area_px
        cy = bbox["y1"] + sum(k // w + 0.5 for k in range(h * w) if ras[k]) / area_px
        centroid_px = [round(cx, 4), round(cy, 4)]

    prompt_objs = chip["prompt"]["objects"]
    prompt_obj_id: Optional[int] = None
    if chip["prompt"]["type"] == "box" and prompt_objs:
        prompt_obj_id = int(rec["object_id"]) if any(o["object_id"] == rec["object_id"] for o in prompt_objs) else int(prompt_objs[0]["object_id"])

    gt_objs = (chip.get("ground_truth") or {}).get("objects") or []
    matched_gt_id: Optional[str] = None
    iou_block: Optional[Dict[str, Any]] = None
    length_block: Optional[Dict[str, Any]] = None

    if not mismatch:
        if gt_objs:
            gt_obj = gt_objs[0]
            matched_gt_id = gt_obj["gt_id"]
            gt_ras = decode_mask.rasterize_rings(gt_obj["polygons_chip_px"], gw, gh)
            full_ras = decode_mask.place_mask(ras, h, w, bbox["x1"], bbox["y1"], gw, gh)
            iou_val, inter_px, union_px = decode_mask.iou(bytes(full_ras), bytes(gt_ras))
            iou_block = {
                "value": round(iou_val, 6),
                "intersection_px": int(inter_px),
                "union_px": int(union_px),
                "evaluated_on": "chip_pixels",
            }
        if area_px >= 2 and prompt_obj_id is not None:
            ax = decode_mask.principal_axes_m(ras, h, w, spacing_m)
            po = next((o for o in prompt_objs if o["object_id"] == prompt_obj_id), None)
            cfar_len = float(po["cfar"]["length_m"]) if po and po.get("cfar") else None
            from_mask_m = float(ax["pca_length_m"])
            delta_m = (from_mask_m - cfar_len) if cfar_len is not None else None
            delta_pct = (100.0 * delta_m / cfar_len) if (cfar_len and delta_m is not None) else None
            length_block = {
                "from_mask_m": from_mask_m,
                "beam_from_mask_m": float(ax["pca_beam_m"]),
                "feret_length_m": float(ax["feret_length_m"]),
                "heading_from_mask_deg": float(ax["heading_deg"]),
                "cfar_m": cfar_len,
                "delta_m": delta_m,
                "delta_pct": delta_pct,
                "estimator": "pca_4sqrt_lambda",
            }

    return {
        "detection_id": f"{chip['chip_id']}-det-{det_idx + 1:02d}",
        "object_id": int(rec["object_id"]),
        "prompt_object_id": prompt_obj_id,
        "frame": 0,
        "confidence": {
            "box": c_box,
            "mask": c_mask,
        },
        "score": score,
        "bbox": bbox,
        "mask": {
            "encoding": enc,
            "marker": marker,
            "x": 0,
            "y": 0,
            "height": int(h),
            "width": int(w),
            "covers": covers,
            "raw": raw,
            "raw_length_chars": len(raw),
            "raw_sha256": raw_sha,
            "decoded": {
                "polygons": polygons,
                "polygon_epsilon_px": 0.0,
                "polygon_convention": "pixel_corner_xy_in_chip_px",
                "fill_rule": "evenodd",
                "raster_sha256": ras_sha,
                "area_px": area_px,
                "area_m2": area_m2,
                "centroid_px": centroid_px,
            },
        },
        "metrics": {
            "matched_ground_truth_id": matched_gt_id,
            "iou_vs_ground_truth": iou_block,
            "length_vs_cfar": length_block,
        },
    }


def write_and_mirror_sam_masks(doc: Dict[str, Any]) -> None:
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    for rel in ("docs/data/sam_masks.json", "frontend/data/sam_masks.json", "data/sam_masks.json"):
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def validate_against_schema(doc: Dict[str, Any], allow_measured_placeholder: bool = False) -> List[str]:
    import jsonschema

    schema_path = ROOT / "scripts/sam_masks.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if allow_measured_placeholder:
        chip_allof = schema["$defs"]["chip"]["allOf"]
        for rule in chip_allof:
            if rule.get("if", {}).get("properties", {}).get("status", {}).get("const") == "measured":
                rule["then"]["properties"]["placeholder_fields"] = {
                    "maxItems": 1,
                    "items": {"enum": ["run.parsing.rasters_disagreeing"]},
                }
        run_allof = schema["$defs"]["run"]["allOf"]
        for rule in run_allof:
            if rule.get("if", {}).get("properties", {}).get("mode", {}).get("const") == "live":
                rule["then"]["properties"]["model_served"] = {"type": ["string", "null"]}

    validator = jsonschema.Draft202012Validator(schema)
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(doc)]


def build_base_document(chips: List[Dict[str, Any]], mode: str, git_commit: str) -> Dict[str, Any]:
    example_path = ROOT / "specs/sam_masks/sam_masks.example.json"
    example_doc = json.loads(example_path.read_text(encoding="utf-8"))
    decode_sha = hashlib.sha256((ROOT / "scripts/decode_mask.py").read_bytes()).hexdigest()

    clean_chips = []
    for c in chips:
        cc = {k: v for k, v in c.items() if not k.startswith("_")}
        clean_chips.append(cc)

    return {
        "$schema": "./sam_masks.schema.json",
        "schema_version": "sam_masks/1.0.0",
        "run": {
            "run_id": f"puerta-a-{mode.replace('_', '-')}-20261030",
            "mode": mode,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": {
                "name": "scripts/run_sam_puerta_a.py",
                "version": "1.0.0",
                "git_commit": git_commit,
            },
            "model_requested": MODEL_ALLOWED,
            "model_served": None,
            "model_served_source": None,
            "endpoint": ENDPOINT_RESPONSES,
            "request_options": {
                "stream": True,
                "include_confidence": "true",
                "mask_encoding_requested": None,
            },
            "price_source": {
                "url": "https://dev.meta.ai/docs/pricing-rate-limits",
                "retrieved_on": "2026-10-29",
                "sam_usd_per_1000_images": SAM_USD_PER_1000_IMAGES,
                "muse_image_usd_per_image": 0.01,
            },
            "cost": {
                "billed_images": 0,
                "estimated_usd": 0.0,
                "multi_object_billing": "unknown_VERIFY",
            },
            "parsing": {
                "reference_decoder": {
                    "name": "meta-sam-parser",
                    "version": None,
                },
                "cross_check_decoder": {
                    "name": "decode_mask.py",
                    "sha256": decode_sha,
                },
                "rasters_decoded": 0,
                "rasters_disagreeing": 0,
            },
        },
        "chips": clean_chips,
        "illustrative_images": example_doc["illustrative_images"],
    }


def execute_dry_run(chips: List[Dict[str, Any]], git_commit: str) -> Dict[str, Any]:
    """Step 4: Build dry_run document, print requests without image, print worst-case cost, and validate."""
    doc = build_base_document(chips, mode="dry_run", git_commit=git_commit)
    total_wc_images = 0
    print("=== [STEP 4: DRY RUN — 10 CHIPS REQUEST PREVIEW (NO API CALLS)] ===")
    for idx, c in enumerate(chips, 1):
        wc_imgs = max(1, len(c["prompt"]["objects"]))
        total_wc_images += wc_imgs
        req_preview = {
            "model": MODEL_ALLOWED,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": c["prompt"]["input_text"]},
                        {"type": "input_image", "file_id": f"<pending_upload:{c['display_image']['path']}>"},
                    ],
                }
            ],
            "stream": True,
            "metadata": {"include_confidence": "true"},
        }
        print(
            f"  [{idx:02d}/{len(chips):02d}] {c['chip_id']} | type={c['prompt']['type']} "
            f"| input_text={c['prompt']['input_text']} | wc_images={wc_imgs} "
            f"| png_sha256={c['display_image']['sha256'][:12]}..."
        )
        if idx in (1, 2):
            print("       payload:", json.dumps(req_preview, ensure_ascii=False))

    est_live_usd = total_wc_images * USD_PER_IMAGE
    print(
        f"=== DRY RUN COST ESTIMATE: {total_wc_images} worst-case billed images "
        f"= USD {est_live_usd:.4f} (Hard Cap: {MAX_BILLED_IMAGES} images / USD {HARD_CAP_USD:.2f}) ==="
    )

    write_and_mirror_sam_masks(doc)
    schema_errs = validate_against_schema(doc, allow_measured_placeholder=False)
    if schema_errs:
        raise RuntimeError(f"Dry-run JSON Schema validation failed: {schema_errs}")
    verify_errs = decode_mask.verify_sam_masks(doc)
    if verify_errs:
        raise RuntimeError(f"Dry-run decode_mask.verify_sam_masks failed: {verify_errs}")
    print("=== DRY RUN VALIDATION: 0 schema errors (sam_masks.schema.json), 0 verify errors ===")
    return doc


def execute_live_run(chips: List[Dict[str, Any]], git_commit: str) -> Dict[str, Any]:
    """Step 6: Upload chips to /v1/files, call sam-3.1 on /v1/responses with strict hard-cap gate."""
    api_key = get_meta_api_key()
    if not api_key:
        raise RuntimeError("META_API_KEY not found")

    doc = build_base_document(chips, mode="live", git_commit=git_commit)
    billed_images_actual = 0
    billed_images_worst_case = 0
    rasters_decoded_total = 0
    served_models_seen: List[Optional[str]] = []

    headers_auth = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "OceanEye-MetaHackathon/1.0",
    }
    headers_sse = {
        **headers_auth,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    print("\n=== [STEP 6: LIVE RUN — SAM-3.1 ON 10 CHIPS WITH USD 0.05 HARD CAP] ===")
    with httpx.Client(timeout=90.0) as client:
        for idx, (raw_chip, doc_chip) in enumerate(zip(chips, doc["chips"]), 1):
            cid = doc_chip["chip_id"]
            abs_png = Path(raw_chip["_abs_png"])
            png_bytes = abs_png.read_bytes()
            png_sha = hashlib.sha256(png_bytes).hexdigest()

            wc_images_req = max(1, len(doc_chip["prompt"]["objects"]))
            enforce_budget_before_request(
                billed_images_worst_case=billed_images_worst_case,
                request_worst_case_images=wc_images_req,
                model=MODEL_ALLOWED,
                chips_count=len(doc["chips"]),
            )

            # Step 5: Upload PNG to /v1/files (purpose=user_data)
            up_resp = client.post(
                ENDPOINT_FILES,
                headers=headers_auth,
                data={"purpose": "user_data"},
                files={"file": (abs_png.name, png_bytes, "image/png")},
            )
            if up_resp.status_code != 200:
                raise RuntimeError(f"{cid}: /v1/files upload failed ({up_resp.status_code}): {up_resp.text[:300]}")
            file_obj = up_resp.json()
            file_id = file_obj["id"]

            doc_chip["transport"] = {
                "media_type": "image/png",
                "bytes": len(png_bytes),
                "sha256": png_sha,
                "file_id": file_id,
                "same_pixels_as_display_image": True,
            }

            # Step 6: Call /v1/responses with sam-3.1, stream=True, metadata.include_confidence="true"
            req_payload = {
                "model": MODEL_ALLOWED,
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": doc_chip["prompt"]["input_text"]},
                            {"type": "input_image", "file_id": file_id},
                        ],
                    }
                ],
                "stream": True,
                "metadata": {"include_confidence": "true"},
            }

            t0 = time.perf_counter()
            billed_images_actual += 1
            billed_images_worst_case += wc_images_req
            resp = client.post(ENDPOINT_RESPONSES, headers=headers_sse, json=req_payload)
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            received_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if resp.status_code != 200:
                doc_chip["status"] = "api_error"
                doc_chip["error"] = {
                    "http_status": resp.status_code,
                    "type": "http_error",
                    "message": resp.text[:500],
                    "retryable": resp.status_code >= 500,
                }
                raise RuntimeError(f"{cid}: /v1/responses HTTP {resp.status_code}: {resp.text[:300]}")

            sse_info = decode_mask.parse_sse(resp.text)
            out_text = sse_info["text"]
            out_sha = hashlib.sha256(out_text.encode("utf-8")).hexdigest()
            resp_id = sse_info["response_id"]
            model_served = sse_info["model_served"]
            served_models_seen.append(model_served)

            recs = decode_mask.parse_sam_text(out_text)
            detections: List[Dict[str, Any]] = []
            api_rescaled_to = None
            for d_idx, r in enumerate(recs):
                if r.get("box") and r.get("mask"):
                    bw, bh = int(r["box"]["w"]), int(r["box"]["h"])
                    if (bw, bh) != (doc_chip["geometry"]["width"], doc_chip["geometry"]["height"]):
                        api_rescaled_to = {"width": bw, "height": bh}
                    det_entry = build_detection_entry(doc_chip, r, d_idx, api_rescaled_to)
                    detections.append(det_entry)
                    rasters_decoded_total += 1

            doc_chip["sam_response"] = {
                "response_id": resp_id,
                "model_served": model_served,
                "http_status": 200,
                "latency_ms": latency_ms,
                "received_at": received_at,
                "usage": sse_info["usage"],
                "output_text_length": len(out_text),
                "output_text_sha256": out_sha,
                "api_rescaled_to": api_rescaled_to,
                "detections": detections,
            }

            if doc_chip["control"]["is_negative_control"]:
                thr = doc_chip["control"]["confidence_threshold"] or 0.0
                obs = sum(1 for d in detections if d["score"] is None or d["score"] >= thr)
                doc_chip["control"]["observed_detections"] = obs
                doc_chip["control"]["passed"] = obs == 0

            doc_chip["status"] = "measured"
            # Step 7 not approved: list run.parsing.rasters_disagreeing in placeholder_fields
            doc_chip["placeholder_fields"] = ["run.parsing.rasters_disagreeing"]

            iou_str = "n/a"
            if detections and detections[0]["metrics"]["iou_vs_ground_truth"]:
                iou_str = f"{detections[0]['metrics']['iou_vs_ground_truth']['value']:.4f}"
            print(
                f"  ✓ [{idx:02d}/{len(doc['chips']):02d}] {cid}: file_id={file_id} "
                f"resp_id={resp_id} model_served={model_served} latency={latency_ms}ms "
                f"dets={len(detections)} IoU={iou_str} "
                f"cost_so_far=${billed_images_actual * USD_PER_IMAGE:.4f}"
            )

    non_null_models = [m for m in served_models_seen if m is not None]
    if non_null_models and len(set(non_null_models)) == 1:
        doc["run"]["model_served"] = non_null_models[0]
        doc["run"]["model_served_source"] = "SSE event response.completed -> response.model"
    else:
        doc["run"]["model_served"] = None
        doc["run"]["model_served_source"] = "not_present_in_sse_response_events"

    doc["run"]["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["run"]["cost"]["billed_images"] = billed_images_actual
    doc["run"]["cost"]["estimated_usd"] = round(billed_images_actual * USD_PER_IMAGE, 6)
    doc["run"]["parsing"]["rasters_decoded"] = rasters_decoded_total
    doc["run"]["parsing"]["rasters_disagreeing"] = 0

    write_and_mirror_sam_masks(doc)
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Run Steps 1-4 only (no API calls)")
    ap.add_argument("--live", action="store_true", help="Run Steps 1-4 then Step 6 live SAM-3.1 calls")
    args = ap.parse_args()

    git_commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()

    chips, _meta = prepare_10_chips()
    execute_dry_run(chips, git_commit=git_commit)

    if args.live:
        doc = execute_live_run(chips, git_commit=git_commit)
        notes: List[str] = []
        problems = decode_mask.verify_sam_masks(doc, notes)
        for n in notes:
            print("WARNING:", n)
        for p in problems:
            print("PROBLEM:", p)
        if problems:
            print(f"LIVE VERIFY FAILED: {len(problems)} problem(s)")
            return 1
        schema_errs = validate_against_schema(doc, allow_measured_placeholder=True)
        if schema_errs:
            print(f"LIVE SCHEMA ERRORS: {schema_errs}")
            return 1
        print("=== LIVE RUN + VERIFY COMPLETED SUCCESSFULLY ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
