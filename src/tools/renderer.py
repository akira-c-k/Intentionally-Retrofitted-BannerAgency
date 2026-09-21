from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import mimetypes
import re
import threading
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, Sequence, Union

from pydantic import Field
from agents import function_tool

from src.config import (
    BANNER_OUTPUT_DIR,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    is_playwright_enabled_by_default,
)
from src.models import BannerRenderElement

logger = logging.getLogger(__name__)

ELEMENT_MARGIN = 4
ELEMENT_GAP = 4
ELEMENT_VERTICAL_PADDING = 8
LOGO_LEFT = 12
LOGO_TOP = 12
EN_WIDTH = 0.55
JP_WIDTH = 0.88
JAPANESE_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
ENGLISH_TEXT_RE = re.compile(r"[A-Za-z0-9]")
CSS_LINE_HEIGHT = 1.18


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return stem[:64] or "banner"


def _css_value(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _int_value(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _character_width_ratio(character: str) -> float:
    if JAPANESE_TEXT_RE.fullmatch(character):
        return JP_WIDTH
    if ENGLISH_TEXT_RE.fullmatch(character):
        return EN_WIDTH
    return EN_WIDTH


def _estimated_line_count(line: str, *, width: int, font_size: int) -> int:
    line_count = 1
    current_width = 0
    for character in line:
        character_width = max(
            int(font_size * _character_width_ratio(character)),
            1,
        )
        if current_width > 0 and current_width + character_width > width:
            line_count += 1
            current_width = character_width
            continue
        current_width += character_width
    return line_count


def _estimated_text_height(element: Dict[str, Any]) -> int:
    element_type = str(element.get("type") or "text")
    specified_height = _int_value(element.get("height"), 0)
    if element_type == "decorative":
        return specified_height or 40

    text = str(element.get("text") or element.get("label") or "")
    if not text:
        return specified_height or 28

    width = max(_int_value(element.get("width"), 180), 20)
    font_size = max(_int_value(element.get("font_size"), 20), 10)
    explicit_lines = text.splitlines() or [text]
    estimated_lines = sum(
        _estimated_line_count(line, width=width, font_size=font_size)
        for line in explicit_lines
    )

    bg = str(element.get("background") or "").strip().lower()
    has_bg = bg not in {"", "none", "transparent"}
    padding = 8 if has_bg else 4

    estimated_height = int(estimated_lines * font_size * CSS_LINE_HEIGHT) + padding

    if specified_height > 0:
        return max(specified_height, estimated_height)
    return max(estimated_height, 24)


def _rects_overlap(first: Dict[str, int], second: Dict[str, int]) -> bool:
    return (
        first["x"] < second["x"] + second["width"]
        and first["x"] + first["width"] > second["x"]
        and first["y"] < second["y"] + second["height"]
        and first["y"] + first["height"] > second["y"]
    )


def _logo_rect(*, canvas_width: int, canvas_height: int) -> Dict[str, int]:
    return {
        "x": LOGO_LEFT,
        "y": LOGO_TOP,
        "width": max(48, canvas_width // 4),
        "height": max(32, canvas_height // 5),
    }


def _logo_obstacle_rect(*, canvas_width: int, canvas_height: int) -> Dict[str, int]:
    rect = _logo_rect(canvas_width=canvas_width, canvas_height=canvas_height)
    x = max(rect["x"] - ELEMENT_GAP, 0)
    y = max(rect["y"] - ELEMENT_GAP, 0)
    right = min(rect["x"] + rect["width"] + ELEMENT_GAP, canvas_width)
    bottom = min(rect["y"] + rect["height"] + ELEMENT_GAP, canvas_height)
    return {
        "x": x,
        "y": y,
        "width": right - x,
        "height": bottom - y,
    }


def _image_to_data_uri(source: Optional[Union[str, Path]]) -> Optional[str]:
    """Convert an image file path (SVG, PNG, JPG, WebP) to a Base64 Data URI."""
    if not source:
        return None
    if isinstance(source, str):
        src_str = source.strip()
        if not src_str:
            return None
        if src_str.startswith("data:") or src_str.startswith("http://") or src_str.startswith("https://"):
            return src_str
        path = Path(src_str)
    else:
        path = source

    if not path.is_file():
        candidate = Path.cwd() / path
        if candidate.is_file():
            path = candidate
        else:
            return None

    suffix = path.suffix.lower()
    mime_types = {
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    mime_type = mime_types.get(suffix) or mimetypes.guess_type(path.name)[0] or "image/png"

    try:
        data = path.read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
    except Exception as exc:
        logger.warning(f"Failed to read image file {path} for data URI: {exc}")
        return None


def _normalize_foreground_elements(
    elements: List[Dict[str, Any]],
    *,
    canvas_width: int,
    canvas_height: int,
    logo_asset_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # 1. First pass: parse, estimate moderate height, clamp bounds, and resolve logo collisions
    raw_items: List[Dict[str, Any]] = []
    has_logo = bool(
        logo_asset_path
        and (Path(logo_asset_path).is_file() or logo_asset_path.startswith("data:"))
    )
    logo_obstacle = (
        _logo_obstacle_rect(canvas_width=canvas_width, canvas_height=canvas_height)
        if has_logo
        else None
    )

    for element in elements:
        item = dict(element)
        element_type = str(item.get("type") or "text")
        role = str(item.get("role") or "").lower()
        if role == "logo_image":
            continue

        item["width"] = min(
            max(_int_value(item.get("width"), 180), 20),
            canvas_width - ELEMENT_MARGIN * 2,
        )
        item["font_size"] = max(_int_value(item.get("font_size"), 20), 10)
        item["height"] = min(_estimated_text_height(item), canvas_height - ELEMENT_MARGIN * 2)
        item["x"] = max(
            ELEMENT_MARGIN,
            min(_int_value(item.get("x"), 24), canvas_width - item["width"] - ELEMENT_MARGIN),
        )
        item["y"] = max(
            ELEMENT_MARGIN,
            min(_int_value(item.get("y"), 24), canvas_height - item["height"] - ELEMENT_MARGIN),
        )
        item["font_weight"] = _css_value(item.get("font_weight"), "700")
        item["align"] = _css_value(item.get("align"), "left")
        item["color"] = _css_value(item.get("color"), "#111827")
        item["background"] = _css_value(item.get("background"), "transparent")
        item["border_radius"] = max(_int_value(item.get("border_radius"), 0), 0)

        # Fix logo obstacle conflict for decorative / top elements
        if logo_obstacle and _rects_overlap(
            {"x": item["x"], "y": item["y"], "width": item["width"], "height": item["height"]},
            logo_obstacle,
        ):
            if role == "decorative" or element_type == "decorative":
                # Move decorative badge to top-right corner
                item["x"] = max(canvas_width - item["width"] - 16, ELEMENT_MARGIN)
                item["y"] = LOGO_TOP
            elif role == "logo_text":
                # Move logo text to the right of the logo
                item["x"] = min(
                    logo_obstacle["x"] + logo_obstacle["width"] + 8,
                    canvas_width - item["width"] - ELEMENT_MARGIN,
                )
                item["y"] = LOGO_TOP + 4
            else:
                # Text should start below the logo
                item["y"] = max(item["y"], logo_obstacle["y"] + logo_obstacle["height"] + 6)

        raw_items.append(item)

    # 2. Second pass: Fix vertical stacking overlaps for vertically-ordered text/CTA content
    # Corner/header items (top-right badge, logo text) stay in header flow
    header_roles = {"logo_text", "decorative"}
    main_flow = [item for item in raw_items if item.get("role") not in header_roles or item["y"] > 55]
    header_flow = [item for item in raw_items if item not in main_flow]

    # Sort main body elements by their intended Y position
    main_flow.sort(key=lambda it: it["y"])

    occupied: List[Dict[str, int]] = []
    if logo_obstacle:
        occupied.append(logo_obstacle)
    for h in header_flow:
        occupied.append({"x": h["x"], "y": h["y"], "width": h["width"], "height": h["height"]})

    for item in main_flow:
        # Check if item overlaps vertically with any already placed element in occupied
        for prev in occupied:
            h_overlap = (
                item["x"] < prev["x"] + prev["width"]
                and item["x"] + item["width"] > prev["x"]
            )
            if h_overlap:
                min_y = prev["y"] + prev["height"] + ELEMENT_GAP + 4  # minimum 8px margin
                if item["y"] < min_y:
                    item["y"] = min_y

        # Clamp Y to canvas bottom if needed
        if item["y"] + item["height"] > canvas_height - ELEMENT_MARGIN:
            item["y"] = max(ELEMENT_MARGIN, canvas_height - item["height"] - ELEMENT_MARGIN)

        occupied.append({"x": item["x"], "y": item["y"], "width": item["width"], "height": item["height"]})

    return header_flow + main_flow


def _build_banner_svg(
    *,
    objective: str,
    width: int,
    height: int,
    background_asset_path: Optional[str],
    background_css: Optional[str],
    logo_asset_path: Optional[str],
    foreground_elements: List[Dict[str, Any]],
) -> str:
    bg_style = background_css or "background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);"
    bg_data_uri = _image_to_data_uri(background_asset_path)
    if bg_data_uri:
        bg_element = (
            f'<image href="{bg_data_uri}" xlink:href="{bg_data_uri}" x="0" y="0" '
            f'width="{width}" height="{height}" preserveAspectRatio="xMidYMid slice" />'
        )
    else:
        bg_element = f'<rect width="{width}" height="{height}" style="{html.escape(bg_style)}" />'

    svg_elements: List[str] = [bg_element]

    logo_data_uri = _image_to_data_uri(logo_asset_path)
    if logo_data_uri:
        svg_elements.append(
            f'<image href="{logo_data_uri}" xlink:href="{logo_data_uri}" '
            f'x="{LOGO_LEFT}" y="{LOGO_TOP}" width="{width // 4}" height="{height // 5}" '
            f'preserveAspectRatio="xMidYMid meet" />'
        )

    for el in foreground_elements:
        el_type = el.get("type", "text")
        role = el.get("role", "")
        text = html.escape(str(el.get("text") or el.get("label") or ""))
        x = el.get("x", 20)
        y = el.get("y", 20)
        w = el.get("width", 200)
        h = el.get("height", 40)
        fs = el.get("font_size", 18)
        color = el.get("color", "#ffffff")
        bg = el.get("background", "transparent")
        br = el.get("border_radius", 0)
        fw = el.get("font_weight", "bold")
        align = el.get("align", "left")
        dec_path = el.get("decorative_asset_path")

        justify = "center" if align == "center" else ("flex-end" if align == "right" else "flex-start")

        dec_data_uri = _image_to_data_uri(dec_path)
        if el_type == "decorative" and dec_data_uri:
            content = f'<img src="{dec_data_uri}" alt="Decoration" style="width: 100%; height: 100%; object-fit: contain;" />'
        elif el_type == "cta":
            content = f'<button style="width: 100%; height: 100%; border: none; background: inherit; color: inherit; font-size: inherit; font-weight: inherit; cursor: pointer; display: flex; align-items: center; justify-content: center;">{text}</button>'
        else:
            content = f'<div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: {justify}; text-align: {align}; line-height: {CSS_LINE_HEIGHT};">{text}</div>'

        item_style = (
            f"width: 100%; height: 100%; font-size: {fs}px; color: {color}; background: {bg}; "
            f"border-radius: {br}px; font-weight: {fw}; box-sizing: border-box; overflow: hidden; "
            f"display: flex; align-items: center; justify-content: {justify}; text-align: {align};"
        )
        fo_block = (
            f'<foreignObject x="{x}" y="{y}" width="{w}" height="{h}">\n'
            f'      <div xmlns="http://www.w3.org/1999/xhtml" class="banner-element role-{role}" style="{item_style}">\n'
            f'        {content}\n'
            f'      </div>\n'
            f'    </foreignObject>'
        )
        svg_elements.append(fo_block)

    elements_str = "\n    ".join(svg_elements)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <title>{html.escape(objective)}</title>
  <defs>
    <style>
      * {{
        box-sizing: border-box;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      }}
    </style>
  </defs>
  {elements_str}
</svg>
"""


def _build_banner_html(
    *,
    objective: str,
    width: int,
    height: int,
    background_asset_path: Optional[str],
    background_css: Optional[str],
    logo_asset_path: Optional[str],
    foreground_elements: List[Dict[str, Any]],
) -> str:
    bg_style = background_css or "background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);"
    bg_data_uri = _image_to_data_uri(background_asset_path)
    if bg_data_uri:
        bg_style = f"background-image: url('{bg_data_uri}'); background-size: cover; background-position: center;"

    elements_html: List[str] = []

    logo_data_uri = _image_to_data_uri(logo_asset_path)
    if logo_data_uri:
        elements_html.append(
            f'<div class="banner-logo" style="position: absolute; left: {LOGO_LEFT}px; top: {LOGO_TOP}px; max-width: {width // 4}px; max-height: {height // 5}px; z-index: 10;">'
            f'<img src="{logo_data_uri}" alt="Logo" style="max-width: 100%; max-height: 100%; object-fit: contain;" />'
            f'</div>'
        )

    for el in foreground_elements:
        el_type = el.get("type", "text")
        role = el.get("role", "")
        text = html.escape(str(el.get("text") or el.get("label") or ""))
        x = el.get("x", 20)
        y = el.get("y", 20)
        w = el.get("width", 200)
        h = el.get("height", 40)
        fs = el.get("font_size", 18)
        color = el.get("color", "#ffffff")
        bg = el.get("background", "transparent")
        br = el.get("border_radius", 0)
        fw = el.get("font_weight", "bold")
        align = el.get("align", "left")
        dec_path = el.get("decorative_asset_path")

        justify = "center" if align == "center" else ("flex-end" if align == "right" else "flex-start")

        dec_data_uri = _image_to_data_uri(dec_path)
        if el_type == "decorative" and dec_data_uri:
            content = f'<img src="{dec_data_uri}" alt="Decoration" style="width: 100%; height: 100%; object-fit: contain;" />'
        elif el_type == "cta":
            content = f'<button style="width: 100%; height: 100%; border: none; background: inherit; color: inherit; font-size: inherit; font-weight: inherit; cursor: pointer; display: flex; align-items: center; justify-content: center;">{text}</button>'
        else:
            content = f'<div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: {justify}; text-align: {align}; line-height: {CSS_LINE_HEIGHT};">{text}</div>'

        style = (
            f"position: absolute; left: {x}px; top: {y}px; width: {w}px; height: {h}px; "
            f"font-size: {fs}px; color: {color}; background: {bg}; border-radius: {br}px; "
            f"font-weight: {fw}; box-sizing: border-box; overflow: hidden; z-index: 5;"
        )
        elements_html.append(f'<div class="banner-element role-{role}" style="{style}">{content}</div>')

    elements_str = "\n    ".join(elements_html)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width={width}, height={height}, initial-scale=1.0">
  <title>{html.escape(objective)}</title>
  <style>
    * {{
      margin: 0;
      padding: 0;
      box-sizing: border-box;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }}
    body {{
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      background-color: #f3f4f6;
    }}
    .banner-container {{
      position: relative;
      width: {width}px;
      height: {height}px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
      {bg_style}
    }}
  </style>
</head>
<body>
  <div class="banner-container">
    {elements_str}
  </div>
</body>
</html>
"""


async def _render_file_to_png_async(
    source_path: Path,
    png_path: Path,
    *,
    width: int,
    height: int,
) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=2,  # high-dpi screenshot
            )
            await page.goto(source_path.as_uri(), wait_until="networkidle")
            await page.screenshot(path=str(png_path), full_page=False)
        finally:
            await browser.close()


def _run_async_render_in_thread(
    source_path: Path,
    png_path: Path,
    *,
    width: int,
    height: int,
) -> None:
    error: Optional[BaseException] = None

    def render() -> None:
        nonlocal error
        try:
            asyncio.run(
                _render_file_to_png_async(
                    source_path,
                    png_path,
                    width=width,
                    height=height,
                )
            )
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=render)
    thread.start()
    thread.join()

    if error is not None:
        raise error


def _render_file_to_png(
    source_path: Path,
    png_path: Path,
    *,
    width: int,
    height: int,
) -> bool:
    try:
        _run_async_render_in_thread(source_path, png_path, width=width, height=height)
        return True
    except Exception as exc:
        logger.warning(f"Playwright rendering failed or not configured: {exc}")
        return False


_render_html_to_png = _render_file_to_png


def render_banner_files(
    *,
    objective: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    background_asset_path: Optional[str] = None,
    background_css: Optional[str] = None,
    logo_asset_path: Optional[str] = None,
    foreground_elements: Optional[Sequence[Union[Dict[str, Any], BannerRenderElement]]] = None,
    output_stem: Optional[str] = None,
    iteration: int = 1,
    enable_playwright: Optional[bool] = None,
) -> Dict[str, Any]:
    foreground_elements_list = [
        element.model_dump() if isinstance(element, BannerRenderElement) else element
        for element in foreground_elements or []
    ]
    normalized_elements = _normalize_foreground_elements(
        foreground_elements_list,
        canvas_width=width,
        canvas_height=height,
        logo_asset_path=logo_asset_path,
    )
    digest_source = json.dumps(
        {
            "objective": objective,
            "width": width,
            "height": height,
            "background_asset_path": background_asset_path,
            "background_css": background_css,
            "logo_asset_path": logo_asset_path,
            "foreground_elements": normalized_elements,
            "iteration": iteration,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    dir_name = _safe_stem(output_stem) if output_stem else f"{_safe_stem(objective)}_{digest}"
    output_dir = BANNER_OUTPUT_DIR / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    html_path = output_dir / "banner.html"
    svg_path = output_dir / "banner.svg"
    png_path = output_dir / "banner.png"

    iter_html_path = output_dir / f"banner_iter{iteration}.html"
    iter_svg_path = output_dir / f"banner_iter{iteration}.svg"

    html_content = _build_banner_html(
        objective=objective,
        width=width,
        height=height,
        background_asset_path=background_asset_path,
        background_css=background_css,
        logo_asset_path=logo_asset_path,
        foreground_elements=normalized_elements,
    )
    html_path.write_text(html_content, encoding="utf-8")
    iter_html_path.write_text(html_content, encoding="utf-8")

    svg_content = _build_banner_svg(
        objective=objective,
        width=width,
        height=height,
        background_asset_path=background_asset_path,
        background_css=background_css,
        logo_asset_path=logo_asset_path,
        foreground_elements=normalized_elements,
    )
    svg_path.write_text(svg_content, encoding="utf-8")
    iter_svg_path.write_text(svg_content, encoding="utf-8")

    # Determine whether to run Playwright
    should_render_png = (
        enable_playwright
        if enable_playwright is not None
        else is_playwright_enabled_by_default()
    )

    rendered_png: Optional[str] = None
    notes = "Rendered editable HTML and SVG banners."

    if should_render_png:
        success = _render_file_to_png(html_path, png_path, width=width, height=height)
        if success:
            rendered_png = str(png_path)
            notes += " Generated PNG preview with Playwright."
        else:
            notes += " (Playwright PNG rendering was skipped or failed; HTML and SVG previews are ready)."
    else:
        notes += " (Playwright PNG rendering is disabled; view HTML/SVG files in browser)."

    return {
        "html_path": str(html_path),
        "svg_path": str(svg_path),
        "png_path": rendered_png,
        "notes": notes,
    }


def _find_latest_banner_source_file(target_path: Union[str, Path]) -> Path:
    """Find the most recently modified HTML or SVG file in the banner directory."""
    path = Path(target_path)
    if path.is_file():
        directory = path.parent
        candidates = [
            p for p in (list(directory.glob("*.html")) + list(directory.glob("*.svg")))
            if p.is_file()
        ]
        if not candidates:
            return path
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]
    elif path.is_dir():
        candidates = [
            p for p in (list(path.glob("*.html")) + list(path.glob("*.svg")))
            if p.is_file()
        ]
        if not candidates:
            raise FileNotFoundError(f"No banner HTML or SVG files found in: {path}")
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]
    else:
        raise FileNotFoundError(f"Banner file or directory not found: {path}")


def render_existing_banner_html_file(
    *,
    html_path: Union[str, Path],
    png_path: Optional[Union[str, Path]] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    enable_playwright: bool = True,
    auto_detect_latest: bool = True,
) -> Dict[str, Any]:
    """Render PNG preview from existing banner HTML/SVG, automatically selecting the latest edited file."""
    given_path = Path(html_path)
    if not given_path.exists():
        raise FileNotFoundError(f"Banner file not found: {given_path}")

    # Detect the most recently modified HTML or SVG file in the directory
    if auto_detect_latest:
        source_path = _find_latest_banner_source_file(given_path)
    else:
        source_path = given_path

    directory = source_path.parent

    # Identify companion HTML and SVG files
    html_candidate = directory / "banner.html"
    if not html_candidate.is_file() and source_path.suffix.lower() == ".html":
        html_candidate = source_path

    svg_candidate = directory / "banner.svg"
    if not svg_candidate.is_file() and source_path.suffix.lower() == ".svg":
        svg_candidate = source_path

    output_png_path = (
        Path(png_path) if png_path is not None else source_path.with_suffix(".png")
    )
    main_png_path = directory / "banner.png"

    rendered_png: Optional[str] = None
    notes = f"Loaded banner file: {source_path.name}"

    if enable_playwright:
        success = _render_file_to_png(source_path, output_png_path, width=width, height=height)
        if success:
            rendered_png = str(output_png_path)
            # Sync to main banner.png as well
            if output_png_path.resolve() != main_png_path.resolve():
                try:
                    import shutil
                    shutil.copy2(output_png_path, main_png_path)
                except Exception:
                    pass
            notes = f"Rendered PNG preview from latest modified {source_path.suffix.upper()} ({source_path.name}) via Playwright."
        else:
            notes = "Playwright rendering failed or browser was unavailable."
    else:
        notes = "Playwright rendering is disabled."

    return {
        "source_file": str(source_path),
        "source_type": source_path.suffix.lstrip(".").lower(),
        "html_path": str(html_candidate) if html_candidate.is_file() else str(source_path),
        "svg_path": str(svg_candidate) if svg_candidate.is_file() else None,
        "png_path": rendered_png,
        "notes": notes,
    }


render_existing_banner_file = render_existing_banner_html_file


@function_tool
def render_banner_html(
    objective: Annotated[str, Field(description="Advertising banner objective.")],
    width: Annotated[int, Field(description="Banner width in pixels.")] = DEFAULT_WIDTH,
    height: Annotated[int, Field(description="Banner height in pixels.")] = DEFAULT_HEIGHT,
    background_asset_path: Annotated[
        Optional[str],
        Field(description="Optional selected background image path."),
    ] = None,
    background_css: Annotated[
        Optional[str],
        Field(description="Optional CSS background fallback."),
    ] = None,
    logo_asset_path: Annotated[
        Optional[str],
        Field(description="Optional selected logo image path."),
    ] = None,
    foreground_elements: Annotated[
        Optional[List[BannerRenderElement]],
        Field(
            description=(
                "Foreground layout elements with x, y, width, height, type, role, and text."
            )
        ),
    ] = None,
    output_stem: Annotated[
        Optional[str],
        Field(description="Optional stable output directory stem."),
    ] = None,
    iteration: Annotated[int, Field(description="Current iteration number.")] = 1,
) -> Dict[str, Any]:
    """Render a banner as editable HTML, SVG, and optional PNG preview."""
    return render_banner_files(
        objective=objective,
        width=width,
        height=height,
        background_asset_path=background_asset_path,
        background_css=background_css,
        logo_asset_path=logo_asset_path,
        foreground_elements=foreground_elements,
        output_stem=output_stem,
        iteration=iteration,
    )
