from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildOptions:
    source: Path
    output_directory: Path
    output_stem: str
    parcel_id_field: str | None = None
    repair_internal_gaps: bool = True
    gap_tolerance_m: float = 5.0
    export_file_gdb: bool = False
    export_kmz: bool = True


@dataclass(frozen=True)
class BuildResult:
    geopackage: Path
    kmz: Path | None
    file_gdb: Path | None
    file_gdb_zip: Path | None
    parcel_count: int
    point_count: int
    gaps_repaired: int
    overlaps_repaired: int
    affected_parcels: int
    review_count: int
    warnings: tuple[str, ...]
