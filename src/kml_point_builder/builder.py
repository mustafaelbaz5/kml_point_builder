from __future__ import annotations

import html
import os
import re
import sqlite3
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import geopandas as gpd
from pyproj import CRS

from .geometry import centered_interior_point, polygonal_part
from .models import BuildOptions, BuildResult


GPKG_LAYERS = {
    "parcels": "parcels",
    "points": "reference_points",
    "attachments": "parcel_attachments",
}

QFIELD_GPKG_NAME = "qfield_data.gpkg"
QFIELD_PROJECT_NAME = "qfield_project.qgz"


def _id_field(frame: gpd.GeoDataFrame, requested: str | None) -> str:
    if requested:
        if requested not in frame.columns:
            raise ValueError(f"ID field '{requested}' was not found in the source.")
        return requested
    for name in ("Name", "name", "title", "Title"):
        if name in frame.columns:
            return name
    raise ValueError("No ID field found. Choose the field that contains the unique parcel ID.")


def _description_fields(value: object) -> dict[str, str]:
    """Extract only the four field-work values used in the QField form."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>|</div>|</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text).replace("\xa0", " ")
    result: dict[str, str] = {}
    labels = {
        "holder_name": "اسم الحائز",
        "holding_number": "رقم الحيازة",
        "national_id": "الرقم القومي",
        "crop_type": "نوع المحصول|نوع الزرع",
    }
    for field, label in labels.items():
        match = re.search(rf"(?:^|\n)\s*(?:{label})\s*:\s*([^;\n<]+)", text, flags=re.IGNORECASE)
        result[field] = match.group(1).strip() if match else ""
    return result


def _load_parcels(path: Path, requested_id: str | None) -> tuple[gpd.GeoDataFrame, str]:
    read_options = {}
    # KML created by this tool has one polygon layer and one point layer.
    # Selecting the polygon layer prevents a warning and prevents points from
    # being treated as candidate parcels on a later import.
    if path.suffix.lower() == ".kml":
        try:
            import pyogrio

            layers = pyogrio.list_layers(path)
            layer_names = [name for name, _ in layers]
            if "parcels" in layer_names:
                read_options["layer"] = "parcels"
            polygon_layers = [name for name, geometry_type in layers if geometry_type in {"Polygon", "MultiPolygon"}]
            if polygon_layers and "layer" not in read_options:
                read_options["layer"] = polygon_layers[0]
        except Exception:
            # Fall back to GDAL's default layer when a third-party KML does not
            # expose a layer catalogue.
            pass
    source = gpd.read_file(path, **read_options)
    if source.empty:
        raise ValueError("The KML/KMZ does not contain any readable features.")
    if source.crs is None:
        source = source.set_crs("EPSG:4326")
    source["geometry"] = source.geometry.map(polygonal_part)
    source = source[source.geometry.notna() & ~source.geometry.is_empty].copy()
    if source.empty:
        raise ValueError("No polygon geometry was found in the KML/KMZ.")
    id_field = _id_field(source, requested_id)
    # Re-imported output KML files can contain a ``parcel_id`` attribute in
    # addition to the visible ``Name``. Rename would create duplicate columns;
    # explicitly replace the old value with the selected ID source instead.
    parcel_ids = source[id_field].copy()
    if "parcel_id" in source.columns:
        source = source.drop(columns="parcel_id")
    source["parcel_id"] = parcel_ids.astype(str).str.strip()
    if source["parcel_id"].eq("").any() or source["parcel_id"].duplicated().any():
        raise ValueError("Every KML Name must be present and unique because it is the ID used for points and QField photos.")
    description_column = next((column for column in source.columns if column.lower() == "description"), None)
    extracted = source[description_column].map(_description_fields) if description_column else [{} for _ in range(len(source))]
    for field in ("holder_name", "holding_number", "national_id", "crop_type"):
        source[field] = [values.get(field, "") for values in extracted]
    preferred = ["parcel_id", "holder_name", "holding_number", "national_id", "crop_type", "geometry"]
    source = source[[column for column in preferred if column in source.columns] + [column for column in source.columns if column not in preferred]]
    return source, id_field


def _working_crs(source: gpd.GeoDataFrame) -> CRS:
    estimated = source.estimate_utm_crs()
    if estimated is None:
        raise ValueError("Could not determine a projected CRS for metre-based cleanup.")
    return CRS.from_user_input(estimated)


def _create_output_directory(root: Path, source: Path) -> Path:
    """Create a non-destructive association-named output folder."""
    root.mkdir(parents=True, exist_ok=True)
    association_name = source.parent.name.strip() or source.stem
    base_name = f"{association_name}_محول"
    for number in range(1, 10_000):
        folder_name = base_name if number == 1 else f"{base_name}_{number}"
        candidate = root / folder_name
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError("Could not create a unique output folder.")


def _write_layer(frame: gpd.GeoDataFrame, path: Path, layer: str) -> None:
    output = frame.copy()
    types = set(output.geometry.geom_type.dropna())
    kwargs = {"geometry_type": "Polygon"} if not types or types.issubset({"Polygon", "MultiPolygon"}) else {}
    output.to_file(path, layer=layer, driver="GPKG", engine="pyogrio", **kwargs)


def _create_attachment_table(path: Path) -> None:
    """Create the non-spatial child table required for QField's photo relation."""
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE parcel_attachments (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                attachment_uid TEXT NOT NULL,
                parcel_id TEXT NOT NULL,
                photo_path TEXT,
                original_name TEXT,
                captured_at TEXT,
                notes TEXT,
                attachment_status TEXT NOT NULL DEFAULT 'PENDING'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO gpkg_contents (table_name, data_type, identifier, description, last_change, srs_id)
            VALUES (?, 'attributes', ?, ?, ?, 0)
            """,
            (GPKG_LAYERS["attachments"], GPKG_LAYERS["attachments"], "Photos captured in QField", timestamp),
        )


def _qgs_layer(layer_id: str, layer_name: str, data_source: str, geometry: str, form_xml: str = "") -> str:
    return f"""
    <maplayer type=\"vector\" geometry=\"{geometry}\" hasScaleBasedVisibilityFlag=\"0\" autoRefreshTime=\"0\" autoRefreshEnabled=\"0\">
      <id>{layer_id}</id><datasource>{data_source}</datasource><layername>{layer_name}</layername>
      <provider encoding=\"UTF-8\">ogr</provider><providerKey>ogr</providerKey>
      {form_xml}
    </maplayer>"""


def _write_qfield_project(path: Path, gpkg_name: str) -> None:
    """Create a QField project through the installed QGIS Python runtime."""
    qgis_bat = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "OSGeo4W" / "bin" / "python-qgis.bat"
    script = Path(__file__).with_name("qgis_project.py")
    if not qgis_bat.exists():
        raise RuntimeError("QGIS Desktop (OSGeo4W) is required to create the QField project.")
    completed = subprocess.run(
        ["cmd", "/c", str(qgis_bat), str(script), str(path), gpkg_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise RuntimeError(f"QGIS could not create the QField project: {completed.stderr or completed.stdout}")
    return

    """Legacy manual writer retained below for reference."""
    parcels_id = "land_parcels_qfield"
    attachments_id = "parcel_attachments_qfield"
    points_id = "reference_points_qfield"
    attachments_form = """
      <fieldConfiguration>
        <field name=\"attachment_uid\"><editWidget type=\"Hidden\"><config/></editWidget></field>
        <field name=\"parcel_id\"><editWidget type=\"Hidden\"><config/></editWidget></field>
        <field name=\"photo_path\"><editWidget type=\"ExternalResource\"><config>
          <Option type=\"Map\"><Option name=\"DocumentViewer\" type=\"int\" value=\"1\"/><Option name=\"DocumentViewerContent\" type=\"int\" value=\"1\"/><Option name=\"RelativeStorage\" type=\"int\" value=\"1\"/><Option name=\"DefaultRoot\" type=\"QString\" value=\"DCIM\"/></Option>
        </config></editWidget></field>
      </fieldConfiguration>
      <defaults><default field=\"attachment_uid\" expression=\"uuid('WithoutBraces')\" applyOnUpdate=\"0\"/></defaults>
      <editorlayout>tablayout</editorlayout>
      <attributeEditorForm><attributeEditorContainer name=\"Photo\" groupBox=\"0\">
        <attributeEditorField name=\"photo_path\" index=\"2\"/><attributeEditorField name=\"notes\" index=\"5\"/>
      </attributeEditorContainer></attributeEditorForm>"""
    parcels_form = """
      <fieldConfiguration>
        <field name=\"parcel_id\"><editWidget type=\"TextEdit\"><config/></editWidget></field>
        <field name=\"holder_name\"><editWidget type=\"TextEdit\"><config/></editWidget></field>
        <field name=\"holding_number\"><editWidget type=\"TextEdit\"><config/></editWidget></field>
        <field name=\"national_id\"><editWidget type=\"TextEdit\"><config/></editWidget></field>
        <field name=\"crop_type\"><editWidget type=\"TextEdit\"><config/></editWidget></field>
      </fieldConfiguration>
      <aliases><alias field=\"parcel_id\" index=\"0\" name=\"كود القطعة\"/><alias field=\"holder_name\" index=\"1\" name=\"اسم الحائز\"/><alias field=\"holding_number\" index=\"2\" name=\"رقم الحيازة\"/><alias field=\"national_id\" index=\"3\" name=\"الرقم القومي\"/><alias field=\"crop_type\" index=\"4\" name=\"نوع الزرع\"/></aliases>
      <editable><field name=\"parcel_id\" editable=\"0\"/><field name=\"holder_name\" editable=\"0\"/><field name=\"holding_number\" editable=\"0\"/><field name=\"national_id\" editable=\"0\"/><field name=\"crop_type\" editable=\"1\"/></editable>
      <editorlayout>tablayout</editorlayout>
      <attributeEditorForm><attributeEditorContainer name=\"Parcel\" groupBox=\"0\">
        <attributeEditorRelation name=\"Photos\" relation=\"parcel_photos\" forceSuppressPopup=\"0\" showLinkButton=\"0\" showUnlinkButton=\"0\"/>
        <attributeEditorField name=\"crop_type\" index=\"4\"/><attributeEditorField name=\"parcel_id\" index=\"0\"/><attributeEditorField name=\"holder_name\" index=\"1\"/><attributeEditorField name=\"holding_number\" index=\"2\"/><attributeEditorField name=\"national_id\" index=\"3\"/>
      </attributeEditorContainer></attributeEditorForm>"""
    qgs = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<qgis version=\"3.34.0\" projectname=\"{path.stem}\">
  <homePath path=\".\"/><title>{path.stem}</title>
  <properties><Paths><Absolute type=\"bool\">false</Absolute></Paths></properties>
  <projectlayers>
    {_qgs_layer(parcels_id, "land_parcels", f"./{gpkg_name}|layername=parcels", "Polygon", parcels_form)}
    {_qgs_layer(attachments_id, "parcel_attachments", f"./{gpkg_name}|layername=parcel_attachments", "NoGeometry", attachments_form)}
    {_qgs_layer(points_id, "reference_points", f"./{gpkg_name}|layername=reference_points", "Point")}
  </projectlayers>
  <layer-tree-group name=\"QField parcels\"><layer-tree-layer id=\"{parcels_id}\" name=\"land_parcels\" checked=\"Qt::Checked\"/><layer-tree-layer id=\"{attachments_id}\" name=\"parcel_attachments\" checked=\"Qt::Unchecked\"/><layer-tree-layer id=\"{points_id}\" name=\"reference_points\" checked=\"Qt::Unchecked\"/></layer-tree-group>
  <relations><relation id=\"parcel_photos\" name=\"Photos\" strength=\"Composition\" referencedLayer=\"{parcels_id}\" referencingLayer=\"{attachments_id}\" referencedField=\"parcel_id\" referencingField=\"parcel_id\"/></relations>
</qgis>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{path.stem}.qgs", qgs)


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
    output_directory = _create_output_directory(options.output_directory, options.source)
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

    # Keep data-source names ASCII-only because Android builds of QField can
    # fail to resolve a relative datasource containing Arabic file names.
    gpkg = output_directory / QFIELD_GPKG_NAME
    if gpkg.exists():
        gpkg.unlink()
    _write_layer(parcels, gpkg, GPKG_LAYERS["parcels"])
    _write_layer(points, gpkg, GPKG_LAYERS["points"])
    _create_attachment_table(gpkg)
    kml = output_directory / f"{options.output_stem}.kml"
    _write_kml(parcels, points, kml)
    attachments_directory = output_directory / "DCIM"
    attachments_directory.mkdir(exist_ok=True)
    qgis_project = None
    if options.create_qfield_project:
        qgis_project = output_directory / QFIELD_PROJECT_NAME
        _write_qfield_project(qgis_project, gpkg.name)

    return BuildResult(
        output_directory=output_directory,
        geopackage=gpkg,
        kml=kml,
        qgis_project=qgis_project,
        attachments_directory=attachments_directory,
        parcel_count=len(parcels),
        point_count=len(points),
        warnings=(),
    )
