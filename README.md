# KML Point Builder

Standalone Windows tool that converts a KML/KMZ parcel drawing into GIS-ready
layers without requiring ArcGIS to run the tool.

## Output

The primary output is one GeoPackage (`.gpkg`) containing:

- `parcels_original` — source polygons after valid-geometry repair.
- `parcels_topology_clean` — the working parcel layer after topology cleanup.
- `reference_points` — one `polylabel` point per parcel carrying `parcel_id`.
- `gaps_review` — enclosed narrow gaps needing review.
- `overlaps_review` — overlaps needing review.
- `topology_changes` — parcels changed by the automatic cleanup and their area delta.

Optional output: a File Geodatabase (`.gdb`) with clean parcels and reference
points, plus `name.gdb.zip` containing the complete `.gdb` folder for safe
transfer, and a KMZ preview for Google Earth. Extract the ZIP before adding the
Geodatabase to ArcGIS Pro.

## Topology policy

The UI repairs **enclosed** gaps and overlaps that are 5 m wide or less by
default. Each connected conflict is allocated at once between every affected
parcel using equal-distance shared midlines; this avoids ID or area ordering
bias and supports conflicts involving more than two parcels.

Large or geometrically unresolvable conflicts are retained in `gaps_review` or
`overlaps_review` with a status explaining why they need review.

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

Add `parcels_topology_clean` and `reference_points` from the GeoPackage or File
Geodatabase. After redrawing parcels in a separate editable layer, run Spatial
Join with the redrawn polygons as target and `reference_points` as join features.
Accept only results where `Join_Count = 1`.
