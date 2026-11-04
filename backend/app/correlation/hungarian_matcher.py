"""
Optimal Bipartite SAR-AIS Hungarian Matcher & Ghost AIS Spoofing Engine.

Solves the global correspondence problem between instantaneous SAR detections
and kinematic-projected AIS positions:
- Minimizes aggregate Euclidean/Haversine distance matrix via scipy.optimize.linear_sum_assignment.
- Validation gating: pairs exceeding R_gating (default: 3.0 NM) are rejected.
- Tri-Tactical Classification:
  1. AIS_CONFIRMED: Radar return matches legitimate AIS transponder.
  2. DARK_VESSEL: Strong radar return with ZERO matching AIS signal (IUU illicit suspect).
  3. GHOST_AIS: AIS transponder claims to be inside the SAR footprint, but the satellite
     radar detects NO physical hull at that location (Identity spoofing / geospatial deception).
"""

import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

from app.processing.morphology import SarVesselDetection
from app.correlation.kinematic_predictor import (
    ProjectedAisVessel,
    haversine_distance_nm
)


class MatchedPair(BaseModel):
    sar_target: SarVesselDetection
    ais_vessel: ProjectedAisVessel
    distance_nm: float
    confidence_score: float


class CorrelationResult(BaseModel):
    confirmed_matches: List[MatchedPair] = Field(default_factory=list)
    dark_vessels: List[SarVesselDetection] = Field(default_factory=list)
    ghost_ais_vessels: List[ProjectedAisVessel] = Field(default_factory=list)
    total_sar_targets: int
    total_confirmed: int
    total_dark_vessels: int
    total_ghost_ais: int
    dark_vessel_percentage: float
    summary_report: str


def point_in_bbox(lat: float, lon: float, bbox: Tuple[float, float, float, float], margin_nm: float = 1.0) -> bool:
    """
    Check if a coordinate lies inside a bounding box (min_lon, min_lat, max_lon, max_lat)
    allowing for an optional boundary margin in NM.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    deg_margin = margin_nm / 60.0
    return (
        (min_lat - deg_margin) <= lat <= (max_lat + deg_margin) and
        (min_lon - deg_margin) <= lon <= (max_lon + deg_margin)
    )


def match_sar_with_ais(
    sar_targets: List[SarVesselDetection],
    ais_vessels: List[ProjectedAisVessel],
    sar_bbox: Optional[Tuple[float, float, float, float]] = None,
    gating_distance_nm: float = 3.0
) -> CorrelationResult:
    """
    Execute Hungarian bipartite matching between SAR detections and projected AIS.

    Args:
        sar_targets: List of detected SAR targets.
        ais_vessels: List of kinematic-projected AIS vessels.
        sar_bbox: Optional (min_lon, min_lat, max_lon, max_lat) of the SAR scene.
                  Used to confirm whether unmatched AIS vessels were truly inside
                  the observed satellite footprint to declare GHOST_AIS.
        gating_distance_nm: Maximum allowable distance in NM to accept a match.

    Returns:
        CorrelationResult with tri-tactical classification.
    """
    n_sar = len(sar_targets)
    n_ais = len(ais_vessels)

    confirmed_matches: List[MatchedPair] = []
    matched_sar_indices = set()
    matched_ais_indices = set()

    if n_sar > 0 and n_ais > 0:
        # Build cost matrix D of size (n_sar, n_ais) with Haversine distances
        cost_matrix = np.zeros((n_sar, n_ais), dtype=np.float64)
        for i, s_tgt in enumerate(sar_targets):
            for j, a_vsl in enumerate(ais_vessels):
                d = haversine_distance_nm(
                    s_tgt.lat, s_tgt.lon,
                    a_vsl.projected_lat, a_vsl.projected_lon
                )
                cost_matrix[i, j] = d

        # Solve optimal assignment with Hungarian algorithm
        row_ind, col_ind = linear_sum_assignment(cost_matrix)

        for r, c in zip(row_ind, col_ind):
            dist = float(cost_matrix[r, c])
            # Apply dynamic gating: combine base gating with AIS uncertainty
            effective_gate = max(gating_distance_nm, ais_vessels[c].uncertainty_nm)
            if dist <= effective_gate:
                matched_sar_indices.add(r)
                matched_ais_indices.add(c)

                # Confidence score inversely proportional to distance and time delta
                conf = max(0.0, min(1.0, 1.0 - (dist / effective_gate)))

                tgt = sar_targets[r].model_copy()
                tgt.classification = "AIS_CONFIRMED"

                vsl = ais_vessels[c].model_copy()
                vsl.status = "MATCHED_CONFIRMED"

                confirmed_matches.append(MatchedPair(
                    sar_target=tgt,
                    ais_vessel=vsl,
                    distance_nm=round(dist, 2),
                    confidence_score=round(conf, 3)
                ))

    # Identify Dark Vessels (SAR detections with no matching AIS)
    dark_vessels: List[SarVesselDetection] = []
    for i, s_tgt in enumerate(sar_targets):
        if i not in matched_sar_indices:
            dark_det = s_tgt.model_copy()
            dark_det.classification = "DARK_VESSEL"
            dark_vessels.append(dark_det)

    # Identify Ghost AIS Vessels (AIS transmitting inside the scene, but no radar hull)
    ghost_ais_vessels: List[ProjectedAisVessel] = []
    for j, a_vsl in enumerate(ais_vessels):
        if j not in matched_ais_indices:
            # Check if this vessel's projected coordinate was inside the satellite scene footprint
            is_inside = True
            if sar_bbox is not None:
                is_inside = point_in_bbox(a_vsl.projected_lat, a_vsl.projected_lon, sar_bbox)

            if is_inside:
                ghost_vsl = a_vsl.model_copy()
                ghost_vsl.status = "GHOST_AIS_SPOOF"
                ghost_ais_vessels.append(ghost_vsl)

    total_sar = len(sar_targets)
    total_dark = len(dark_vessels)
    total_conf = len(confirmed_matches)
    total_ghost = len(ghost_ais_vessels)

    dark_pct = round((total_dark / max(total_sar, 1)) * 100.0, 1)

    summary = (
        f"Correlator Analysis Complete: {total_sar} SAR targets processed. "
        f"{total_conf} confirmed with AIS, {total_dark} DARK VESSELS detected "
        f"({dark_pct}% dark ratio), and {total_ghost} GHOST AIS spoofing anomalies identified."
    )

    return CorrelationResult(
        confirmed_matches=confirmed_matches,
        dark_vessels=dark_vessels,
        ghost_ais_vessels=ghost_ais_vessels,
        total_sar_targets=total_sar,
        total_confirmed=total_conf,
        total_dark_vessels=total_dark,
        total_ghost_ais=total_ghost,
        dark_vessel_percentage=dark_pct,
        summary_report=summary
    )
