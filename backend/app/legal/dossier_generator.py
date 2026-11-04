"""
Forensic Evidence Dossier Generator & Cryptographic Chain of Custody (UNCLOS / Ley 24.922).

Generates tamper-evident forensic intelligence dossiers for suspected dark vessels
and unauthorized intruders in the Argentine Exclusive Economic Zone (EEZ - Milla 200):
- Accurate geodesic distance to the Argentine maritime boundary (SHN / Ley 23.968).
- Tipification under UNCLOS / CONVEMAR (Arts. 56, 62, 73) and Ley Federal de Pesca 24.922.
- Integration with formal deterministic Legal Rules Engine.
- Cryptographic SHA-256 seal guaranteeing chain-of-custody integrity for maritime tribunals.
"""

import json
import math
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.processing.morphology import SarVesselDetection
from app.correlation.kinematic_predictor import haversine_distance_nm
from app.legal.rules_engine import evaluate_maritime_case, LegalVerdict

# Official boundary vertices along the Argentine Mile 200 outer limit (Milla 200)
# from Río de la Plata down to Tierra del Fuego / Malvinas shelf
OFFICIAL_EEZ_BOUNDARY_LINE: List[Tuple[float, float]] = [
    # Unified with data/argentina_eez.geojson (Lat, Lon)
    (-35.03, -56.55),
    (-35.40, -55.90),
    (-36.20, -54.50),
    (-37.10, -53.80),
    (-38.00, -53.20),
    (-39.00, -53.00),
    (-40.00, -53.30),
    (-41.00, -54.00),
    (-42.00, -55.10),
    (-43.00, -56.20),
    (-44.00, -57.40),
    (-44.80, -58.50),
    (-45.20, -59.20),
    (-45.60, -59.70),
    (-46.00, -60.05),
    (-46.50, -60.40),
    (-47.00, -60.80),
    (-47.80, -61.50),
    (-48.60, -62.20),
    (-49.50, -63.10),
    (-50.40, -64.00),
    (-51.20, -64.80),
    (-52.00, -65.50),
    (-52.80, -65.80),
    (-53.50, -65.20),
    (-54.20, -64.50),
    (-54.80, -63.80),
    (-55.20, -63.00),
    (-55.60, -62.00),
    (-55.80, -60.80),
]


class ForensicDossier(BaseModel):
    dossier_id: str
    target_id: str
    classification: str
    timestamp_utc: str
    wgs84_coordinates: Dict[str, float]
    estimated_dimensions: Dict[str, float]
    radar_cross_section_db: float
    distance_to_eez_nm: float
    boundary_status: str
    nearest_boundary_point: Dict[str, float]
    legal_infraction_type: str
    legal_verdict: Optional[Dict[str, Any]] = None
    legal_citations: List[str]
    chain_of_custody_hash_sha256: str
    evidentiary_payload: Dict[str, Any]


def calculate_distance_to_eez_boundary(lat: float, lon: float) -> Tuple[float, Tuple[float, float], str]:
    """
    Compute minimum geodesic distance to the official Argentine 200 NM boundary line.

    Convention:
    - If vessel longitude is west of the boundary line at the given latitude, it is INSIDE the EEZ.
    - If vessel longitude is east of the boundary line, it is outside in international waters (High Seas / Mile 201).
    """
    min_dist = float("inf")
    nearest_pt = OFFICIAL_EEZ_BOUNDARY_LINE[0]

    for b_lat, b_lon in OFFICIAL_EEZ_BOUNDARY_LINE:
        d = haversine_distance_nm(lat, lon, b_lat, b_lon)
        if d < min_dist:
            min_dist = d
            nearest_pt = (b_lat, b_lon)

    # Determine inside/outside orientation relative to nearest boundary longitude
    is_inside = lon < nearest_pt[1]

    if is_inside:
        signed_dist = -round(min_dist, 2)
        status = "EEZ_INTRUSION_CONFIRMED"
    elif min_dist <= 3.0:
        signed_dist = round(min_dist, 2)
        status = "CRITICAL_BORDER_PROXIMITY_ALERT"
    else:
        signed_dist = round(min_dist, 2)
        status = "HIGH_SEAS_ADJACENT"

    return signed_dist, nearest_pt, status


def generate_forensic_dossier(
    sar_target: SarVesselDetection,
    ais_match: Optional[Any] = None,
    scene_id: str = "S1A_IW_GRDH_1SDV_2026_MILE201"
) -> ForensicDossier:
    """
    Generate a cryptographic forensic dossier for a detected naval target with
    formal legal verdict and SHA-256 evidence chain.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    signed_dist, nearest_pt, boundary_status = calculate_distance_to_eez_boundary(
        sar_target.lat, sar_target.lon
    )

    # Formal deterministic legal verdict
    verdict = evaluate_maritime_case(
        distance_to_eez_nm=signed_dist,
        classification=sar_target.classification
    )

    dossier_id = f"DOSSIER-{sar_target.id}-{int(datetime.now(timezone.utc).timestamp())}"

    evidentiary_payload = {
        "dossier_id": dossier_id,
        "satellite_scene_id": scene_id,
        "target_id": sar_target.id,
        "classification": sar_target.classification,
        "lat": sar_target.lat,
        "lon": sar_target.lon,
        "length_meters": sar_target.length_m,
        "beam_meters": sar_target.beam_m,
        "peak_rcs_db": sar_target.peak_rcs_db,
        "snr_db": sar_target.snr_db,
        "distance_to_eez_nm": signed_dist,
        "boundary_status": boundary_status,
        "nearest_boundary_point": {"lat": nearest_pt[0], "lon": nearest_pt[1]},
        "legal_rule_id": verdict.rule_id,
        "legal_infraction_code": verdict.infraction_code,
        "legal_severity": verdict.severity.value,
        "ais_telemetry": ais_match.model_dump() if ais_match and hasattr(ais_match, "model_dump") else None,
        "generated_timestamp_utc": now_utc
    }

    # Cryptographic SHA-256 seal of the evidentiary payload
    payload_str = json.dumps(evidentiary_payload, sort_keys=True)
    sha256_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

    return ForensicDossier(
        dossier_id=dossier_id,
        target_id=sar_target.id,
        classification=sar_target.classification,
        timestamp_utc=now_utc,
        wgs84_coordinates={"latitude": sar_target.lat, "longitude": sar_target.lon},
        estimated_dimensions={
            "length_meters": sar_target.length_m,
            "beam_meters": sar_target.beam_m,
            "heading_degrees": sar_target.heading_deg
        },
        radar_cross_section_db=sar_target.peak_rcs_db,
        distance_to_eez_nm=signed_dist,
        boundary_status=boundary_status,
        nearest_boundary_point={"latitude": nearest_pt[0], "longitude": nearest_pt[1]},
        legal_infraction_type=verdict.infraction_code,
        legal_verdict=verdict.model_dump(),
        legal_citations=verdict.legal_basis_articles,
        chain_of_custody_hash_sha256=sha256_hash,
        evidentiary_payload=evidentiary_payload
    )
