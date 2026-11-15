from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load config and apply compatibility patches early
from src.config import (
    BANNER_MAX_ITERATIONS,
    BANNER_OUTPUT_DIR,
    BANNER_PREVIOUS_HTML_MAX_CHARS,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    GRAPH_OUTPUT_DIR,
    LLM_API_KEY,
    LLM_MODEL,
    is_playwright_enabled_by_default,
)

from agents.agent import Agent, AgentToolStreamEvent
from agents.run import RunConfig, Runner
from agents.stream_events import AgentUpdatedStreamEvent, RunItemStreamEvent, StreamEvent

from src.banner_agents.orchestrator import build_banner_orchestrator
from src.models import (
    BannerBackgroundSelection,
    BannerCreationOutput,
    BannerDeveloperOutput,
    BannerForegroundPlan,
    BannerRequirements,
)
from src.tools.graph import RuntimeGraphBuilder
from src.tools.renderer import render_banner_files, render_existing_banner_html_file

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Suppress noisy HTTP client logs from interfering with interactive CLI display
for _noisy_logger in ("httpx", "httpcore", "openai", "urllib3"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

_PROGRESS_FRAMES = (
    "□□□□□□□□□□",
    "■□□□□□□□□□",
    "■■□□□□□□□□",
    "■■■□□□□□□□",
    "■■■■□□□□□□",
    "■■■■■□□□□□",
    "■■■■■■□□□□",
    "■■■■■■■□□□",
    "■■■■■■■■□□",
    "■■■■■■■■■□",
    "■■■■■■■■■■",
)


class _ProgressReporter:
    def __init__(self, title: str) -> None:
        self.title = title
        self._frame = 0
        self._active_agent = "orchestrator"
        self._status = "initializing"
        self._line_width = 0

    async def __aenter__(self) -> _ProgressReporter:
        self._render()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._clear_line()

    def update(self, agent_name: str, status: str) -> None:
        self._active_agent = agent_name
        self._status = status
        self._frame = (self._frame + 1) % len(_PROGRESS_FRAMES)
        self._render()

    def record_stream_event(self, event: StreamEvent) -> None:
        if isinstance(event, AgentUpdatedStreamEvent):
            self.update(event.new_agent.name, "agent selected")
        elif isinstance(event, RunItemStreamEvent):
            agent = getattr(event.item, "agent", None)
            agent_name = getattr(agent, "name", "agent") if agent else "agent"
            self.update(agent_name, f"processing {type(event.item).__name__}")

    def record_nested_agent_event(self, payload: AgentToolStreamEvent) -> None:
        agent = payload.get("agent")
        agent_name = getattr(agent, "name", "subagent") if agent else "subagent"
        self.update(agent_name, "working as tool")

    def _render(self) -> None:
        bar = _PROGRESS_FRAMES[self._frame]
        line = f"[{bar}] {self.title} | {self._active_agent}: {self._status}"
        self._line_width = max(self._line_width, len(line))
        sys.stdout.write("\r" + line.ljust(self._line_width))
        sys.stdout.flush()

    def _clear_line(self) -> None:
        if self._line_width:
            sys.stdout.write("\r" + " " * self._line_width + "\r")
            sys.stdout.flush()
            self._line_width = 0


class _NestedStreamObserver:
    def __init__(self, runtime_graph: RuntimeGraphBuilder) -> None:
        self._runtime_graph = runtime_graph
        self.active_progress: _ProgressReporter | None = None

    def __call__(self, payload: AgentToolStreamEvent) -> None:
        self._runtime_graph.record_nested_agent_event(payload)
        if self.active_progress is not None:
            self.active_progress.record_nested_agent_event(payload)


def _safe_theme_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text.strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug[:40] or "banner"


_safe_dir_stem = _safe_theme_slug


def _banner_run_id(theme: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_theme = _safe_theme_slug(theme)
    return f"{timestamp}_{clean_theme}"


async def _resolve_banner_theme(objective: str, theme: str | None = None) -> str:
    if theme and theme.strip():
        return _safe_theme_slug(theme)

    if LLM_API_KEY:
        try:
            agent = Agent(
                name="theme_extractor",
                instructions=(
                    "Extract a concise English theme name (1 to 3 words, snake_case, lowercase) "
                    "representing the given advertising objective, suitable for a folder name. "
                    "Examples: 'spring_sale', 'ai_assistant', 'coffee_promo'. "
                    "Output ONLY the concise snake_case theme name and nothing else."
                ),
            )
            run_config = (
                RunConfig(model=LLM_MODEL, tracing_disabled=True)
                if LLM_MODEL
                else RunConfig(tracing_disabled=True)
            )
            result = await Runner.run(agent, objective, max_turns=1, run_config=run_config)
            output_text = str(result.final_output or "").strip()
            slug = _safe_theme_slug(output_text)
            if slug and slug != "banner":
                return slug
        except Exception as e:
            logger.warning("Failed to extract theme with LLM: %s", e)

    return _safe_theme_slug(objective)


def _read_previous_banner_html(html_path: str) -> str:
    path = Path(html_path)
    if not path.is_file():
        return f"[previous HTML file not found: {html_path}]"
    html_text = path.read_text(encoding="utf-8")
    if len(html_text) <= BANNER_PREVIOUS_HTML_MAX_CHARS:
        return html_text
    return html_text[:BANNER_PREVIOUS_HTML_MAX_CHARS] + "\n[previous HTML truncated]"


def _banner_revision_snapshot(output: BannerCreationOutput) -> str:
    return (
        "Previous banner output JSON (SOURCE OF TRUTH for revision):\n"
        "=== STRICT MINIMAL DIFFERENCE REQUIREMENT ===\n"
        "By default, every single field, element, logo, background, text, and style from this previous output MUST be preserved.\n"
        "Apply surgical changes ONLY to the specific element or text directly targeted by reviewer_feedback.\n"
        "- NEVER replace selected_logo_asset_id / logo_asset_path unless requested.\n"
        "- NEVER add decorative badges / ribbons unless requested.\n"
        "- NEVER rewrite unaffected copies (main_copy, body_copy, cta, note) unless requested.\n"
        "============================================\n"
        + json.dumps(output.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        + "\nPrevious rendered HTML path:\n"
        + output.developer.html_path
        + "\nPrevious rendered HTML content. Read this as the source file to revise for "
        "the next iteration, preserving DOM/CSS unless reviewer_feedback requires a change:\n"
        + _read_previous_banner_html(output.developer.html_path)
    )


def _extract_objective_from_banner_dir(banner_dir: Path) -> str:
    """Extract purpose/objective from banner_data.json or banner.html."""
    data_file = banner_dir / "banner_data.json"
    if data_file.is_file():
        try:
            data = json.loads(data_file.read_text(encoding="utf-8"))
            purpose = data.get("requirements", {}).get("purpose")
            if purpose:
                return str(purpose)
        except Exception:
            pass

    html_file = banner_dir / "banner.html"
    if html_file.is_file():
        try:
            content = html_file.read_text(encoding="utf-8")
            match = re.search(r"<title>(.*?)</title>", content, re.IGNORECASE)
            if match and match.group(1).strip():
                return match.group(1).strip()
        except Exception:
            pass

    return banner_dir.name


def list_existing_banners() -> list[dict[str, Any]]:
    """List existing banner folders sorted by modification time (newest first)."""
    if not BANNER_OUTPUT_DIR.is_dir():
        return []

    results = []
    for entry in BANNER_OUTPUT_DIR.iterdir():
        if not entry.is_dir() or entry.name.startswith((".", "test_")):
            continue
        html_file = entry / "banner.html"
        if not html_file.is_file():
            continue

        objective = _extract_objective_from_banner_dir(entry)
        results.append({
            "id": entry.name,
            "path": entry,
            "objective": objective,
            "mtime": html_file.stat().st_mtime,
        })

    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results


def load_existing_banner(target: str | Path) -> tuple[BannerCreationOutput, str, int, int]:
    """Load an existing banner from banner_id or directory path."""
    target_path = Path(target)
    if not target_path.is_dir():
        candidate = BANNER_OUTPUT_DIR / target
        if candidate.is_dir():
            target_path = candidate
        else:
            raise FileNotFoundError(f"Banner directory not found: {target}")

    banner_id = target_path.name
    data_file = target_path / "banner_data.json"
    if data_file.is_file():
        try:
            output = BannerCreationOutput.model_validate_json(data_file.read_text(encoding="utf-8"))
            return output, banner_id, output.requirements.width, output.requirements.height
        except Exception as e:
            logger.warning("Failed to parse %s: %s, falling back to HTML parsing", data_file, e)

    html_file = target_path / "banner.html"
    if not html_file.is_file():
        raise FileNotFoundError(f"banner.html not found in {target_path}")

    html_content = html_file.read_text(encoding="utf-8")
    title_match = re.search(r"<title>(.*?)</title>", html_content, re.IGNORECASE)
    objective = title_match.group(1).strip() if title_match else banner_id

    # Detect width/height from meta viewport
    width = DEFAULT_WIDTH
    height = DEFAULT_HEIGHT
    meta_match = re.search(r'content="width=(\d+),\s*height=(\d+)', html_content)
    if meta_match:
        width = int(meta_match.group(1))
        height = int(meta_match.group(2))

    # Detect latest iteration
    last_iter = 1
    for p in target_path.glob("banner_iter*.html"):
        m = re.search(r"banner_iter(\d+)\.html$", p.name)
        if m:
            last_iter = max(last_iter, int(m.group(1)))

    svg_file = target_path / "banner.svg"
    png_file = target_path / "banner.png"

    output = BannerCreationOutput(
        iteration=last_iter,
        requirements=BannerRequirements(
            purpose=objective,
            audience="General",
            mood="modern",
            tone="neutral",
            copy_requirements=[],
            width=width,
            height=height,
            notes="",
        ),
        background=BannerBackgroundSelection(
            selected_asset_id=None,
            asset_path=None,
            metadata="{}",
            background_css="",
            rationale="Loaded from existing banner HTML",
            notes="",
        ),
        foreground=BannerForegroundPlan(
            selected_logo_asset_id=None,
            logo_asset_path=None,
            elements=[],
            rationale="Loaded from existing banner HTML",
            notes="",
        ),
        developer=BannerDeveloperOutput(
            html_path=str(html_file.resolve()),
            svg_path=str(svg_file.resolve()) if svg_file.is_file() else None,
            png_path=str(png_file.resolve()) if png_file.is_file() else None,
            notes="Loaded from existing banner files",
        ),
        notes="Loaded from existing banner files",
    )
    return output, banner_id, width, height


def _save_banner_data(output_dir: Path, output: BannerCreationOutput) -> None:
    """Save BannerCreationOutput as JSON in the banner output directory for future resumes."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_data = output.model_dump_json(indent=2)
        (output_dir / "banner_data.json").write_text(json_data, encoding="utf-8")
        (output_dir / f"banner_iter{output.iteration}.json").write_text(json_data, encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save banner_data.json: %s", e)


def _handle_render_action(
    action: str,
    output: BannerCreationOutput,
    width: int,
    height: int,
    enable_playwright: bool,
) -> None:
    parts = action.split(maxsplit=1)
    file_override = parts[1].strip() if len(parts) > 1 else None

    target_path = Path(output.developer.html_path)
    if file_override:
        candidate = target_path.parent / file_override
        if candidate.is_file():
            target_path = candidate
        elif Path(file_override).is_file():
            target_path = Path(file_override)

    render_result = render_existing_banner_html_file(
        html_path=target_path,
        width=width,
        height=height,
        enable_playwright=enable_playwright,
    )
    print(f"\n[Re-render Preview Updated]")
    source_file_path = render_result.get("source_file", render_result["html_path"])
    print(f"  Source (latest): {source_file_path}")
    print(f"  HTML:            {render_result['html_path']}")
    if render_result.get("svg_path"):
        print(f"  SVG:             {render_result['svg_path']}")
    if render_result.get("png_path"):
        print(f"  PNG preview:     {render_result['png_path']}")
    print(f"  Notes:           {render_result.get('notes')}\n")


def apply_minimal_diff_guardrails(
    current: BannerCreationOutput,
    previous: BannerCreationOutput,
    reviewer_feedback: str | None,
) -> BannerCreationOutput:
    """Enforce minimal difference modification as default behavior on revisions."""
    feedback_lower = (reviewer_feedback or "").lower()
    modified = False

    # 1. Background Guardrail: preserve background unless explicitly requested
    bg_keywords = ("背景", "background", "bg", "壁紙", "バックグラウンド", "背景色")
    if not any(kw in feedback_lower for kw in bg_keywords):
        if (
            current.background.selected_asset_id != previous.background.selected_asset_id
            or current.background.background_css != previous.background.background_css
            or current.background.asset_path != previous.background.asset_path
        ):
            current.background = previous.background
            modified = True

    # 2. Logo Asset Guardrail: preserve logo unless explicitly requested
    logo_keywords = ("ロゴ", "logo", "シンボル", "マーク", "brand_logo")
    if not any(kw in feedback_lower for kw in logo_keywords):
        if (
            current.foreground.selected_logo_asset_id != previous.foreground.selected_logo_asset_id
            or current.foreground.logo_asset_path != previous.foreground.logo_asset_path
        ):
            current.foreground.selected_logo_asset_id = previous.foreground.selected_logo_asset_id
            current.foreground.logo_asset_path = previous.foreground.logo_asset_path
            modified = True

    # 3. Decorative Asset Guardrail: do not allow new unrequested decorations
    decor_keywords = ("バッジ", "リボン", "装飾", "アイコン", "badge", "ribbon", "decoration", "tag", "new", "新着")
    if not any(kw in feedback_lower for kw in decor_keywords):
        prev_has_decor = any(el.type == "decorative" for el in previous.foreground.elements)
        if not prev_has_decor:
            filtered_elements = [el for el in current.foreground.elements if el.type != "decorative"]
            if len(filtered_elements) != len(current.foreground.elements):
                current.foreground.elements = filtered_elements
                modified = True

    if modified:
        output_stem = Path(current.developer.html_path).parent.name
        rendered = render_banner_files(
            objective=current.requirements.purpose,
            width=current.requirements.width,
            height=current.requirements.height,
            background_asset_path=current.background.asset_path,
            background_css=current.background.background_css,
            logo_asset_path=current.foreground.logo_asset_path,
            foreground_elements=[el.model_dump() for el in current.foreground.elements],
            output_stem=output_stem,
            iteration=current.iteration,
        )
        current.developer.html_path = rendered["html_path"]
        current.developer.svg_path = rendered.get("svg_path")
        current.developer.png_path = rendered.get("png_path")
        current.developer.notes = (
            current.developer.notes
            + " [Enforced minimal difference modification: preserved previous logo/background/decorations]."
        )

    return current


def _print_banner_creation_output(output: BannerCreationOutput) -> None:
    print("\n=================== BANNER OUTPUT ===================")
    print(f"Iteration: {output.iteration}")
    print(f"Purpose:   {output.requirements.purpose}")
    print(f"Audience:  {output.requirements.audience}")
    print(f"Mood/Tone: {output.requirements.mood} / {output.requirements.tone}")
    print("Copy Requirements:")
    for req in output.requirements.copy_requirements:
        print(f"  - {req}")
    print("\nBackground:")
    print(f"  - Asset ID:  {output.background.selected_asset_id}")
    print(f"  - Path:      {output.background.asset_path}")
    print(f"  - Style:     {output.background.background_css}")
    print(f"  - Rationale: {output.background.rationale}")
    print("\nForeground Elements:")
    for el in output.foreground.elements:
        print(f"  - [{el.type}/{el.role}] '{el.text or el.decorative_asset_path}' at ({el.x}, {el.y}) {el.width}x{el.height} color:{el.color} bg:{el.background}")
    print("\nRendered Files:")
    print(f"  - HTML: {output.developer.html_path}")
    if output.developer.svg_path:
        print(f"  - SVG:  {output.developer.svg_path}")
    if output.developer.png_path:
        print(f"  - PNG:  {output.developer.png_path}")
    else:
        print("  - PNG:  (Disabled or skipped)")
    print(f"  - Notes: {output.developer.notes}")
    print("=====================================================\n")


async def run_banner_pipeline(
    *,
    objective: str | None = None,
    theme: str | None = None,
    resume_target: str | Path | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    notes: str | None = None,
    enable_playwright: bool = False,
    interactive: bool = True,
) -> BannerCreationOutput:
    if resume_target:
        final_output, banner_id, loaded_width, loaded_height = load_existing_banner(resume_target)
        objective = objective or final_output.requirements.purpose
        width = loaded_width or width
        height = loaded_height or height
        start_iteration = final_output.iteration + 1
        previous_summary = _banner_revision_snapshot(final_output)
        print(f"\n================ Loaded Existing Banner ================")
        print(f"Banner ID:           {banner_id}")
        print(f"Directory:           {BANNER_OUTPUT_DIR / banner_id}")
        print(f"Objective:           {objective}")
        print(f"Dimensions:          {width}x{height}")
        print(f"Previous Iterations: {final_output.iteration}")
        print(f"HTML:                {final_output.developer.html_path}")
        print("========================================================\n")
    else:
        if not objective:
            raise ValueError("Objective is required when not resuming an existing banner.")
        resolved_theme = await _resolve_banner_theme(objective, theme=theme)
        banner_id = _banner_run_id(resolved_theme)
        start_iteration = 1
        previous_summary = None
        final_output = None

    runtime_graph = RuntimeGraphBuilder()
    nested_stream_observer = _NestedStreamObserver(runtime_graph)
    orchestrator = build_banner_orchestrator(on_nested_stream=nested_stream_observer)

    reviewer_feedback: str | None = None

    # Set environment variable for tools
    os.environ["ENABLE_PLAYWRIGHT_RENDER"] = "true" if enable_playwright else "false"

    if resume_target and interactive and final_output is not None:
        while True:
            action = input(
                "Review action for resumed banner [Comment (request revision) / Render (re-render) / OK (approve) / Exit]: "
            ).strip().lower()
            if action in {"ok", "approve"}:
                print(f"Banner approved! HTML available at: {final_output.developer.html_path}")
                if final_output.developer.svg_path:
                    print(f"SVG available at: {final_output.developer.svg_path}")
                return final_output

            if action in {"exit", "quit"}:
                print("Banner editing exited.")
                return final_output

            if action in {"render", "re-render", "r"} or action.startswith(("render ", "re-render ")):
                _handle_render_action(action, final_output, width, height, enable_playwright)
                previous_summary = _banner_revision_snapshot(final_output)
                continue

            if action in {"comment", "c"}:
                feedback = input("Enter revision comment: ").strip()
                if feedback:
                    reviewer_feedback = feedback
                    print("Revision comment recorded. Running next iteration...")
                break

            print("Please enter OK, Comment, Render, or Exit.")

    previous_output: BannerCreationOutput | None = final_output
    max_iterations = max(BANNER_MAX_ITERATIONS, start_iteration + 4)
    try:
        for iteration in range(start_iteration, max_iterations + 1):
            print(f"\n--- Starting Banner Creation Iteration {iteration}/{max_iterations} ---")
            if iteration > 1:
                revision_header = (
                    "MODE: REVISION (DEFAULT: STRICT MINIMAL DIFFERENCE MODIFICATION)\n"
                    "CRITICAL: Apply surgical, minimal diff changes strictly based on reviewer feedback.\n"
                    "- PRESERVE previous logo asset, background, and unaffected element styles verbatim.\n"
                    "- DO NOT swap logos unless explicitly asked.\n"
                    "- DO NOT add unsolicited decorative badges or ribbons.\n"
                    "- DO NOT rewrite or rephrase any copy text (main copy, subcopy, CTA, notes) unless explicitly targeted by reviewer feedback.\n\n"
                )
            else:
                revision_header = ""

            banner_query = (
                revision_header
                + "Create an advertising banner using BannerAgency multi-agent orchestration.\n"
                f"Objective: {objective}\n"
                f"Width: {width}\n"
                f"Height: {height}\n"
                f"Notes: {notes or ''}\n"
                f"Banner ID: {banner_id}\n"
                f"Iteration: {iteration}\n"
                f"Reviewer feedback: {reviewer_feedback or ''}\n"
                f"Previous summary: {previous_summary or ''}"
            )

            runtime_graph.record_query(banner_query, orchestrator.name)
            run_config = RunConfig(model=LLM_MODEL, tracing_disabled=True) if LLM_MODEL else RunConfig(tracing_disabled=True)
            streamed = Runner.run_streamed(
                orchestrator,
                banner_query,
                max_turns=12,
                run_config=run_config,
            )

            async with _ProgressReporter("Creating Banner") as progress:
                progress.update("orchestrator", "Dispatching sub-agent tools")
                nested_stream_observer.active_progress = progress
                try:
                    async for event in streamed.stream_events():
                        runtime_graph.record_stream_event(event)
                        progress.record_stream_event(event)
                finally:
                    nested_stream_observer.active_progress = None

            final_output = BannerCreationOutput.model_validate(streamed.final_output)
            if iteration > 1 and previous_output is not None:
                final_output = apply_minimal_diff_guardrails(
                    current=final_output,
                    previous=previous_output,
                    reviewer_feedback=reviewer_feedback,
                )
            previous_output = final_output
            _print_banner_creation_output(final_output)
            _save_banner_data(BANNER_OUTPUT_DIR / banner_id, final_output)
            previous_summary = _banner_revision_snapshot(final_output)

            if not interactive:
                break

            should_stop = False
            while True:
                action = input("Review action [OK (approve) / Comment (request revision) / Render (re-render) / Exit]: ").strip().lower()
                if action in {"ok", "approve"}:
                    print(f"Banner approved! HTML available at: {final_output.developer.html_path}")
                    if final_output.developer.svg_path:
                        print(f"SVG available at: {final_output.developer.svg_path}")
                    should_stop = True
                    break

                if action in {"exit", "quit"}:
                    print("Banner creation exited.")
                    should_stop = True
                    break

                if action in {"render", "re-render", "r"} or action.startswith(("render ", "re-render ")):
                    _handle_render_action(action, final_output, width, height, enable_playwright)
                    previous_summary = _banner_revision_snapshot(final_output)
                    continue

                if action in {"comment", "c"}:
                    feedback = input("Enter revision comment: ").strip()
                    if feedback:
                        reviewer_feedback = feedback
                        print("Revision comment recorded. Running next iteration...")
                    break

                print("Please enter OK, Comment, Render, or Exit.")

            if should_stop or iteration >= max_iterations:
                break

    finally:
        # Save runtime graph
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_graph_base = GRAPH_OUTPUT_DIR / f"banner_graph_{banner_id}"
        md_path, _ = runtime_graph.save(output_graph_base)
        print(f"Runtime orchestration Mermaid graph saved to: {md_path}")

    return final_output  # type: ignore[return-value]


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Agent Advertising Banner Generator")
    parser.add_argument("--objective", "-o", type=str, help="Banner objective description")
    parser.add_argument("--resume", "-r", type=str, default=None, help="Resume editing an existing banner (folder name or path)")
    parser.add_argument("--theme", "-t", type=str, default=None, help="Concise English theme name for output folder (e.g. spring_sale)")
    parser.add_argument("--width", "-w", type=int, default=DEFAULT_WIDTH, help=f"Banner width (default: {DEFAULT_WIDTH})")
    parser.add_argument("--height", "-H", type=int, default=DEFAULT_HEIGHT, help=f"Banner height (default: {DEFAULT_HEIGHT})")
    parser.add_argument("--notes", "-n", type=str, default=None, help="Additional design notes")
    parser.add_argument("--render-png", action="store_true", default=None, help="Enable Playwright PNG preview rendering")
    parser.add_argument("--no-render-png", action="store_false", dest="render_png", help="Disable Playwright PNG preview rendering")
    parser.add_argument("--non-interactive", action="store_true", help="Run once without interactive review loop")

    args = parser.parse_args()

    resume_target = args.resume
    objective = args.objective

    if not resume_target and not objective:
        existing_banners = list_existing_banners()
        if existing_banners:
            print("\nRecent banners found:")
            for idx, b in enumerate(existing_banners[:5], start=1):
                print(f"  [{idx}] {b['id']} ({b['objective']})")
            user_input = input("\nSelect a number to resume editing, or enter a new advertising objective: ").strip()
            if user_input.isdigit() and 1 <= int(user_input) <= len(existing_banners[:5]):
                resume_target = existing_banners[int(user_input) - 1]["id"]
            elif user_input:
                objective = user_input
            else:
                print("Objective or resume target is required.")
                sys.exit(1)
        else:
            objective = input("Enter banner advertising objective: ").strip()
            if not objective:
                print("Objective is required.")
                sys.exit(1)

    width = args.width
    height = args.height

    # Check Playwright rendering choice
    enable_playwright = args.render_png
    if enable_playwright is None:
        default_pw = is_playwright_enabled_by_default()
        choice = input(f"Enable Playwright PNG preview rendering? [y/N] (default: {'Y' if default_pw else 'N'}): ").strip().lower()
        if choice in {"y", "yes"}:
            enable_playwright = True
        elif choice in {"n", "no"}:
            enable_playwright = False
        else:
            enable_playwright = default_pw

    asyncio.run(
        run_banner_pipeline(
            objective=objective,
            theme=args.theme,
            resume_target=resume_target,
            width=width,
            height=height,
            notes=args.notes,
            enable_playwright=enable_playwright,
            interactive=not args.non_interactive,
        )
    )


if __name__ == "__main__":
    main()
