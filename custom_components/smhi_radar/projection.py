"""WGS84 to SWEREF99 TM (EPSG:3006) projection.

Gauss-Kruger forward formulas as published by Lantmateriet. Accurate to the
millimetre within Sweden, which removes the need for pyproj.
"""

from __future__ import annotations

import math

# GRS80 ellipsoid and SWEREF99 TM parameters.
_A = 6378137.0
_F = 1 / 298.257222101
_CENTRAL_MERIDIAN = math.radians(15.0)
_SCALE = 0.9996
_FALSE_EASTING = 500000.0

_E2 = _F * (2 - _F)
_N = _F / (2 - _F)
_A_ROOF = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)

_CA = _E2
_CB = (5 * _E2**2 - _E2**3) / 6
_CC = (104 * _E2**3 - 45 * _E2**4) / 120
_CD = 1237 * _E2**4 / 1260

_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16 + 41 * _N**4 / 180,
    13 * _N**2 / 48 - 3 * _N**3 / 5 + 557 * _N**4 / 1440,
    61 * _N**3 / 240 - 103 * _N**4 / 140,
    49561 * _N**4 / 161280,
)


def to_sweref99tm(latitude: float, longitude: float) -> tuple[float, float]:
    """Return (easting, northing) in metres for a WGS84 coordinate."""
    phi = math.radians(latitude)
    sin_phi = math.sin(phi)
    phi_star = phi - sin_phi * math.cos(phi) * (
        _CA + _CB * sin_phi**2 + _CC * sin_phi**4 + _CD * sin_phi**6
    )
    delta_lambda = math.radians(longitude) - _CENTRAL_MERIDIAN
    xi = math.atan(math.tan(phi_star) / math.cos(delta_lambda))
    eta = math.atanh(math.cos(phi_star) * math.sin(delta_lambda))

    northing = xi
    easting = eta
    for k, beta in enumerate(_BETA, start=1):
        northing += beta * math.sin(2 * k * xi) * math.cosh(2 * k * eta)
        easting += beta * math.cos(2 * k * xi) * math.sinh(2 * k * eta)

    return _SCALE * _A_ROOF * easting + _FALSE_EASTING, _SCALE * _A_ROOF * northing
