"""Build assets/basemap.json for SMHI Weather Radar.

Projects Natural Earth 1:10m data to SWEREF99 TM (EPSG:3006), clips it to
the SMHI radar composite extent and simplifies it. Run this only when the
basemap needs to change; Home Assistant does not run it.

Requires: pip install numpy pyproj shapely

Usage:
    python script/build_basemap.py NATURAL_EARTH_DIR OUTPUT_JSON

NATURAL_EARTH_DIR must hold these files from
https://github.com/nvkelso/natural-earth-vector/tree/master/geojson
    ne_10m_admin_0_countries.geojson
    ne_10m_admin_1_states_provinces.geojson
    ne_10m_lakes.geojson
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
from pyproj import Transformer
import shapely
from shapely.geometry import box, shape

# SMHI radar composite extent in EPSG:3006 (easting, northing).
MIN_E, MIN_N, MAX_E, MAX_N = 126648, 5983984, 1075693, 7771252
SIMPLIFY_METRES = 500
TRANSFORMER = Transformer.from_crs("EPSG:4326", "EPSG:3006", always_xy=True)
EXTENT = box(MIN_E, MIN_N, MAX_E, MAX_N)
# Generous lon/lat window around the extent; clipping here first keeps
# far away geometry from being projected into the Swedish grid.
WINDOW = box(-5.0, 50.0, 45.0, 75.0)


def _project(coords: np.ndarray) -> np.ndarray:
    easting, northing = TRANSFORMER.transform(coords[:, 0], coords[:, 1])
    return np.column_stack([easting, northing])


def _load(directory: Path, name: str) -> list[dict]:
    return json.loads((directory / name).read_text(encoding="utf-8"))["features"]


def _prepare(feature: dict):
    geometry = shape(feature["geometry"])
    if not geometry.is_valid:
        geometry = geometry.buffer(0)
    geometry = geometry.intersection(WINDOW)
    if geometry.is_empty:
        return None
    clipped = shapely.transform(geometry, _project).intersection(EXTENT)
    if clipped.is_empty:
        return None
    return clipped.simplify(SIMPLIFY_METRES, preserve_topology=True)


def _polygons(geometry) -> list[list[list[list[int]]]]:
    """Return polygons as lists of integer rings."""
    if geometry is None or geometry.is_empty:
        return []
    parts = getattr(geometry, "geoms", [geometry])
    result = []
    for part in parts:
        if part.geom_type != "Polygon" or part.area < SIMPLIFY_METRES**2:
            continue
        rings = [part.exterior, *part.interiors]
        result.append([[[round(x), round(y)] for x, y in ring.coords] for ring in rings])
    return result


def main(source: Path, output: Path) -> None:
    neighbours, sweden, lakes, counties = [], [], [], []

    for feature in _load(source, "ne_10m_admin_0_countries.geojson"):
        polygons = _polygons(_prepare(feature))
        if feature["properties"]["ADM0_A3"] == "SWE":
            sweden.extend(polygons)
        else:
            neighbours.extend(polygons)

    for feature in _load(source, "ne_10m_lakes.geojson"):
        lakes.extend(_polygons(_prepare(feature)))

    for feature in _load(source, "ne_10m_admin_1_states_provinces.geojson"):
        if feature["properties"].get("adm0_a3") == "SWE":
            counties.extend(_polygons(_prepare(feature)))

    basemap = {
        "source": "Natural Earth 1:10m (public domain), projected to EPSG:3006",
        "crs": "EPSG:3006",
        "bounds": [MIN_E, MIN_N, MAX_E, MAX_N],
        "layers": {
            "neighbours": neighbours,
            "sweden": sweden,
            "lakes": lakes,
            "counties": counties,
        },
    }
    output.write_text(json.dumps(basemap, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
