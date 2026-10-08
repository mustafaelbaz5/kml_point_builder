from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
from pyproj import CRS
from shapely import force_2d
from shapely.ops import unary_union

from .geometry import allocate_region_on_midlines, interior_gap_polygons, maximum_width_m, polygonal_part, safe_interior_point
from .models import BuildOptions, BuildResult


GPKG_LAYERS = {
    "original": "parcels_original",
    "clean": "parcels_topology_clean",
    "points": "reference_points",
    "gaps": "gaps_review",
    "overlaps": "overlaps_review",
    "changes": "topology_changes",
}


@dataclass(frozen=True)
class TopologyCleanupResult:
    parcels: gpd.GeoDataFrame
    gaps_review: gpd.GeoDataFrame
    overlaps_review: gpd.GeoDataFrame
    changes: gpd.GeoDataFrame
    gaps_repaired: int
    overlaps_repaired: int
    affected_parcels: int
    review_count: int
    warnings: tuple[str, ...]


def _id_field(frame: gpd.GeoDataFrame, requested: str | None) -> str:
    if requested:
        if requested not in frame.columns:
            raise ValueError(f"ID field '{requested}' was not found in the source.")
        return requested
    for name in ("Name", "name", "title", "Title"):
        if name in frame.columns:
            return name
    raise ValueError("No ID field found. Choose the field that contains the unique parcel ID.")


def _load_parcels(path: Path, requested_id: str | None) -> tuple[gpd.GeoDataFrame, str]:
    source = gpd.read_file(path)
    if source.empty:
        raise ValueError("The KML/KMZ does not contain any readable features.")
    if source.crs is None:
        source = source.set_crs("EPSG:4326")
    source["geometry"] = source.geometry.map(polygonal_part)
    source = source[source.geometry.notna() & ~source.geometry.is_empty].copy()
    if source.empty:
        raise ValueError("No polygon geometry was found in the KML/KMZ.")
    id_field = _id_field(source, requested_id)
    source = source.rename(columns={id_field: "parcel_id"})
    source["parcel_id"] = source["parcel_id"].astype(str).str.strip()
    if source["parcel_id"].eq("").any() or source["parcel_id"].duplicated().any():
        raise ValueError("Parcel IDs must be present and unique before points can be created.")
    return source, id_field


def _working_crs(source: gpd.GeoDataFrame) -> CRS:
    estimated = source.estimate_utm_crs()
    if estimated is None:
        raise ValueError("Could not determine a projected CRS for metre-based cleanup.")
    return CRS.from_user_input(estimated)


def _find_overlaps(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    rows: list[dict] = []
    index = frame.sindex
    for left_index, left in enumerate(frame.geometry):
        for right_index in index.query(left, predicate="intersects"):
            if right_index <= left_index:
                continue
            overlap = left.intersection(frame.geometry.iloc[right_index])
            if overlap.geom_type in {"Polygon", "MultiPolygon"} and overlap.area > 0:
                rows.append({"parcel_id_a": frame.iloc[left_index]["parcel_id"], "parcel_id_b": frame.iloc[right_index]["parcel_id"], "area_m2": overlap.area, "geometry": overlap})
    if not rows:
        return gpd.GeoDataFrame({"parcel_id_a": [], "parcel_id_b": [], "area_m2": []}, geometry=[], crs=frame.crs)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=frame.crs)


def _polygon_components(geometry) -> list:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    return [item for item in getattr(geometry, "geoms", []) if item.geom_type in {"Polygon", "MultiPolygon"}]


def _empty_review(crs) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"issue_type": [], "min_width_m": [], "status": [], "neighbour_count": []}, geometry=[], crs=crs)


def _connected_problem_groups(candidates: list[dict]) -> list[list[int]]:
    """Group only regions sharing area, never regions that merely touch an edge."""
    remaining = set(range(len(candidates)))
    groups = []
    while remaining:
        group = {remaining.pop()}
        changed = True
        while changed:
            changed = False
            for candidate_index in list(remaining):
                if any(candidates[candidate_index]["geometry"].intersection(candidates[group_index]["geometry"]).area > 0.01 for group_index in group):
                    remaining.remove(candidate_index)
                    group.add(candidate_index)
                    changed = True
        groups.append(sorted(group))
    return groups


