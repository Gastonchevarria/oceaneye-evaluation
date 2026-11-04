"""
Offline SAR Scene Fixtures & Synthetic Data Generator for Evaluation and OceanEye Sentinel 2.0 Evaluation.

Provides zero-dependency, reproducible sub-scenes of Sentinel-1 IW GRD Dual-Pol
over the Argentine Mile 201 corridor (South Atlantic - Agujero Azul).

Enables instant evaluation without requiring external credentials or downloading
1.8 GB files during tests or jury demonstrations.
"""

import os
import json
import numpy as np
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, List

FIXTURES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures")
)
FIXTURE_FILE = os.path.join(FIXTURES_DIR, "mile201_sentinel1_dualpol.npz")
METADATA_FILE = os.path.join(FIXTURES_DIR, "fixture_metadata.json")


def generate_mile201_fixture(shape: Tuple[int, int] = (600, 600)) -> Dict[str, Any]:
    """
    Generate or retrieve the calibrated Sentinel-1 dual-pol sub-scene of Mile 201.
    Contains exactly 15 SAR vessels:
      - 3 Vessels with active AIS broadcasts (AIS_CONFIRMED)
      - 12 Dark Vessels with disabled transponders (DARK_VESSEL - IUU suspect fleet)
      - Plus 1 Ghost AIS transponder broadcasting with no physical radar target.
    """
    os.makedirs(FIXTURES_DIR, exist_ok=True)

    np.random.seed(1982)  # Deterministic seed for reproducible scientific benchmarks
    h, w = shape

    # Geographic bounds: Argentine Mile 201 Corridor (Agujero Azul)
    # min_lon, min_lat, max_lon, max_lat
    bounds = (-60.80, -46.20, -59.60, -45.00)

    # 1. Realistic Sea Clutter Modeling
    # In C-band SAR:
    # VV has higher Bragg backscatter from waves (mean power ~ 8.0 - 12.0)
    # VH has low sea backscatter (mean power ~ 0.8 - 1.5)
    vv = np.random.exponential(scale=10.0, size=(h, w)).astype(np.float32)
    vh = np.random.exponential(scale=1.2, size=(h, w)).astype(np.float32)

    # Add gentle sea swell / ocean wave modulation pattern
    x = np.linspace(0, 10 * np.pi, w)
    y = np.linspace(0, 10 * np.pi, h)
    xx, yy = np.meshgrid(x, y)
    wave_pattern = (np.sin(xx * 0.8 + yy * 0.5) * 0.25 + 1.0).astype(np.float32)
    vv *= wave_pattern

    # 2. Vessel Coordinates & Fleet Ground Truth
    # 15 distinct vessel locations
    # (r, c, length_m, beam_m, heading_deg, is_ais, name, mmsi)
    vessels_spec = [
        # 3 AIS Confirmed Vessels
        (120, 150, 75.0, 13.0, 170.0, True, "LU RONG YU 588", "413294810"),
        (220, 310, 68.0, 12.0, 165.0, True, "FU YUAN YU 7881", "413388120"),
        (480, 240, 72.0, 12.5, 175.0, True, "ZHONG YU 218", "413491029"),

        # 12 Dark Vessels (Turned off transponders inside/bordering EEZ)
        (80, 420, 65.0, 11.0, 180.0, False, "DARK POTERO 01", None),
        (140, 435, 62.0, 10.5, 185.0, False, "DARK POTERO 02", None),
        (190, 450, 70.0, 12.0, 170.0, False, "DARK POTERO 03", None),
        (250, 465, 78.0, 13.0, 160.0, False, "DARK TRAWLER 04", None),
        (280, 180, 58.0, 10.0, 190.0, False, "DARK POTERO 05", None),
        (330, 210, 64.0, 11.5, 175.0, False, "DARK POTERO 06", None),
        (370, 390, 85.0, 14.0, 165.0, False, "DARK REEFER 07", None),
        (410, 420, 66.0, 11.0, 180.0, False, "DARK POTERO 08", None),
        (440, 150, 60.0, 10.0, 170.0, False, "DARK POTERO 09", None),
        (490, 480, 73.0, 12.0, 185.0, False, "DARK POTERO 10", None),
        (530, 320, 68.0, 11.0, 175.0, False, "DARK POTERO 11", None),
        (560, 270, 75.0, 13.0, 165.0, False, "DARK POTERO 12", None),
    ]

    min_lon, min_lat, max_lon, max_lat = bounds
    t_sar = datetime(2026, 4, 15, 14, 2, 30, tzinfo=timezone.utc)

    ground_truth_targets = []
    ais_reports = []
    res_m = 10.0

    for idx, (r, c, length, beam, heading, has_ais, name, mmsi) in enumerate(vessels_spec, 1):
        # Pixel coordinates to Lat/Lon
        lat = max_lat - (r / (h - 1)) * (max_lat - min_lat)
        lon = min_lon + (c / (w - 1)) * (max_lon - min_lon)

        # Inject realistic elongated naval hull radar return
        # Metallic hull creates high cross-polarization (VH) return (40 to 80 dB power)
        # and moderate VV return, with length-to-beam ratio matching naval architecture
        a_px = max((length / 2.0) / res_m, 2.0)
        b_px = max((beam / 2.0) / res_m, 0.7)
        theta = np.radians(heading)
        rad = int(np.ceil(a_px)) + 1
        rr, cc = np.ogrid[-rad:rad+1, -rad:rad+1]

        u = -rr * np.cos(theta) + cc * np.sin(theta)
        v = rr * np.sin(theta) + cc * np.cos(theta)
        ellipse_norm = (u / a_px)**2 + (v / b_px)**2
        mask_ship = ellipse_norm <= 1.0

        # Bounds check
        r_min, r_max = max(0, r - rad), min(h, r + rad + 1)
        c_min, c_max = max(0, c - rad), min(w, c + rad + 1)

        mr_min = r_min - (r - rad)
        mr_max = mr_min + (r_max - r_min)
        mc_min = c_min - (c - rad)
        mc_max = mc_min + (c_max - c_min)
        sub_mask = mask_ship[mr_min:mr_max, mc_min:mc_max]
        sub_norm = ellipse_norm[mr_min:mr_max, mc_min:mc_max]
        weight = np.clip(1.0 - 0.25 * sub_norm, 0.5, 1.0).astype(np.float32)

        vv[r_min:r_max, c_min:c_max][sub_mask] += (float(np.random.uniform(180.0, 340.0)) * weight[sub_mask]).astype(np.float32)
        vh[r_min:r_max, c_min:c_max][sub_mask] += (float(np.random.uniform(220.0, 440.0)) * weight[sub_mask]).astype(np.float32)

        tgt_info = {
            "target_id": f"GT-TGT-{idx:02d}",
            "pixel_r": r,
            "pixel_c": c,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "length_m": length,
            "beam_m": beam,
            "heading_deg": heading,
            "has_active_ais": has_ais,
            "name": name,
            "mmsi": mmsi,
            "expected_classification": "AIS_CONFIRMED" if has_ais else "DARK_VESSEL"
        }
        ground_truth_targets.append(tgt_info)

        if has_ais:
            # AIS transmitted slightly earlier (e.g. 15 minutes before SAR)
            t_ais = t_sar.replace(minute=max(0, t_sar.minute - 15))
            ais_reports.append({
                "mmsi": mmsi,
                "name": name,
                "flag": "🇨🇳 China",
                "vessel_type": "Squid Jigger",
                "lat": round(lat + 0.005, 5),  # slight kinematic offset before dead reckoning
                "lon": round(lon - 0.003, 5),
                "heading": 170.0,
                "avg_speed": 1.2,
                "last_seen": t_ais.isoformat()
            })

    # Add 1 GHOST AIS vessel: broadcasts valid AIS coordinates inside the scene,
    # but NO physical metallic ship is placed in the SAR radar array!
    ghost_r, ghost_c = 300, 300
    ghost_lat = max_lat - (ghost_r / (h - 1)) * (max_lat - min_lat)
    ghost_lon = min_lon + (ghost_c / (w - 1)) * (max_lon - min_lon)
    ais_reports.append({
        "mmsi": "999000123",
        "name": "PHANTOM TRANSMITTER",
        "flag": "🚩 Flag of Convenience",
        "vessel_type": "Carrier",
        "lat": round(ghost_lat, 5),
        "lon": round(ghost_lon, 5),
        "heading": 90.0,
        "avg_speed": 0.5,
        "last_seen": t_sar.isoformat()
    })

    # Save to disk as compressed numpy fixture
    np.savez_compressed(
        FIXTURE_FILE,
        vv=vv,
        vh=vh,
        bounds=np.array(bounds, dtype=np.float64),
        resolution_m=np.float32(10.0)
    )

    metadata = {
        "scene_id": "S1A_IW_GRDH_1SDV_20260415T140230_MILE201_ARGENTINA",
        "sensor": "Sentinel-1A SAR C-Band",
        "mode": "Interferometric Wide Swath (IW)",
        "product_type": "GRD (Ground Range Detected)",
        "polarizations": ["VV", "VH"],
        "bounds": bounds,
        "acquisition_time_utc": t_sar.isoformat(),
        "total_ground_truth_targets": len(ground_truth_targets),
        "expected_dark_vessels": 12,
        "expected_ais_confirmed": 3,
        "expected_ghost_ais": 1,
        "ground_truth_targets": ground_truth_targets,
        "ais_reports": ais_reports
    }

    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return metadata


def load_mile201_fixture() -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Load the calibrated sub-scene fixture from disk.
    If not already generated, creates it automatically.
    """
    if not os.path.exists(FIXTURE_FILE) or not os.path.exists(METADATA_FILE):
        generate_mile201_fixture()

    data = np.load(FIXTURE_FILE)
    vv = data["vv"]
    vh = data["vh"]

    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    return vv, vh, metadata
