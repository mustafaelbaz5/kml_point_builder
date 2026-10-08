from __future__ import annotations

from collections.abc import Iterable

from shapely import Geometry, make_valid, voronoi_polygons
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon
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


def interior_gap_polygons(boundaries: Iterable[Geometry], coverage: Geometry):
    """Find only enclosed holes bounded by parcel edges.

    Open space at the outside of the mapped area is deliberately ignored: it might
    be a road, canal, or simply land outside the project boundary.
    """
    from shapely.ops import polygonize, unary_union

    all_lines = unary_union(list(boundaries))
    candidates = polygonize(all_lines)
    gaps = []
    for candidate in candidates:
        if candidate.area == 0 or coverage.covers(candidate.representative_point()):
            continue
        gaps.append(candidate)
    return gaps


def sample_boundary(geometry: Geometry, spacing_m: float) -> list[Point]:
    """Sample a polygon boundary densely enough to approximate its medial line."""
    boundary = geometry if isinstance(geometry, (LineString, MultiLineString)) else geometry.boundary
    parts = boundary.geoms if hasattr(boundary, "geoms") else [boundary]
    points: list[Point] = []
    for line in parts:
        if line.length == 0:
            continue
        sample_count = max(2, int(line.length / spacing_m) + 1)
        points.extend(line.interpolate(position / (sample_count - 1), normalized=True) for position in range(sample_count))
    return points


def allocate_region_on_midlines(region: Polygon, neighbours: dict[object, Geometry], tolerance_m: float):
    """Split one disputed region fairly between every adjacent parcel.

    A Voronoi diagram of boundary samples provides shared lines that are
    equidistant from nearby parcel boundaries. It is computed once for the
    complete connected conflict region, avoiding order-dependent allocation.
    """
    if len(neighbours) < 2:
        return None
    sample_spacing = min(0.5, max(0.1, tolerance_m / 10))
    region_context = region.buffer(tolerance_m)
    samples: list[Point] = []
    for geometry in neighbours.values():
        local_boundary = geometry.boundary.intersection(region_context)
        samples.extend(sample_boundary(local_boundary if not local_boundary.is_empty else geometry, sample_spacing))
    if len(samples) < 4:
        return None

    diagram = voronoi_polygons(MultiPoint(samples), extend_to=region_context.envelope)
    allocations: dict[object, list[Geometry]] = {key: [] for key in neighbours}
    for cell in diagram.geoms:
        clipped = cell.intersection(region)
        if clipped.is_empty or clipped.area == 0:
            continue
        probe = clipped.representative_point()
        distances = {key: probe.distance(geometry.boundary) for key, geometry in neighbours.items()}
        winner = min(distances, key=distances.get)
        allocations[winner].append(clipped)

    from shapely.ops import unary_union

    shares = {key: unary_union(parts) for key, parts in allocations.items() if parts}
    covered = unary_union(list(shares.values())) if shares else GeometryCollection()
    if region.difference(covered).area > max(0.01, region.area * 0.001):
        return None
    return shares


def split_gap_on_midline(gap: Polygon, left: Geometry, right: Geometry, tolerance_m: float):
    """Allocate a confirmed two-parcel gap to its nearest parcel boundary.

    Voronoi cells of closely sampled parcel boundaries form an approximation of
    the equal-distance (midline) boundary through the gap. This avoids snapping
    one parcel all the way to the other parcel's original edge.
    """
    spacing = min(0.5, max(0.1, tolerance_m / 20))
    samples = sample_boundary(left, spacing) + sample_boundary(right, spacing)
    if len(samples) < 4:
        return None
    diagram = voronoi_polygons(MultiPoint(samples), extend_to=gap.buffer(tolerance_m))
    left_parts = []
    right_parts = []
    for cell in diagram.geoms:
        clipped = cell.intersection(gap)
        if clipped.is_empty or clipped.area == 0:
            continue
        probe = clipped.representative_point()
        if probe.distance(left.boundary) <= probe.distance(right.boundary):
            left_parts.append(clipped)
        else:
            right_parts.append(clipped)
    from shapely.ops import unary_union

    left_share = unary_union(left_parts) if left_parts else None
    right_share = unary_union(right_parts) if right_parts else None
    if left_share is None or right_share is None:
        return None
    # The two shares must cover the full gap; otherwise retain it for review.
    if gap.difference(left_share.union(right_share)).area > max(0.01, gap.area * 0.001):
        return None
    return left_share, right_share
