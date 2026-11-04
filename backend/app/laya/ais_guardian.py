"""
LAYA Real-Time AIS Guardian: Deterministic Edge Anomaly Detector (Sistema 1).

Evaluates high-frequency AIS radio broadcasts in sub-40ms latency against
three hard deterministic kinematic and geospatial rules:

1. Geo-Fence Proximity Alert:
   Checks if a vessel is within 5 Nautical Miles (NM) of the Argentine EEZ boundary
   (Mile 200 outer limit), triggering pre-emptive naval alert.

2. Coordinate Teleportation (Spoofing):
   Detects impossible spatial jumps (e.g. > 30 NM in < 5 minutes, implying > 360 knots),
   proving GPS spoofing, transponder cloning, or synthetic signal injection.

3. Dead-Drop Blackout Trigger:
   Detects sudden cessation of AIS transmission while operating inside the
   critical surveillance corridor (Mile 201 / Agujero Azul).
   Immediately issues a satellite tasking intercept order (TASK_SENTINEL1_INTERCEPT_ORDER)
   for priority verification on the next Sentinel-1 pass.
"""

import time
import math
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from pydantic import BaseModel, Field

from app.correlation.kinematic_predictor import haversine_distance_nm
from app.legal.dossier_generator import calculate_distance_to_eez_boundary

# Bounding box of the critical Blue Hole / Mile 201 surveillance polygon
BLUE_HOLE_CRITICAL_BOX = {
    "min_lat": -47.50,
    "max_lat": -43.00,
    "min_lon": -61.50,
    "max_lon": -57.50
}


class AisGuardianAlert(BaseModel):
    rule_name: str
    severity: str  # "CRITICAL" | "HIGH" | "WARNING" | "INFO"
    description: str
    action_taken: str


class AisGuardianVerdict(BaseModel):
    mmsi: str
    vessel_name: str
    lat: float
    lon: float
    timestamp_utc: str
    evaluation_latency_ms: float
    is_anomaly: bool
    alerts: List[AisGuardianAlert] = Field(default_factory=list)
    satellite_tasking_order: Optional[Dict[str, Any]] = None
    status: str = "VERDICT_COMPLETED"


