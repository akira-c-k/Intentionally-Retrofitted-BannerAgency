from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()


def _patch_openai_token_details() -> None:
    """Compatibility patch for openai-agents with newer openai SDK versions where

    new fields (e.g. cache_write_tokens) were introduced without default values.
    """
    try:
        from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

        if hasattr(InputTokensDetails, "model_fields"):
            for field_name in ("cache_write_tokens", "cached_tokens"):
                if field_name in InputTokensDetails.model_fields:
                    InputTokensDetails.model_fields[field_name].default = 0

        if hasattr(OutputTokensDetails, "model_fields"):
            for field_name in ("reasoning_tokens",):
                if field_name in OutputTokensDetails.model_fields:
                    OutputTokensDetails.model_fields[field_name].default = 0
    except Exception:
        pass


_patch_openai_token_details()

# Base directories
BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
ASSETS_DIR = BASE_DIR / "assets"
BACKGROUND_ASSET_DIR = ASSETS_DIR / "backgrounds"
LOGO_ASSET_DIR = ASSETS_DIR / "logos"
DECORATION_ASSET_DIR = ASSETS_DIR / "decorations"

# Output directories
ARTIFACTS_DIR = BASE_DIR / "artifacts"
OUTPUT_DIR = ARTIFACTS_DIR / "output"
BANNER_OUTPUT_DIR = OUTPUT_DIR / "banners"
BANNER_MEMORY_DIR = OUTPUT_DIR / "memory"
BANNER_SESSION_DB_PATH = OUTPUT_DIR / "banner_memory.sqlite3"
GRAPH_OUTPUT_DIR = ARTIFACTS_DIR / "graph"

# Default parameters
DEFAULT_WIDTH = 300
DEFAULT_HEIGHT = 250
BANNER_MAX_ITERATIONS = 5
BANNER_PREVIOUS_HTML_MAX_CHARS = 30_000


# Playwright configuration
# Can be enabled/disabled via environment variable or CLI argument
def is_playwright_enabled_by_default() -> bool:
    env_val = os.getenv("ENABLE_PLAYWRIGHT_RENDER", "false").strip().lower()
    return env_val in {"1", "true", "yes", "on"}


# Supported image extensions for assets
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")