def _topology_cleanup(frame: gpd.GeoDataFrame, tolerance_m: float) -> TopologyCleanupResult:
    """Repair all eligible internal conflict regions without order-dependent bias."""
    clean = frame.copy()
    coverage = unary_union(clean.geometry.tolist())
    gap_candidates = interior_gap_polygons(clean.geometry.boundary, coverage)
    pair_overlaps = _find_overlaps(clean)
    overlap_candidates = _polygon_components(unary_union(pair_overlaps.geometry.tolist())) if not pair_overlaps.empty else []
    candidates = [
        {"issue_type": "GAP", "geometry": geometry, "min_width_m": maximum_width_m(geometry)}
        for geometry in gap_candidates
    ] + [
        {"issue_type": "OVERLAP", "geometry": geometry, "min_width_m": maximum_width_m(geometry)}
        for geometry in overlap_candidates
    ]
    eligible = [candidate for candidate in candidates if candidate["min_width_m"] <= tolerance_m]
    review_records = [
        {**candidate, "status": "EXCEEDS_TOLERANCE", "neighbour_count": 0}
        for candidate in candidates
        if candidate["min_width_m"] > tolerance_m
    ]
    groups = _connected_problem_groups(eligible)
    processed_candidate_indexes: set[int] = set()
    affected_positions: set[int] = set()
    for group in groups:
        component = unary_union([eligible[index]["geometry"] for index in group])
        nearby_positions: set[int] = set()
        for candidate_index in group:
            candidate = eligible[candidate_index]
            search_area = candidate["geometry"].buffer(0.001) if candidate["issue_type"] == "GAP" else candidate["geometry"]
            nearby_positions.update(clean.sindex.query(search_area, predicate="intersects"))
        neighbours = {position: clean.geometry.iloc[position] for position in sorted(nearby_positions)}
        issue_types = {eligible[index]["issue_type"] for index in group}
        issue_type = "_AND_".join(sorted(issue_types)) or "TOPOLOGY"
        if len(neighbours) < 2:
            review_records.append({"issue_type": issue_type, "min_width_m": component.minimum_clearance, "status": "INSUFFICIENT_NEIGHBOURS", "neighbour_count": len(neighbours), "geometry": component})
            continue
        shares = allocate_region_on_midlines(component, neighbours, tolerance_m)
        if shares is None:
            review_records.append({"issue_type": issue_type, "min_width_m": component.minimum_clearance, "status": "ALLOCATION_FAILED", "neighbour_count": len(neighbours), "geometry": component})
            continue
        replacements = {}
        for position, geometry in neighbours.items():
            replacement = polygonal_part(geometry.difference(component).union(shares.get(position)))
            if replacement is None:
                review_records.append({"issue_type": issue_type, "min_width_m": component.minimum_clearance, "status": "INVALID_RESULT", "neighbour_count": len(neighbours), "geometry": component})
                replacements = {}
                break
            replacements[position] = replacement
        if not replacements:
            continue
        for position, replacement in replacements.items():
            if clean.geometry.iloc[position].symmetric_difference(replacement).area > 0.01:
                affected_positions.add(position)
            clean.loc[clean.index[position], "geometry"] = replacement
        processed_candidate_indexes.update(group)

    gaps_repaired = sum(1 for index in processed_candidate_indexes if eligible[index]["issue_type"] == "GAP")
    overlaps_repaired = sum(1 for index in processed_candidate_indexes if eligible[index]["issue_type"] == "OVERLAP")
    gap_review_records = [record for record in review_records if "GAP" in record["issue_type"]]
    overlap_review_records = [record for record in review_records if "OVERLAP" in record["issue_type"]]
    gaps_review = gpd.GeoDataFrame(gap_review_records, geometry="geometry", crs=clean.crs) if gap_review_records else _empty_review(clean.crs)
    overlaps_review = gpd.GeoDataFrame(overlap_review_records, geometry="geometry", crs=clean.crs) if overlap_review_records else _empty_review(clean.crs)

    change_records = []
    for position in sorted(affected_positions):
        original = frame.geometry.iloc[position]
        final = clean.geometry.iloc[position]
        change_records.append({
            "parcel_id": clean.iloc[position]["parcel_id"],
            "original_area_m2": original.area,
            "clean_area_m2": final.area,
            "area_change_m2": final.area - original.area,
            "change_status": "TOPOLOGY_ADJUSTED",
            "geometry": final,
        })
    changes = gpd.GeoDataFrame(change_records, geometry="geometry", crs=clean.crs) if change_records else gpd.GeoDataFrame({"parcel_id": [], "original_area_m2": [], "clean_area_m2": [], "area_change_m2": [], "change_status": []}, geometry=[], crs=clean.crs)
    warnings = []
    if gaps_repaired:
        warnings.append(f"{gaps_repaired} internal gap(s) at or below {tolerance_m:g} m were rebuilt using shared midlines.")
    if overlaps_repaired:
        warnings.append(f"{overlaps_repaired} overlap region(s) at or below {tolerance_m:g} m were rebuilt using shared midlines.")
    if review_records:
        warnings.append(f"{len(review_records)} topology issue(s) were left in review layers.")
    return TopologyCleanupResult(clean, gaps_review, overlaps_review, changes, gaps_repaired, overlaps_repaired, len(affected_positions), len(review_records), tuple(warnings))


