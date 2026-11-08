import geopandas as gpd
import pyogrio
import sqlite3
import zipfile
from shapely.geometry import box

from kml_point_builder.builder import _description_fields, build_project
from kml_point_builder.models import BuildOptions


def test_build_creates_qfield_photo_relation_using_kml_name_id(tmp_path):
    source = gpd.GeoDataFrame(
        {"Name": ["parcel-a", "parcel-b"]},
        geometry=[box(30, 30, 30.001, 30.001), box(30.0012, 30, 30.0022, 30.001)],
        crs="EPSG:4326",
    )
    source_path = tmp_path / "source.geojson"
    source.to_file(source_path, driver="GeoJSON", engine="pyogrio")

    result = build_project(BuildOptions(source_path, tmp_path, "reference"))

    assert result.geopackage.exists()
    assert result.output_directory.name.endswith("_محول")
    assert result.geopackage.parent == result.output_directory
    assert result.kml.exists()
    assert result.qgis_project.exists()
    assert result.attachments_directory.is_dir()
    assert {layer[0] for layer in pyogrio.list_layers(result.geopackage)} == {
        "parcels",
        "reference_points",
        "parcel_attachments",
    }
    parcels = gpd.read_file(result.geopackage, layer="parcels")
    points = gpd.read_file(result.geopackage, layer="reference_points")
    assert list(parcels["parcel_id"]) == ["parcel-a", "parcel-b"]
    assert list(points["parcel_id"]) == ["parcel-a", "parcel-b"]
    assert all(parcel.contains(point) for parcel, point in zip(parcels.geometry, points.geometry))
    assert "<name>parcels</name>" in result.kml.read_text(encoding="utf-8")
    assert "<name>reference_points</name>" in result.kml.read_text(encoding="utf-8")
    with sqlite3.connect(result.geopackage) as connection:
        attachment_columns = {row[1] for row in connection.execute("PRAGMA table_info(parcel_attachments)")}
    assert {"fid", "attachment_uid", "parcel_id", "photo_path"}.issubset(attachment_columns)
    with zipfile.ZipFile(result.qgis_project) as archive:
        project_xml = archive.read("qfield_project.qgs").decode("utf-8")
    assert "parcel_photos" in project_xml
    assert "ExternalResource" in project_xml
    assert "uuid('WithoutBraces')" in project_xml
    assert "<editorlayout>tablayout</editorlayout>" in project_xml
    assert "كود القطعة" in project_xml


def test_reimport_with_existing_parcel_id_keeps_visible_name_as_id(tmp_path):
    source = gpd.GeoDataFrame(
        {"Name": ["visible-title"], "parcel_id": ["old-exported-value"]},
        geometry=[box(30, 30, 30.001, 30.001)],
        crs="EPSG:4326",
    )
    source_path = tmp_path / "reimport.geojson"
    source.to_file(source_path, driver="GeoJSON", engine="pyogrio")

    result = build_project(BuildOptions(source_path, tmp_path, "reimport"))

    parcels = gpd.read_file(result.geopackage, layer="parcels")
    assert list(parcels["parcel_id"]) == ["visible-title"]


def test_arabic_description_extracts_only_qfield_form_fields():
    values = _description_fields(
        "<div>اسم الحائز: محمد علي;</div><div>رقم الحيازة: 007;</div>"
        "<div>الرقم القومي: 01234567890123;</div><div>نوع المحصول: ارز;</div>"
    )

    assert values == {
        "holder_name": "محمد علي",
        "holding_number": "007",
        "national_id": "01234567890123",
        "crop_type": "ارز",
    }


def test_existing_association_output_creates_numbered_folder_without_overwrite(tmp_path):
    association_directory = tmp_path / "جمعية البساتين"
    association_directory.mkdir()
    source = gpd.GeoDataFrame(
        {"Name": ["parcel-a"]}, geometry=[box(30, 30, 30.001, 30.001)], crs="EPSG:4326"
    )
    source_path = association_directory / "source.geojson"
    source.to_file(source_path, driver="GeoJSON", engine="pyogrio")
    output_root = tmp_path / "outputs"

    first = build_project(BuildOptions(source_path, output_root, "reference"))
    second = build_project(BuildOptions(source_path, output_root, "reference"))

    assert first.output_directory.name == "جمعية البساتين_محول"
    assert second.output_directory.name == "جمعية البساتين_محول_2"
    assert first.geopackage.exists()
    assert second.geopackage.exists()
