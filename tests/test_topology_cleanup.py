import geopandas as gpd
import pyogrio
from shapely.geometry import box

from kml_point_builder.builder import build_project
from kml_point_builder.models import BuildOptions


def test_build_creates_only_parcels_and_reference_points_without_changing_parcels(tmp_path):
    source = gpd.GeoDataFrame(
        {"Name": ["parcel-a", "parcel-b"]},
        geometry=[box(30, 30, 30.001, 30.001), box(30.0012, 30, 30.0022, 30.001)],
        crs="EPSG:4326",
    )
    source_path = tmp_path / "source.geojson"
    source.to_file(source_path, driver="GeoJSON", engine="pyogrio")

    result = build_project(BuildOptions(source_path, tmp_path, "reference"))

    assert result.geopackage.exists()
    assert result.kml.exists()
    assert {layer[0] for layer in pyogrio.list_layers(result.geopackage)} == {"parcels", "reference_points"}
    parcels = gpd.read_file(result.geopackage, layer="parcels")
    points = gpd.read_file(result.geopackage, layer="reference_points")
    assert list(parcels["parcel_id"]) == ["parcel-a", "parcel-b"]
    assert list(points["parcel_id"]) == ["parcel-a", "parcel-b"]
    assert all(parcel.contains(point) for parcel, point in zip(parcels.geometry, points.geometry))
    assert "<name>parcels</name>" in result.kml.read_text(encoding="utf-8")
    assert "<name>reference_points</name>" in result.kml.read_text(encoding="utf-8")