def _write_layer(frame: gpd.GeoDataFrame, path: Path, layer: str) -> None:
    output = frame.copy()
    types = set(output.geometry.geom_type.dropna())
    kwargs = {"geometry_type": "Polygon"} if not types or types.issubset({"Polygon", "MultiPolygon"}) else {}
    output.to_file(path, layer=layer, driver="GPKG", engine="pyogrio", **kwargs)


def build_project(options: BuildOptions) -> BuildResult:
    options.output_directory.mkdir(parents=True, exist_ok=True)
    source, _ = _load_parcels(options.source, options.parcel_id_field)
    projected_crs = _working_crs(source)
    original = source.to_crs(projected_crs)
    clean = original.copy()
    # Cleanup is a two-dimensional cadastral operation; source altitude values
    # can make GEOS topology operations fail on otherwise valid parcel outlines.
    clean["geometry"] = clean.geometry.map(force_2d)
    clean["geometry_status"] = "VALID"

    if options.repair_internal_gaps:
        topology = _topology_cleanup(clean, options.gap_tolerance_m)
        clean = topology.parcels
        gaps = topology.gaps_review
        overlaps = topology.overlaps_review
        changes = topology.changes
        warnings = list(topology.warnings)
    else:
        gaps = gpd.GeoDataFrame({"gap_id": [], "min_width_m": [], "status": []}, geometry=[], crs=projected_crs)
        overlaps = _empty_review(projected_crs)
        changes = gpd.GeoDataFrame({"parcel_id": [], "original_area_m2": [], "clean_area_m2": [], "area_change_m2": [], "change_status": []}, geometry=[], crs=projected_crs)
        warnings = []
        topology = TopologyCleanupResult(clean, gaps, overlaps, changes, 0, 0, 0, 0, ())

    points = clean[["parcel_id", "geometry"]].copy()
    points["geometry"] = clean.geometry.map(safe_interior_point)
    points["point_method"] = "polylabel"
    points["clearance_m"] = points.geometry.distance(clean.geometry.boundary)
    points["point_status"] = "OK"

    gpkg = options.output_directory / f"{options.output_stem}.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    _write_layer(original, gpkg, GPKG_LAYERS["original"])
    _write_layer(clean, gpkg, GPKG_LAYERS["clean"])
    _write_layer(points, gpkg, GPKG_LAYERS["points"])
    _write_layer(gaps, gpkg, GPKG_LAYERS["gaps"])
    _write_layer(overlaps, gpkg, GPKG_LAYERS["overlaps"])
    _write_layer(changes, gpkg, GPKG_LAYERS["changes"])

    kmz = None
    if options.export_kmz:
        kmz = options.output_directory / f"{options.output_stem}_preview.kmz"
        clean.to_crs("EPSG:4326").to_file(kmz, driver="KML", engine="pyogrio")

    file_gdb = None
    file_gdb_zip = None
    if options.export_file_gdb:
        file_gdb = options.output_directory / f"{options.output_stem}.gdb"
        if file_gdb.exists():
            shutil.rmtree(file_gdb)
        # A GDB is a directory. Include every GIS layer in it, then archive the
        # directory as one transferable .gdb.zip file after all writers close.
        _write_gdb_layer(original, file_gdb, GPKG_LAYERS["original"])
        _write_gdb_layer(clean, file_gdb, GPKG_LAYERS["clean"])
        _write_gdb_layer(points, file_gdb, GPKG_LAYERS["points"])
        _write_gdb_layer(gaps, file_gdb, GPKG_LAYERS["gaps"])
        _write_gdb_layer(overlaps, file_gdb, GPKG_LAYERS["overlaps"])
        _write_gdb_layer(changes, file_gdb, GPKG_LAYERS["changes"])
        file_gdb_zip = Path(shutil.make_archive(str(file_gdb), "zip", root_dir=file_gdb.parent, base_dir=file_gdb.name))

    return BuildResult(
        gpkg,
        kmz,
        file_gdb,
        file_gdb_zip,
        len(clean),
        len(points),
        topology.gaps_repaired,
        topology.overlaps_repaired,
        topology.affected_parcels,
        topology.review_count,
        tuple(warnings),
    )


def _write_gdb_layer(frame: gpd.GeoDataFrame, path: Path, layer: str) -> None:
    """Write a feature class using GDAL's File Geodatabase driver."""
    output = frame.copy()
    # OpenFileGDB accepts multipart areas when the layer is explicitly declared
    # as Polygon. Remove source Z values, which are not needed by this 2D tool
    # and caused some GDAL versions to reject MultiPolygon review layers.
    output["geometry"] = output.geometry.map(force_2d)
    types = set(output.geometry.geom_type.dropna())
    kwargs = {"geometry_type": "Polygon"} if not types or types.issubset({"Polygon", "MultiPolygon"}) else {}
    output.to_file(path, layer=layer, driver="OpenFileGDB", engine="pyogrio", **kwargs)
