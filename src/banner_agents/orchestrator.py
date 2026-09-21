from __future__ import annotations

from typing import Any, Optional
from agents.agent import Agent

from src.banner_agents.background_designer import build_banner_background_designer
from src.banner_agents.developer import build_banner_developer
from src.banner_agents.front_designer import build_banner_front_designer
from src.banner_agents.front_refiner import build_banner_front_refiner
from src.banner_agents.strategist import build_banner_strategist
from src.models import (
    BannerBackgroundInput,
    BannerCreationOutput,
    BannerDeveloperInput,
    BannerForegroundInput,
    BannerForegroundRefinementInput,
    BannerInput,
)


def build_banner_orchestrator(on_nested_stream: Optional[Any] = None) -> Agent:
    """Build the Banner Orchestrator (指揮者) Agent.

    The Orchestrator dispatches specialized sub-agents as tools in order:
    1. create_banner_strategy (Strategist)
    2. select_banner_background (Background Designer)
    3. plan_banner_front (Front Designer - Copywriting & Initial Layout)
    4. refine_banner_front (Front Refiner - Layout & Overlap Optimization)
    5. develop_banner (Developer - HTML/CSS & Preview Rendering)
    """
    return Agent(
        name="orchestrator",
        instructions=(
            "You are the master banner production orchestrator and task dispatcher. "
            "You do not invent copy, choose images, calculate layout geometry, or render files directly. "
            "Instead, you orchestrate specialized LLM expert tools in this exact sequence:\n\n"
            "Execution Steps:\n"
            "Step 1: Call `create_banner_strategy` with the user's objective, dimensions, iteration, "
            "reviewer_feedback, and previous_summary to produce strategic requirements.\n"
            "Step 2: Call `select_banner_background` with requirements, banner_id, iteration, reviewer_feedback, "
            "and previous_summary to obtain a background image or clean CSS fallback.\n"
            "Step 3: Call `plan_banner_front` with requirements, background, banner_id, iteration, "
            "reviewer_feedback, and previous_summary to draft copy, CTA, logo, and responsive layout positions.\n"
            "Step 4: Call `refine_banner_front` with requirements, background, draft foreground, banner_id, "
            "iteration, reviewer_feedback, and previous_summary to eliminate overlap and optimize visual hierarchy.\n"
            "Step 5: Call `develop_banner` with requirements, background, refined foreground, banner_id, "
            "iteration, reviewer_feedback, and previous_summary (including previous_html_path / previous_html if present) "
            "to render final HTML, SVG, and optional preview files.\n\n"
            "Finally, combine the exact tool outputs into a unified BannerCreationOutput without "
            "modifying any file paths or decisions."
        ),
        output_type=BannerCreationOutput,
        tool_use_behavior="run_llm_again",
        tools=[
            build_banner_strategist().as_tool(
                tool_name="create_banner_strategy",
                tool_description=(
                    "Define banner requirements from an advertising objective. "
                    "Returns structured output with `BannerRequirements`."
                ),
                parameters=BannerInput,
                on_stream=on_nested_stream,
            ),
            build_banner_background_designer().as_tool(
                tool_name="select_banner_background",
                tool_description=(
                    "Select a background asset from catalog metadata or provide a CSS fallback. "
                    "Returns structured output with `BannerBackgroundSelection`."
                ),
                parameters=BannerBackgroundInput,
                on_stream=on_nested_stream,
            ),
            build_banner_front_designer().as_tool(
                tool_name="plan_banner_front",
                tool_description=(
                    "Draft catchcopy, subcopy, CTA, logo, and initial foreground layout plan. "
                    "Returns structured output with `BannerForegroundPlan`."
                ),
                parameters=BannerForegroundInput,
                on_stream=on_nested_stream,
            ),
            build_banner_front_refiner().as_tool(
                tool_name="refine_banner_front",
                tool_description=(
                    "Refine foreground element positions to eliminate overlap and fit within canvas. "
                    "Returns structured output with `BannerForegroundPlan`."
                ),
                parameters=BannerForegroundRefinementInput,
                on_stream=on_nested_stream,
            ),
            build_banner_developer().as_tool(
                tool_name="develop_banner",
                tool_description=(
                    "Render the banner as editable HTML and preview image. "
                    "Returns structured output with `BannerDeveloperOutput`."
                ),
                parameters=BannerDeveloperInput,
                on_stream=on_nested_stream,
            ),
        ],
    )
