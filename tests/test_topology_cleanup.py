import geopandas as gpd
from shapely.geometry import box

from kml_point_builder.builder import _find_overlaps, _topology_cleanup
from kml_point_builder.geometry import interior_gap_polygons


CRS = "EPSG:32636"


def parcels(polygons):
    return gpd.GeoDataFrame(
        {"parcel_id": [f"parcel-{number}" for number in range(len(polygons))]},
        geometry=polygons,
        crs=CRS,
    )


def test_internal_gap_between_four_parcels_is_distributed_without_overlap():
    source = parcels(
        [
            box(0, 0, 9, 20),
            box(11, 0, 20, 20),
            box(9, 0, 11, 9),
            box(9, 11, 11, 20),
        ]
    )

    result = _topology_cleanup(source, tolerance_m=5)

    assert result.gaps_repaired == 1
    assert result.affected_parcels == 4
    assert _find_overlaps(result.parcels).empty
    assert not interior_gap_polygons(result.parcels.geometry.boundary, result.parcels.union_all())


def test_large_internal_gap_is_preserved_for_review():
    source = parcels(
        [
            box(0, 0, 6, 20),
            box(14, 0, 20, 20),
            box(6, 0, 14, 6),
            box(6, 14, 14, 20),
        ]
    )

    result = _topology_cleanup(source, tolerance_m=5)

    assert result.gaps_repaired == 0
    assert len(result.gaps_review) == 1
    assert result.gaps_review.iloc[0]["status"] == "EXCEEDS_TOLERANCE"


def test_open_space_on_project_edge_is_not_treated_as_gap():
    source = parcels([box(0, 0, 9, 10), box(11, 0, 20, 10)])

    result = _topology_cleanup(source, tolerance_m=5)

    assert result.gaps_repaired == 0
    assert result.gaps_review.empty


def test_three_way_overlap_is_rebuilt_without_remaining_overlap():
    source = parcels([box(0, 0, 12, 20), box(8, 0, 20, 20), box(4, 8, 16, 22)])

    result = _topology_cleanup(source, tolerance_m=5)

    assert result.overlaps_repaired >= 1
    assert _find_overlaps(result.parcels).empty
