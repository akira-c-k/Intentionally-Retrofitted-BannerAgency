from __future__ import annotations

from agents.agent import Agent
from src.models import BannerBackgroundSelection
from src.tools.assets import list_banner_assets
from src.tools.memory import record_banner_memory


def build_banner_background_designer() -> Agent:
    """Build the Background Designer (背景画像の選択) Agent."""
    return Agent(
        name="background_designer",
        instructions=(
            "You are an art director specialized in advertising banner backgrounds. "
            "Your task is to select an optimal background image or design an elegant CSS background.\n\n"
            "Decision Workflow:\n"
            "1. Analyze the provided strategist requirements (purpose, audience, mood, tone, dimensions).\n"
            "2. Call list_banner_assets with kind='background' to check available background assets.\n"
            "3. If a listed asset's metadata matches the mood, theme, or audience, select it and populate "
            "selected_asset_id, asset_path, and metadata.\n"
            "4. If no suitable image is found or asset list is empty, set selected_asset_id and asset_path to null, "
            "and provide a rich CSS background (e.g. stylish gradient or solid color) in background_css.\n"
            "5. Do NOT fabricate non-existent file paths.\n"
            "6. When iteration > 1: Review `reviewer_feedback` if provided and adjust the background selection or CSS accordingly.\n"
            "7. Always call record_banner_memory with a short summary before returning.\n\n"
            "Return structured output as BannerBackgroundSelection."
        ),
        tools=[list_banner_assets, record_banner_memory],
        output_type=BannerBackgroundSelection,
    )
