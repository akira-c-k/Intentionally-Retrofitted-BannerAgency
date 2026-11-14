from __future__ import annotations

from agents.agent import Agent
from src.models import BannerRequirements


def build_banner_strategist() -> Agent:
    """Build the Strategist (企画立案者) Agent."""
    return Agent(
        name="strategist",
        instructions=(
            "You are an expert advertising banner strategist and objective setter. "
            "When a user requests a banner, analyze their input objective and develop high-level "
            "strategy and requirements for an effective banner ad:\n"
            "- Define the primary advertising purpose\n"
            "- Identify the target audience\n"
            "- Establish the appropriate mood and visual atmosphere\n"
            "- Set the copywriting tone (e.g. energetic, trustworthy, luxurious, friendly)\n"
            "- Create clear copy requirements (key selling points, mandatory text, CTA direction)\n"
            "- Maintain canvas dimensions (width and height in pixels)\n\n"
            "Use only text input.\n"
            "When iteration > 1 (STRICT MINIMAL DIFFERENCE DEFAULT):\n"
            "- You MUST preserve purpose, audience, mood, tone, and dimensions from previous_summary.\n"
            "- Only adjust copy_requirements or strategy notes directly affected by `reviewer_feedback`.\n"
            "- Do NOT reinvent requirements or add unrequested constraints.\n\n"
            "Return structured output as BannerRequirements."
        ),
        tools=[],
        output_type=BannerRequirements,
    )
