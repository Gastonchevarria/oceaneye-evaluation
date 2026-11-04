"""
Vectorized Enhanced Lee Speckle Filter O(1).

Speckle noise in SAR amplitude images is modeled as a multiplicative noise process.
For Sentinel-1 IW GRD products, the Equivalent Number of Looks (ENL) is approximately L ≈ 4.4,
giving a theoretical speckle noise variance:
    sigma_v^2 = 1.0 / ENL

The classic Lee filter weights local variance vs noise variance:
    W = max(0, (Var(y) - mean(y)^2 * sigma_v^2) / (Var(y) + epsilon))
    x_hat = mean(y) + W * (y - mean(y))

Implementation Strategy:
- Conventional pixel-by-pixel sliding windows take minutes in Python.
- We vectorize local mean and local mean-of-squares using 2D uniform boxcar convolutions
  (`scipy.ndimage.uniform_filter`), executing in O(1) time per pixel (~0.1s for a 2000x2000 sub-scene).
"""

import numpy as np
from scipy.ndimage import uniform_filter


def enhanced_lee_filter(
    image: np.ndarray,
    window_size: int = 7,
    enl: float = 4.4,
    epsilon: float = 1e-10
) -> np.ndarray:
    """
    Apply vectorized adaptive Lee speckle filter to SAR intensity image.

    Args:
        image: 2D numpy array of SAR intensity (linear scale).
        window_size: Odd integer for sliding window diameter (default: 7).
        enl: Equivalent Number of Looks (default: 4.4 for Sentinel-1 IW GRD).
        epsilon: Numerical stability constant.

    Returns:
        2D numpy array (float32) of speckle-filtered SAR intensity.
    """
    img = np.maximum(image.astype(np.float32), epsilon)

    # Theoretical relative variance of speckle noise
    sigma_v_sq = np.float32(1.0 / max(enl, 1.0))

    # Calculate local mean via O(1) uniform boxcar filter
    local_mean = uniform_filter(img, size=window_size, mode='reflect')

    # Calculate local mean of squares: E[y^2]
    local_sq_mean = uniform_filter(img ** 2, size=window_size, mode='reflect')

    # Local variance: Var(y) = E[y^2] - (E[y])^2
    local_variance = np.maximum(local_sq_mean - (local_mean ** 2), 0.0)

    # Adaptive Lee weighting factor W
    # W -> 1 in high-contrast target regions (ship edges preserved intact)
    # W -> 0 in homogeneous sea clutter (pure spatial smoothing)
    numerator = np.maximum(local_variance - (local_mean ** 2) * sigma_v_sq, 0.0)
    denominator = local_variance + epsilon
    weight = np.clip(numerator / denominator, 0.0, 1.0)

    filtered = local_mean + weight * (img - local_mean)
    return np.maximum(filtered, epsilon).astype(np.float32)
