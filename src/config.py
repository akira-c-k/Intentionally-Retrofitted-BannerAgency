from __future__ import annotations

import os
import re
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()


def configure_llm_environment() -> tuple[str | None, str | None, str | None]:
    """Configure LLM API credentials, model settings, and base URL from environment variables."""
    api_key = os.getenv("LLM_API_KEY")
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

    model = os.getenv("LLM_MODEL")
    if model:
        os.environ["OPENAI_DEFAULT_MODEL"] = model

    base_url = os.getenv("LLM_BASE_URL")
    if not base_url and model and ("gemini" in model.lower()):
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"

    if base_url:
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


# Playwright configuration
# Can be enabled/disabled via environment variable or CLI argument
def is_playwright_enabled_by_default() -> bool:
    env_val = os.getenv("ENABLE_PLAYWRIGHT_RENDER", "false").strip().lower()
    return env_val in {"1", "true", "yes", "on"}


# Supported image extensions for assets
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")
