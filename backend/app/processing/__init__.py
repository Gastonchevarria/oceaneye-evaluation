"""
OceanEye Radar Processing Package.
"""

from .calibration import (
    calibrate_dn_to_sigma0_linear,
    sigma0_linear_to_db,
    calibrate_dn_to_sigma0_db
)
from .dual_pol_fusion import (
    fuse_dual_polarization,
    calculate_scr_db,
    evaluate_polarimetric_gain
)
from .lee_filter import enhanced_lee_filter
from .cfar_detector import ca_cfar_2d, calculate_cfar_alpha
from .morphology import (
    SarVesselDetection,
    extract_sar_targets,
    pixel_to_wgs84,
    estimate_inertial_dimensions
)

__all__ = [
    "calibrate_dn_to_sigma0_linear",
    "sigma0_linear_to_db",
    "calibrate_dn_to_sigma0_db",
    "fuse_dual_polarization",
    "calculate_scr_db",
    "evaluate_polarimetric_gain",
    "enhanced_lee_filter",
    "ca_cfar_2d",
    "calculate_cfar_alpha",
    "SarVesselDetection",
    "extract_sar_targets",
    "pixel_to_wgs84",
    "estimate_inertial_dimensions"
]
