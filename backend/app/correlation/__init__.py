"""
OceanEye Kinematic Correlation & Matcher Package.
"""

from .kinematic_predictor import (
    ProjectedAisVessel,
    project_ais_position,
    project_vessels_to_sar_time,
    haversine_distance_nm
)
from .hungarian_matcher import (
    MatchedPair,
    CorrelationResult,
    match_sar_with_ais,
    point_in_bbox
)

__all__ = [
    "ProjectedAisVessel",
    "project_ais_position",
    "project_vessels_to_sar_time",
    "haversine_distance_nm",
    "MatchedPair",
    "CorrelationResult",
    "match_sar_with_ais",
    "point_in_bbox"
]
