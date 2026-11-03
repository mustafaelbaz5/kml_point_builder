from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import geopandas as gpd
from pyproj import CRS

from .geometry import centered_interior_point, polygonal_part
from .models import BuildOptions, BuildResult


GPKG_LAYERS = {
    "parcels": "parcels",
    "points": "reference_points",
}


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


def _write_layer(frame: gpd.GeoDataFrame, path: Path, layer: str) -> None:
    output = frame.copy()
    types = set(output.geometry.geom_type.dropna())
    kwargs = {"geometry_type": "Polygon"} if not types or types.issubset({"Polygon", "MultiPolygon"}) else {}
    output.to_file(path, layer=layer, driver="GPKG", engine="pyogrio", **kwargs)


KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
ET.register_namespace("", KML_NAMESPACE)


def _kml_tag(name: str) -> str:
    return f"{{{KML_NAMESPACE}}}{name}"


def _coordinates(coordinates) -> str:
    return " ".join(f"{x:.12f},{y:.12f},0" for x, y, *_ in coordinates)


def _append_polygon(placemark: ET.Element, geometry) -> None:
    polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    container = placemark if len(polygons) == 1 else ET.SubElement(placemark, _kml_tag("MultiGeometry"))
    for polygon in polygons:
        polygon_node = ET.SubElement(container, _kml_tag("Polygon"))
        outer = ET.SubElement(polygon_node, _kml_tag("outerBoundaryIs"))
        outer_ring = ET.SubElement(outer, _kml_tag("LinearRing"))
        ET.SubElement(outer_ring, _kml_tag("coordinates")).text = _coordinates(polygon.exterior.coords)
        for interior in polygon.interiors:
            inner = ET.SubElement(polygon_node, _kml_tag("innerBoundaryIs"))
            inner_ring = ET.SubElement(inner, _kml_tag("LinearRing"))
            ET.SubElement(inner_ring, _kml_tag("coordinates")).text = _coordinates(interior.coords)


def _append_extended_data(placemark: ET.Element, values: dict[str, object]) -> None:
    extended = ET.SubElement(placemark, _kml_tag("ExtendedData"))
    for name, value in values.items():
        if value is None:
            continue
        data = ET.SubElement(extended, _kml_tag("Data"), name=name)
        ET.SubElement(data, _kml_tag("value")).text = str(value)


def _write_kml(parcels: gpd.GeoDataFrame, points: gpd.GeoDataFrame, path: Path) -> None:
    root = ET.Element(_kml_tag("kml"))
    document = ET.SubElement(root, _kml_tag("Document"))
    parcel_folder = ET.SubElement(document, _kml_tag("Folder"))
    ET.SubElement(parcel_folder, _kml_tag("name")).text = GPKG_LAYERS["parcels"]
    for row in parcels.to_crs("EPSG:4326").itertuples(index=False):
        placemark = ET.SubElement(parcel_folder, _kml_tag("Placemark"))
        ET.SubElement(placemark, _kml_tag("name")).text = str(row.parcel_id)
        _append_extended_data(placemark, {"parcel_id": row.parcel_id})
        _append_polygon(placemark, row.geometry)
    point_folder = ET.SubElement(document, _kml_tag("Folder"))
    ET.SubElement(point_folder, _kml_tag("name")).text = GPKG_LAYERS["points"]
    for row in points.to_crs("EPSG:4326").itertuples(index=False):
        placemark = ET.SubElement(point_folder, _kml_tag("Placemark"))
        ET.SubElement(placemark, _kml_tag("name")).text = str(row.parcel_id)
        _append_extended_data(placemark, {"parcel_id": row.parcel_id, "point_method": row.point_method})
        point = ET.SubElement(placemark, _kml_tag("Point"))
        ET.SubElement(point, _kml_tag("coordinates")).text = _coordinates([(row.geometry.x, row.geometry.y)])
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build_project(options: BuildOptions) -> BuildResult:
    options.output_directory.mkdir(parents=True, exist_ok=True)
    source, _ = _load_parcels(options.source, options.parcel_id_field)
    projected_crs = _working_crs(source)
    parcels = source.to_crs(projected_crs)

    points = parcels[["parcel_id", "geometry"]].copy()
    point_results = parcels.geometry.map(centered_interior_point)
    points["geometry"] = point_results.map(lambda result: result[0])
    points["point_method"] = point_results.map(lambda result: result[1])
    points["clearance_m"] = points.geometry.distance(parcels.geometry.boundary)
    points["point_status"] = ["OK" if parcel.contains(point) else "INVALID" for parcel, point in zip(parcels.geometry, points.geometry)]
    if not points["point_status"].eq("OK").all():
        raise ValueError("Could not create an interior reference point for every parcel.")

    gpkg = options.output_directory / f"{options.output_stem}.gpkg"
    if gpkg.exists():
        gpkg.unlink()
    _write_layer(parcels, gpkg, GPKG_LAYERS["parcels"])
    _write_layer(points, gpkg, GPKG_LAYERS["points"])
    kml = options.output_directory / f"{options.output_stem}.kml"
    _write_kml(parcels, points, kml)

    return BuildResult(
        geopackage=gpkg,
        kml=kml,
        parcel_count=len(parcels),
        point_count=len(points),
        warnings=(),
    )
