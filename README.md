# KML Point Builder

Standalone Windows tool that converts a KML/KMZ parcel drawing into GIS-ready
layers without requiring ArcGIS to run the tool.

## Output

Every run creates a portable QField package:

The selected output location is treated as a parent folder. The tool creates a
new subfolder named `اسم_الجمعية_محول`, using the input KML/KMZ parent folder as
the association name. It never overwrites an existing package; a later run uses
`اسم_الجمعية_محول_2` instead.

- `name.gpkg` — a GeoPackage containing `parcels` and `reference_points`.
- `name.gpkg` also contains `parcel_attachments`, a child table for zero or more
  photos per parcel. It uses the original KML `Name` value (`parcel_id`) as the relation key.
- `name.kml` — a KML containing folders with the same `parcels` and
  `reference_points`.
- `name_qfield.qgz` — a QGIS/QField project with a one-to-many parcel/photo
  relation and an attachment widget.
- `DCIM/` — the relative attachment directory used for pictures captured in QField.

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

## QField workflow

Copy the complete output folder to the phone and open `name_qfield.qgz` in
QField. Open a parcel form, then use the **Photos** relation to add one or more
pictures. QField writes the image to `DCIM/` and stores its relative path in
`parcel_attachments`. Copy or synchronize the complete folder back to QGIS;
the relationship and photos remain connected through `parcel_id` from the KML `Name`.

The generated project uses standard QGIS relation and attachment configuration.
It must be opened as the `.qgz` project, not by opening the `.gpkg` alone.

## ArcGIS Pro workflow

Add `parcels` and `reference_points` from the GeoPackage. After redrawing
parcels in a separate editable layer, run Spatial Join with the redrawn polygons
as target and `reference_points` as join features. Accept only results where
`Join_Count = 1`.
