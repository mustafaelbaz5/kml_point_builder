from shapely.geometry import Polygon, box

from kml_point_builder.geometry import centered_interior_point


def test_long_rectangular_parcel_uses_the_true_centroid():
    parcel = box(0, 0, 100, 10)

    point, method = centered_interior_point(parcel)

    assert method == "CENTROID_SAFE"
    assert point.equals(parcel.centroid)


def test_concave_parcel_falls_back_to_a_point_guaranteed_inside():
    parcel = Polygon([(0, 0), (10, 0), (10, 3), (3, 3), (3, 7), (10, 7), (10, 10), (0, 10)])

    point, method = centered_interior_point(parcel)

    assert method == "POLYLABEL_FALLBACK"
    assert parcel.contains(point)
