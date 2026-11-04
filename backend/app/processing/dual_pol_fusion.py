"""
Dual-Polarization (VV + VH) Radar Physics Fusion.

Sentinel-1 operates in C-band dual-polarization (VV and VH channels).
Physical rationale for maritime vessel teledetection:
- The ocean surface generates intense Bragg scattering predominantly in the co-polarized
  VV channel (high sea clutter / wave reflections).
- Ship hulls, orthogonal bulkheads, and metallic superstructures produce multiple internal
  dihedral and trihedral bounce reflections that strongly depolarize the radar beam into
  the cross-polarized VH channel.
- Single-pol VV is plagued by wave clutter false alarms in rough seas (e.g. Malvinas Current).
- Fusing calibrated linear Sigma0 from VH and normalized VV enhances the target Signal-to-Clutter
  Ratio (SCR) by 6 to 10 dB compared to VV alone, isolating steel hulls even in heavy seas.

Formulas:
    I_fused(i, j) = alpha * sigma0_VH,lin(i, j) + (1 - alpha) * (sigma0_VV,lin(i, j) / kappa_clutter)
"""

import numpy as np
from typing import Optional, Tuple


def fuse_dual_polarization(
    vv_lin: np.ndarray,
    vh_lin: np.ndarray,
    alpha: float = 0.75,
    kappa_clutter: Optional[float] = None,
    epsilon: float = 1e-10
) -> np.ndarray:
    """
    Fuse co-polarized (VV) and cross-polarized (VH) SAR intensity channels.

    Args:
        vv_lin: 2D numpy array of Sigma0 linear intensity for VV polarization.
        vh_lin: 2D numpy array of Sigma0 linear intensity for VH polarization.
        alpha: Weight for cross-polarized VH channel (default: 0.75).
               Favors the depolarized target signature while suppressing wave clutter.
        kappa_clutter: Clutter ratio normalization factor. If None, it is estimated
                       automatically from the median ratio of VV to VH sea clutter.
        epsilon: Numerical stability floor.

    Returns:
        2D numpy array (float32) of fused linear SAR intensity.
    """
    if vv_lin.shape != vh_lin.shape:
        raise ValueError(f"Shape mismatch: VV {vv_lin.shape} vs VH {vh_lin.shape}")

    vv = np.maximum(vv_lin.astype(np.float32), epsilon)
    vh = np.maximum(vh_lin.astype(np.float32), epsilon)

    if kappa_clutter is None:
        # Robust estimation of background clutter power ratio using median
        median_vv = float(np.median(vv))
        median_vh = float(np.median(vh))
        kappa_clutter = max(median_vv / max(median_vh, epsilon), 1.0)

    kappa = np.float32(max(kappa_clutter, 1.0))
    alpha_f = np.float32(np.clip(alpha, 0.0, 1.0))

    fused = alpha_f * vh + (1.0 - alpha_f) * (vv / kappa)
    return np.maximum(fused, epsilon).astype(np.float32)


def calculate_scr_db(
    image: np.ndarray,
    target_mask: np.ndarray,
    guard_dilation: int = 5
) -> float:
    """
    Compute Signal-to-Clutter Ratio (SCR) in dB.
    SCR = 10 * log10( Mean(Target) / Mean(Sea Clutter) )
    """
    import scipy.ndimage as ndi

    target_pixels = image[target_mask]
    if len(target_pixels) == 0:
        return 0.0

    # Expand target mask by guard margin to exclude ship sidelobes from clutter estimation
    structure = np.ones((guard_dilation * 2 + 1, guard_dilation * 2 + 1), dtype=bool)
    dilated_target = ndi.binary_dilation(target_mask, structure=structure)
    clutter_mask = ~dilated_target

    clutter_pixels = image[clutter_mask]
    if len(clutter_pixels) == 0:
        return 0.0

    target_power = float(np.mean(target_pixels))
    clutter_power = float(np.mean(clutter_pixels))

    if clutter_power <= 0:
        return 0.0

    return 10.0 * np.log10(max(target_power / clutter_power, 1e-6))


def evaluate_polarimetric_gain(
    vv_lin: np.ndarray,
    vh_lin: np.ndarray,
    fused_lin: np.ndarray,
    target_mask: np.ndarray
) -> dict:
    """
    Evaluate quantitative SCR metrics across polarizations.

    Returns dict with SCR values in dB for VV, VH, and Fused, along with
    the net gain (dB) of the fused product over the VV channel.
    """
    scr_vv = calculate_scr_db(vv_lin, target_mask)
    scr_vh = calculate_scr_db(vh_lin, target_mask)
    scr_fused = calculate_scr_db(fused_lin, target_mask)

    return {
        "scr_vv_db": round(scr_vv, 2),
        "scr_vh_db": round(scr_vh, 2),
        "scr_fused_db": round(scr_fused, 2),
        "gain_over_vv_db": round(scr_fused - scr_vv, 2)
    }
