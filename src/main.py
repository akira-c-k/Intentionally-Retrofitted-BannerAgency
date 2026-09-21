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
    BANNER_PREVIOUS_HTML_MAX_CHARS,
    BANNER_SESSION_DB_PATH,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    GRAPH_OUTPUT_DIR,
    is_playwright_enabled_by_default,
)

from agents.agent import Agent, AgentToolStreamEvent
from agents.memory import SQLiteSession
from agents.run import Runner
from agents.stream_events import AgentUpdatedStreamEvent, RunItemStreamEvent, StreamEvent

from src.banner_agents.orchestrator import build_banner_orchestrator
from src.models import BannerCreationOutput
from src.tools.graph import RuntimeGraphBuilder
from src.tools.memory import (
    append_banner_memory_record,
    reset_active_banner_memory_id,
    set_active_banner_memory_id,
)
from src.tools.renderer import render_existing_banner_html_file

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


def _safe_dir_stem(text: str) -> str:
    stem = re.sub(r"[\s_-]+", "_", text.strip().lower())
    stem = "".join(c if c.isalnum() or c == "_" else "_" for c in stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return stem[:80].rstrip("_") or "banner"


def _banner_run_id(objective: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{_safe_dir_stem(objective)}_{timestamp}"


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
        "Previous banner output JSON. In the next revision, preserve every field except "
        "fields directly required by reviewer_feedback:\n"
        + json.dumps(output.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        + "\nPrevious rendered HTML path:\n"
        + output.developer.html_path
        + "\nPrevious rendered HTML content. Read this as the source file to revise for "
        "the next iteration, preserving DOM/CSS unless reviewer_feedback requires a change:\n"
        + _read_previous_banner_html(output.developer.html_path)
    )


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
    objective: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    notes: str | None = None,
    enable_playwright: bool = False,
    interactive: bool = True,
) -> BannerCreationOutput:
    banner_id = _banner_run_id(objective)
    banner_memory_token = set_active_banner_memory_id(banner_id)

    runtime_graph = RuntimeGraphBuilder()
    nested_stream_observer = _NestedStreamObserver(runtime_graph)
    orchestrator = build_banner_orchestrator(on_nested_stream=nested_stream_observer)

    BANNER_SESSION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    session = SQLiteSession(
        session_id=banner_id,
        db_path=BANNER_SESSION_DB_PATH,
    )

    reviewer_feedback: str | None = None
    previous_summary: str | None = None
    final_output: BannerCreationOutput | None = None

    # Set environment variable for tools
    os.environ["ENABLE_PLAYWRIGHT_RENDER"] = "true" if enable_playwright else "false"

    try:
        for iteration in range(1, BANNER_MAX_ITERATIONS + 1):
            print(f"\n--- Starting Banner Creation Iteration {iteration}/{BANNER_MAX_ITERATIONS} ---")
            banner_query = (
                "Create an advertising banner using BannerAgency multi-agent orchestration.\n"
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
            streamed = Runner.run_streamed(
                orchestrator,
                banner_query,
                max_turns=12,
                session=session,
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
            _print_banner_creation_output(final_output)
            previous_summary = _banner_revision_snapshot(final_output)

            if not interactive:
                break

            should_stop = False
            while True:
                action = input("Review action [OK (approve) / Comment (request revision) / Render (re-render) / Exit]: ").strip().lower()
                if action in {"ok", "approve"}:
                    append_banner_memory_record(
                        banner_id=banner_id,
                        iteration=iteration,
                        role="reviewer",
                        content="Approved by reviewer.",
                        record_type="approval",
                    )
                    print(f"Banner approved! HTML available at: {final_output.developer.html_path}")
                    if final_output.developer.svg_path:
                        print(f"SVG available at: {final_output.developer.svg_path}")
                    should_stop = True
                    break

                if action in {"exit", "quit"}:
                    append_banner_memory_record(
                        banner_id=banner_id,
                        iteration=iteration,
                        role="reviewer",
                        content="Reviewer exited banner creation.",
                        record_type="exit",
                    )
                    print("Banner creation exited.")
                    should_stop = True
                    break

                if action in {"render", "re-render", "r"} or action.startswith(("render ", "re-render ")):
                    parts = action.split(maxsplit=1)
                    file_override = parts[1].strip() if len(parts) > 1 else None
                    
                    target_path = Path(final_output.developer.html_path)
                    if file_override:
                        # Allow specifying filename in the banner folder or full path
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
                    previous_summary = _banner_revision_snapshot(final_output)
                    print(f"\n[Re-render Preview Updated]")
                    source_file_path = render_result.get("source_file", render_result["html_path"])
                    print(f"  Source (latest): {source_file_path}")
                    print(f"  HTML:            {render_result['html_path']}")
                    if render_result.get("svg_path"):
                        print(f"  SVG:             {render_result['svg_path']}")
                    if render_result.get("png_path"):
                        print(f"  PNG preview:     {render_result['png_path']}")
                    print(f"  Notes:           {render_result.get('notes')}\n")
                    continue

                if action in {"comment", "c"}:
                    feedback = input("Enter revision comment: ").strip()
                    if feedback:
                        reviewer_feedback = feedback
                        append_banner_memory_record(
                            banner_id=banner_id,
                            iteration=iteration,
                            role="reviewer",
                            content=reviewer_feedback,
                            record_type="review_comment",
                        )
                        print("Revision comment recorded. Running next iteration...")
                    break

                print("Please enter OK, Comment, Render, or Exit.")

            if should_stop or iteration >= BANNER_MAX_ITERATIONS:
                break

    finally:
        reset_active_banner_memory_id(banner_memory_token)
        session.close()

        # Save runtime graph
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_graph_base = GRAPH_OUTPUT_DIR / f"banner_graph_{banner_id}"
        md_path, _ = runtime_graph.save(output_graph_base)
        print(f"Runtime orchestration Mermaid graph saved to: {md_path}")

    return final_output  # type: ignore[return-value]


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Agent Advertising Banner Generator")
    parser.add_argument("--objective", "-o", type=str, help="Banner objective description")
    parser.add_argument("--width", "-w", type=int, default=DEFAULT_WIDTH, help=f"Banner width (default: {DEFAULT_WIDTH})")
    parser.add_argument("--height", "-H", type=int, default=DEFAULT_HEIGHT, help=f"Banner height (default: {DEFAULT_HEIGHT})")
    parser.add_argument("--notes", "-n", type=str, default=None, help="Additional design notes")
    parser.add_argument("--render-png", action="store_true", default=None, help="Enable Playwright PNG preview rendering")
    parser.add_argument("--no-render-png", action="store_false", dest="render_png", help="Disable Playwright PNG preview rendering")
    parser.add_argument("--non-interactive", action="store_true", help="Run once without interactive review loop")

    args = parser.parse_args()

    objective = args.objective
    if not objective:
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
            width=width,
            height=height,
            notes=args.notes,
            enable_playwright=enable_playwright,
            interactive=not args.non_interactive,
        )
    )


if __name__ == "__main__":
    main()
