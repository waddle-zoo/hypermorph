"""Lossless parser for Superset export ZIP files."""

from __future__ import annotations

import zipfile
from pathlib import Path

import yaml

ASSET_DIRECTORIES = ("databases", "datasets", "charts", "dashboards")


def load_export_bundle(zip_path: str | Path) -> dict[str, list[dict]]:
    """Group source YAML by Superset asset directory without normalizing it."""
    bundle: dict[str, list[dict]] = {key: [] for key in ASSET_DIRECTORIES}
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir() or not info.filename.endswith(".yaml"):
                continue
            asset_directories = [
                part for part in Path(info.filename).parts[:-1] if part in ASSET_DIRECTORIES
            ]
            if not asset_directories:
                continue
            payload = yaml.safe_load(archive.read(info.filename))
            if isinstance(payload, dict):
                bundle[asset_directories[0]].append(payload)
    return bundle
