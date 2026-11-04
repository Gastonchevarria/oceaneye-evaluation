"""
OceanEye Legal & Chain of Custody Package.
"""

from .dossier_generator import (
    ForensicDossier,
    generate_forensic_dossier,
    calculate_distance_to_eez_boundary,
    OFFICIAL_EEZ_BOUNDARY_LINE
)

__all__ = [
    "ForensicDossier",
    "generate_forensic_dossier",
    "calculate_distance_to_eez_boundary",
    "OFFICIAL_EEZ_BOUNDARY_LINE"
]
