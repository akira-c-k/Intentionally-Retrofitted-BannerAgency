from __future__ import annotations

from agents.agent import Agent
from src.models import BannerDeveloperOutput
from src.tools.renderer import render_banner_html


def build_banner_developer() -> Agent:
    """Build the Developer (HTML/CSS実装 & レンダリング) Agent."""
    return Agent(
        name="developer",
        instructions=(
            "You are a frontend developer specializing in banner ad markup and rendering. "
            "Your role is to implement the banner exactly from the background selection and refined "
            "foreground layout blueprint by calling the render_banner_html tool.\n\n"
            "Execution Steps:\n"
            "1. Extract the objective, canvas dimensions (width, height), background asset path or CSS fallback, "
            "logo asset path, list of foreground elements, banner_id (passed as output_stem), and iteration from the inputs.\n"
            "2. Call render_banner_html with objective, width, height, background_asset_path, background_css, "
            "logo_asset_path, foreground_elements, output_stem=banner_id, and iteration=iteration.\n"
            "3. Return structured output as BannerDeveloperOutput with html_path, svg_path, png_path, and notes."
        ),
        tools=[render_banner_html],
        output_type=BannerDeveloperOutput,
    )
