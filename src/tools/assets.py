from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field
from agents import function_tool

from src.config import (
    BACKGROUND_ASSET_DIR,
    DECORATION_ASSET_DIR,
    IMAGE_EXTENSIONS,
    LOGO_ASSET_DIR,
)
from src.models import BannerAssetKind


def _asset_dir(kind: BannerAssetKind) -> Path:
    if kind == "background":
        return BACKGROUND_ASSET_DIR
    if kind == "decoration":
        return DECORATION_ASSET_DIR
    return LOGO_ASSET_DIR


def load_banner_asset_metadata(
    *,
    kind: BannerAssetKind,
    asset_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    directory = asset_dir or _asset_dir(kind)
    assets: List[Dict[str, Any]] = []
    if not directory.is_dir():
        return {
            "kind": kind,
            "asset_dir": str(directory),
            "assets": assets,
            "notes": f"Asset directory does not exist: {directory}",
        }

    for metadata_path in sorted(directory.glob("*.json")):
        asset_path = next(
            (
                directory / f"{metadata_path.stem}{extension}"
                for extension in IMAGE_EXTENSIONS
                if (directory / f"{metadata_path.stem}{extension}").is_file()
            ),
            None,
        )
        if asset_path is None:
            continue

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        if not isinstance(metadata, dict):
            continue

        assets.append(
            {
                "id": metadata_path.stem,
                "kind": kind,
                "asset_path": str(asset_path),
                "metadata_path": str(metadata_path),
                "metadata": metadata,
            }
        )

    return {
        "kind": kind,
        "asset_dir": str(directory),
        "assets": assets,
        "notes": f"Loaded {len(assets)} banner {kind} asset metadata item(s).",
    }


@function_tool
def list_banner_assets(
    kind: Annotated[
        BannerAssetKind,
        Field(description="Asset kind to list: background, logo, or decoration."),
    ],
) -> Dict[str, Any]:
    """List banner assets that have same-stem metadata JSON files."""
    return load_banner_asset_metadata(kind=kind)
