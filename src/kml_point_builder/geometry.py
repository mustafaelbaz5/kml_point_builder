from __future__ import annotations

from shapely import Geometry, make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import polylabel


def polygonal_part(geometry: Geometry) -> Polygon | MultiPolygon | None:
    """Return only polygonal components, retaining all valid parcel area."""
    if geometry is None or geometry.is_empty:
        return None
    fixed = make_valid(geometry)
    if isinstance(fixed, (Polygon, MultiPolygon)):
        return fixed
    if isinstance(fixed, GeometryCollection):
        polygons = [item for item in fixed.geoms if isinstance(item, (Polygon, MultiPolygon))]
        if not polygons:
            return None
        flattened: list[Polygon] = []
        for item in polygons:
            flattened.extend(item.geoms if isinstance(item, MultiPolygon) else [item])
        return MultiPolygon(flattened) if len(flattened) > 1 else flattened[0]
    return None


def safe_interior_point(geometry: Polygon | MultiPolygon, tolerance_m: float = 0.05):
    """Prefer the pole of inaccessibility; it remains inside concave parcels."""
    if isinstance(geometry, MultiPolygon):
        geometry = max(geometry.geoms, key=lambda part: part.area)
    point = polylabel(geometry, tolerance=tolerance_m)
    return point if geometry.contains(point) else geometry.representative_point()


def centered_interior_point(geometry: Polygon | MultiPolygon, safety_margin_m: float = 0.1):
    """Use the visual centre when it is safe; otherwise return a guaranteed interior point."""
    centroid = geometry.centroid
    if geometry.contains(centroid) and centroid.distance(geometry.boundary) >= safety_margin_m:
        return centroid, "CENTROID_SAFE"
    return safe_interior_point(geometry), "POLYLABEL_FALLBACK"
