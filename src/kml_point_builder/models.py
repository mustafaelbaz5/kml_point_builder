from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildOptions:
    source: Path
    output_directory: Path
    output_stem: str
    parcel_id_field: str | None = None


@dataclass(frozen=True)
class BuildResult:
    geopackage: Path
    kml: Path
    parcel_count: int
    point_count: int
    warnings: tuple[str, ...]
