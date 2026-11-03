# KML Point Builder

Standalone Windows tool that converts a KML/KMZ parcel drawing into GIS-ready
layers without requiring ArcGIS to run the tool.

## Output

Every run creates exactly two files:

- `name.gpkg` — a GeoPackage containing `parcels` and `reference_points`.
- `name.kml` — a KML containing folders with the same `parcels` and
  `reference_points`.

The original parcel outlines are retained as read. The tool does not repair or
change gaps, overlaps, or shared boundaries.

Each reference point carries the parcel's original `parcel_id`. The tool first
uses the true centroid when it is safely inside the parcel; for concave or
irregular parcels it uses an interior fallback point instead. This prevents a
point from being placed outside its own parcel.

## Development setup

Install Python 3.11 or later, then run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
kml-point-builder
```

## ArcGIS Pro workflow

Add `parcels` and `reference_points` from the GeoPackage. After redrawing
parcels in a separate editable layer, run Spatial Join with the redrawn polygons
as target and `reference_points` as join features. Accept only results where
`Join_Count = 1`.