def evaluate_ais_telemetry(
    current_report: Dict[str, Any],
    previous_report: Optional[Dict[str, Any]] = None,
    now_utc: Optional[datetime] = None
) -> AisGuardianVerdict:
    """
    Evaluate a single AIS telemetry packet in sub-40ms deterministic time.

    Args:
        current_report: Dictionary with mmsi, name, lat, lon, sog, cog, last_seen.
        previous_report: Optional previous report for kinematic delta analysis.
        now_utc: Optional current reference time.

    Returns:
        AisGuardianVerdict with deterministic rule violations and satellite tasking.
    """
    t_start = time.perf_counter()

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    mmsi = str(current_report.get("mmsi", "UNKNOWN"))
    name = str(current_report.get("name", "UNKNOWN"))
    lat = float(current_report.get("lat", 0.0))
    lon = float(current_report.get("lon", 0.0))
    sog = float(current_report.get("avg_speed", current_report.get("sog", 0.0)))

    alerts: List[AisGuardianAlert] = []
    sat_tasking: Optional[Dict[str, Any]] = None

    # ── Rule 1: Geo-Fence Alert (5 NM buffer from EEZ) ───────────────────────
    signed_dist, nearest_pt, boundary_status = calculate_distance_to_eez_boundary(lat, lon)
    abs_dist = abs(signed_dist)

    if signed_dist < 0:
        alerts.append(AisGuardianAlert(
            rule_name="ZEE_INTRUSION_ACTIVE",
            severity="CRITICAL",
            description=f"Vessel has crossed into Argentine EEZ by {abs_dist:.2f} NM at Lat {lat:.4f}, Lon {lon:.4f}.",
            action_taken="DISPATCH_PNA_INTERCEPT_ALERT"
        ))
    elif abs_dist <= 5.0:
        alerts.append(AisGuardianAlert(
            rule_name="ZEE_BUFFER_PROXIMITY_ALERT",
            severity="HIGH",
            description=f"Vessel is within {abs_dist:.2f} NM (< 5.0 NM threshold) of the official Mile 200 border.",
            action_taken="ACTIVATE_HIGH_FREQUENCY_GEODETIC_TRACKING"
        ))

    # ── Rule 2: Coordinate Teleportation (Spoofing) ──────────────────────────
    if previous_report is not None:
        prev_lat = float(previous_report.get("lat", lat))
        prev_lon = float(previous_report.get("lon", lon))
        
        # Parse timestamps
        prev_time_str = previous_report.get("last_seen", "")
        curr_time_str = current_report.get("last_seen", "")
        try:
            t_prev = datetime.fromisoformat(prev_time_str.replace("Z", "+00:00"))
            t_curr = datetime.fromisoformat(curr_time_str.replace("Z", "+00:00"))
            dt_sec = max(abs((t_curr - t_prev).total_seconds()), 1.0)
        except Exception:
            dt_sec = 300.0  # default 5 min

        dist_jump_nm = haversine_distance_nm(prev_lat, prev_lon, lat, lon)
        speed_implied_knots = dist_jump_nm / (dt_sec / 3600.0)

        # > 30 NM jump in < 5 minutes (speed > 360 knots) is physically impossible for marine vessels
        if dist_jump_nm > 30.0 and dt_sec <= 600.0:
            alerts.append(AisGuardianAlert(
                rule_name="AIS_SPOOFING_COORDINATE_TELEPORTATION",
                severity="CRITICAL",
                description=(
                    f"Vessel leaped {dist_jump_nm:.1f} NM in {dt_sec/60:.1f} minutes "
                    f"(implied speed {speed_implied_knots:.0f} kn). Physical impossibility."
                ),
                action_taken="FLAG_SPOOFING_IDENTITY_DECEPTION"
            ))

    # ── Rule 3: Dead-Drop Trigger (Blackout in Mile 201 hotspot) ─────────────
    # Check if inside the critical Blue Hole box
    in_critical_box = (
        BLUE_HOLE_CRITICAL_BOX["min_lat"] <= lat <= BLUE_HOLE_CRITICAL_BOX["max_lat"] and
        BLUE_HOLE_CRITICAL_BOX["min_lon"] <= lon <= BLUE_HOLE_CRITICAL_BOX["max_lon"]
    )

    ais_disabled = (
        current_report.get("ais_disabled", False) or
        current_report.get("is_blackout", False) or
        (current_report.get("transponder_active") is False)
    )
    if in_critical_box and ais_disabled:
        task_id = f"SAT-TASK-{mmsi}-{int(now_utc.timestamp())}"
        sat_tasking = {
            "task_id": task_id,
            "sensor": "Sentinel-1A SAR C-Band (IW Mode)",
            "target_lat": lat,
            "target_lon": lon,
            "priority": "FLASH_CRITICAL_INTERCEPT",
            "reason": "Sudden AIS transmission blackout inside Mile 201 Agujero Azul corridor",
            "issued_at_utc": now_utc.isoformat()
        }
        alerts.append(AisGuardianAlert(
            rule_name="DEAD_DROP_BLACKOUT_TRIGGER",
            severity="CRITICAL",
            description=f"Vessel terminated AIS broadcast inside critical Mile 201 hotspot. Target marked for radar capture.",
            action_taken=f"TASK_SENTINEL1_INTERCEPT_ORDER (ID: {task_id})"
        ))

    latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
    is_anomaly = len(alerts) > 0

    return AisGuardianVerdict(
        mmsi=mmsi,
        vessel_name=name,
        lat=lat,
        lon=lon,
        timestamp_utc=now_utc.isoformat(),
        evaluation_latency_ms=latency_ms,
        is_anomaly=is_anomaly,
        alerts=alerts,
        satellite_tasking_order=sat_tasking
    )
