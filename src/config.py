from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load .env file
load_dotenv()

_ALLOWED_LOCAL_HTTP_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
}


def validate_llm_base_url(base_url: str) -> str:
    """Validate LLM_BASE_URL using urlparse and exact hostname check."""
    cleaned = base_url.strip()
    if not cleaned:
        raise ValueError("LLM_BASE_URL cannot be empty")
    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").lower()

    if not hostname or parsed.username or parsed.password:
        raise ValueError(f"Invalid LLM_BASE_URL '{cleaned}': missing hostname or contains embedded credentials.")

    if scheme == "https":
        return cleaned
    if scheme == "http" and hostname in _ALLOWED_LOCAL_HTTP_HOSTS:
        return cleaned

    raise ValueError(
        f"Invalid or insecure LLM_BASE_URL '{cleaned}': must use https:// (or http:// with exact host in {sorted(_ALLOWED_LOCAL_HTTP_HOSTS)})."
    )


def configure_llm_environment() -> tuple[str | None, str | None, str | None]:
    """Configure LLM API credentials, model settings, and base URL from environment variables."""
    api_key = os.getenv("LLM_API_KEY")
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    model = os.getenv("LLM_MODEL")
    if model:
        model = re.sub(r"^gemini-(flash|pro)-([0-9.]+)(.*)$", r"gemini-\2-\1\3", model.strip())
        os.environ["OPENAI_DEFAULT_MODEL"] = model

    base_url = os.getenv("LLM_BASE_URL")
    if not base_url and model and ("gemini" in model.lower()):
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"

    if base_url:
        base_url = validate_llm_base_url(base_url)
        os.environ["OPENAI_BASE_URL"] = base_url

    # For non-OpenAI endpoints (e.g. Gemini, custom base_url), disable OpenAI responses API
    # and switch to standard chat completions instead.
    is_non_openai = bool(base_url) or (model and not model.startswith(("gpt-", "o1", "o3", "chatgpt")))
    if is_non_openai:
        try:
            from agents.models import _openai_shared

            _openai_shared.set_use_responses_by_default(False)
        except Exception:
            pass

    return api_key, model, base_url


LLM_API_KEY, LLM_MODEL, LLM_BASE_URL = configure_llm_environment()


def _patch_openai_token_details() -> None:
    """
    Compatibility patch for openai-agents with newer openai SDK versions where
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
GRAPH_OUTPUT_DIR = ARTIFACTS_DIR / "graph"

# Default parameters
DEFAULT_WIDTH = 300
DEFAULT_HEIGHT = 250
BANNER_MAX_ITERATIONS = 5
BANNER_PREVIOUS_HTML_MAX_CHARS = 30_000

# Security & resource limits
MAX_WIDTH = 4096
MAX_HEIGHT = 4096
MAX_PIXELS = 16_000_000
MAX_ASSET_FILE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_FOREGROUND_ELEMENTS = 50
MAX_SVG_ELEMENTS = 2000
MAX_SVG_DEPTH = 32
MAX_SVG_FILTERS = 20
MAX_SVG_STD_DEVIATION = 32.0
MAX_SVG_FILTER_OFFSET = 100.0
MAX_SVG_FILTER_REGION_PCT = 200.0


# Supported image extensions for assets
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")

# Explicit allowlist of loadable SVG assets relative to ASSETS_DIR
ALLOWED_SVG_ASSETS: tuple[str, ...] = (
    # Backgrounds (10)
    "backgrounds/autumn_harvest_bg.svg",
    "backgrounds/clean_minimal_white_bg.svg",
    "backgrounds/cyber_neon_dark_bg.svg",
    "backgrounds/luxury_dark_bg.svg",
    "backgrounds/natural_mint_bg.svg",
    "backgrounds/spring_sale_bg.svg",
    "backgrounds/summer_ocean_blue_bg.svg",
    "backgrounds/tech_blue_bg.svg",
    "backgrounds/vibrant_pop_yellow_bg.svg",
    "backgrounds/warm_sunset_bg.svg",
    # Logos (9)
    "logos/brand_logo_circle.svg",
    "logos/brand_logo_cube.svg",
    "logos/brand_logo_diamond.svg",
    "logos/brand_logo_flame.svg",
    "logos/brand_logo_hex.svg",
    "logos/brand_logo_infinity.svg",
    "logos/brand_logo_leaf.svg",
    "logos/brand_logo_shield.svg",
    "logos/brand_logo_wing.svg",
    # Decorations (9)
    "decorations/discount_burst_badge.svg",
    "decorations/free_shipping_truck_badge.svg",
    "decorations/guarantee_trust_badge.svg",
    "decorations/hot_flame_badge.svg",
    "decorations/limited_time_clock_badge.svg",
    "decorations/new_tag_ribbon.svg",
    "decorations/point_back_coin_badge.svg",
    "decorations/ribbon_badge.svg",
    "decorations/special_crown_badge.svg",
)
