# KML Point Builder

Standalone Windows tool that converts a KML/KMZ parcel drawing into GIS-ready
layers without requiring ArcGIS to run the tool.

## Output

The primary output is one GeoPackage (`.gpkg`) containing:

- `parcels_original` — source polygons after valid-geometry repair.
- `parcels_clean` — the working parcel layer.
- `reference_points` — one `polylabel` point per parcel carrying `parcel_id`.
- `gaps_review` — enclosed narrow gaps needing review.
- `overlaps_review` — overlaps needing review.

Optional output: a File Geodatabase (`.gdb`) with clean parcels and reference
points, plus `name.gdb.zip` containing the complete `.gdb` folder for safe
transfer, and a KMZ preview for Google Earth. Extract the ZIP before adding the
Geodatabase to ArcGIS Pro.

## Gap policy in version 0.1

The UI detects **enclosed** gaps whose minimum width is 5 m or less by default.
When an enclosed gap has exactly two neighbouring parcels, it is allocated by an
equal-distance midline: each parcel grows only to the centre of the gap. This
is the safe interpretation of "centre the line between parcels". The output
record is marked `REPAIRED_MIDLINE`.

Gaps touching more or fewer than two parcels remain `REVIEW_REQUIRED`, because
there is no unambiguous ownership rule for them.

Open gaps at the outer edge of a project are intentionally ignored: they may be
roads, canals, or unmapped land.

## Development setup

Install Python 3.11 or later, then run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
kml-point-builder
```

## ArcGIS Pro workflow

Add `parcels_clean` and `reference_points` from the GeoPackage or File
Geodatabase. After redrawing parcels in a separate editable layer, run Spatial
Join with the redrawn polygons as target and `reference_points` as join features.
Accept only results where `Join_Count = 1`.
