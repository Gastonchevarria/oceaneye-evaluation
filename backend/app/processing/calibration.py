"""
Radiometric Calibration for Sentinel-1 Synthetic Aperture Radar (SAR).

Converts raw Digital Numbers (DN) from Sentinel-1 Level-1 GRD products to
radiometrically calibrated Sigma-Nought (sigma0), both in linear power scale
and logarithmic decibel (dB) scale.

Formulas:
    sigma0_linear = (DN^2) / (A_lut^2)
    sigma0_dB = 10 * log10(sigma0_linear + epsilon)

References:
    - ESA Sentinel-1 Radiometric Calibration Technical Note (S1-TN-ESA-0010)
    - European Space Agency (ESA) Copernicus Sentinel-1 Team
"""

import numpy as np
from typing import Optional, Union


def calibrate_dn_to_sigma0_linear(
    dn: np.ndarray,
    lut: Optional[Union[np.ndarray, float]] = None,
    epsilon: float = 1e-10
) -> np.ndarray:
    """
    Calibrate Digital Numbers (DN) to Sigma0 in linear power scale.

    Args:
        dn: 2D numpy array of unsigned 16-bit or floating-point Digital Numbers.
        lut: Optional calibration Look-Up Table (calibration vector / scaling factor).
             If None, standard Sentinel-1 calibration factor is assumed normalized (1.0).
        epsilon: Small positive value to prevent division by zero or zeros.

    Returns:
        2D numpy array (float32) of Sigma0 in linear units (m^2 / m^2).
    """
    dn_f = dn.astype(np.float32)
    if lut is not None:
        if isinstance(lut, (int, float)):
            lut_f = np.float32(lut)
        else:
            lut_f = lut.astype(np.float32)
        denom = np.maximum(lut_f ** 2, epsilon)
        sigma0_lin = (dn_f ** 2) / denom
    else:
        # Default normalized radiometric calibration for Sentinel-1 GRD High Res
        sigma0_lin = dn_f ** 2 / np.float32(1.0)

    return np.maximum(sigma0_lin, epsilon)


def sigma0_linear_to_db(
    sigma0_lin: np.ndarray,
    epsilon: float = 1e-10
) -> np.ndarray:
    """
    Convert linear Sigma0 values to logarithmic Decibel (dB) scale.

    Args:
        sigma0_lin: 2D numpy array of Sigma0 in linear power scale.
        epsilon: Numerical stability floor.

    Returns:
        2D numpy array (float32) of Sigma0 in dB.
    """
    safe_lin = np.maximum(sigma0_lin, epsilon)
    return 10.0 * np.log10(safe_lin).astype(np.float32)


def calibrate_dn_to_sigma0_db(
    dn: np.ndarray,
    lut: Optional[Union[np.ndarray, float]] = None,
    epsilon: float = 1e-10
) -> np.ndarray:
    """
    Convenience end-to-end function: converts DN directly to Sigma0 in dB.
    """
    lin = calibrate_dn_to_sigma0_linear(dn, lut=lut, epsilon=epsilon)
    return sigma0_linear_to_db(lin, epsilon=epsilon)
