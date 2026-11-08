from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildOptions:
    source: Path
    output_directory: Path
    output_stem: str
    parcel_id_field: str | None = None
    create_qfield_project: bool = True


@dataclass(frozen=True)
class BuildResult:
    output_directory: Path
    geopackage: Path
    kml: Path
    qgis_project: Path | None
    attachments_directory: Path
    parcel_count: int
    point_count: int
    warnings: tuple[str, ...]
