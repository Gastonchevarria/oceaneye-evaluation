"""
Kinematic Dead Reckoning & Temporal Extrapolation for AIS Targets.

In satellite maritime surveillance, SAR images are instantaneous snapshot exposures
taken at an exact UTC second (t_SAR). AIS radio broadcasts arrive asynchronously
with temporal delays ranging from minutes to hours.

At 12 knots, a vessel travels 12 Nautical Miles in one hour.
Applying static Haversine distance without temporal extrapolation leads to massive
false dark vessel alarms or mismatched identities.

This module applies spherical Dead Reckoning:
- Projects vessel coordinates from t_AIS to t_SAR using Speed Over Ground (SOG)
  and Course Over Ground (COG).
- Computes dynamic position uncertainty ellipse (R_uncertainty).
- Flags high-uncertainty vessels if time delta exceeds 3.0 hours.
"""

import math
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from pydantic import BaseModel, Field


class ProjectedAisVessel(BaseModel):
    mmsi: str
    name: str
    flag: str
    vessel_type: str
    orig_lat: float
    orig_lon: float
    projected_lat: float
    projected_lon: float
    sog_knots: float
    cog_degrees: float
    t_ais: datetime
    t_sar: datetime
    delta_seconds: float
    uncertainty_nm: float
    status: str = "PROJECTED"


def haversine_distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Compute great-circle distance between two points in Nautical Miles (NM).
    """
    R_NM = 3440.065  # Earth radius in Nautical Miles
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return float(R_NM * c)


def project_ais_position(
    lat: float,
    lon: float,
    sog_knots: float,
    cog_degrees: float,
    delta_seconds: float
) -> Tuple[float, float, float]:
    """
    Project position using Dead Reckoning along rhumb line / great circle.

    Args:
        lat: Latitude at t_AIS
        lon: Longitude at t_AIS
        sog_knots: Speed over ground in knots (nautical miles per hour)
        cog_degrees: Course over ground in degrees (0 - 360)
        delta_seconds: (t_SAR - t_AIS) in seconds. Positive means extrapolating forward in time.

    Returns:
        (proj_lat, proj_lon, uncertainty_radius_nm)
    """
    if abs(delta_seconds) < 1.0 or sog_knots <= 0.05:
        # Stationary or synchronous
        return lat, lon, 0.2

    # Distance traveled in nautical miles
    hours = delta_seconds / 3600.0
    dist_nm = sog_knots * hours

    cog_rad = math.radians(cog_degrees)

    # 1 Nautical Mile = 1 minute of latitude = 1/60 degrees
    delta_lat = (dist_nm * math.cos(cog_rad)) / 60.0
    proj_lat = lat + delta_lat

    # Longitude adjustment scaled by mean latitude cosine
    mean_lat_rad = math.radians((lat + proj_lat) / 2.0)
    cos_mean_lat = max(math.cos(mean_lat_rad), 0.01)
    delta_lon = (dist_nm * math.sin(cog_rad)) / (60.0 * cos_mean_lat)
    proj_lon = lon + delta_lon

    # Uncertainty grows with time and speed: base GPS error + 10% speed variance
    uncertainty_nm = 0.2 + abs(hours) * (0.15 * max(sog_knots, 1.0))

    return round(proj_lat, 6), round(proj_lon, 6), round(uncertainty_nm, 2)


def project_vessels_to_sar_time(
    ais_vessels: List[Dict[str, Any]],
    t_sar: datetime
) -> List[ProjectedAisVessel]:
    """
    Batch project an arbitrary list of AIS vessel reports to the SAR acquisition time.
    """
    projected: List[ProjectedAisVessel] = []

    for v in ais_vessels:
        # Parse t_ais
        last_seen = v.get("last_seen")
        if isinstance(last_seen, str):
            t_ais = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        elif isinstance(last_seen, datetime):
            t_ais = last_seen
        else:
            t_ais = t_sar

        if t_ais.tzinfo is None:
            t_ais = t_ais.replace(tzinfo=timezone.utc)
        if t_sar.tzinfo is None:
            t_sar = t_sar.replace(tzinfo=timezone.utc)

        delta_sec = (t_sar - t_ais).total_seconds()

        lat = float(v.get("lat", 0.0))
        lon = float(v.get("lon", 0.0))
        sog = float(v.get("avg_speed", v.get("sog", 0.0)))
        cog = float(v.get("heading", v.get("cog", 0.0)))

        p_lat, p_lon, unc_nm = project_ais_position(lat, lon, sog, cog, delta_sec)

        status = "PROJECTED"
        if abs(delta_sec) > 3.0 * 3600:
            status = "STALE_HIGH_UNCERTAINTY"

        proj_vessel = ProjectedAisVessel(
            mmsi=str(v.get("mmsi", "")),
            name=str(v.get("name", "UNKNOWN")),
            flag=str(v.get("flag", "UNKNOWN")),
            vessel_type=str(v.get("vessel_type", "Fishing")),
            orig_lat=lat,
            orig_lon=lon,
            projected_lat=p_lat,
            projected_lon=p_lon,
            sog_knots=sog,
            cog_degrees=cog,
            t_ais=t_ais,
            t_sar=t_sar,
            delta_seconds=delta_sec,
            uncertainty_nm=unc_nm,
            status=status
        )
        projected.append(proj_vessel)

    return projected
