from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import mimetypes
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, Sequence, Union

from pydantic import Field
from agents import function_tool

from src.config import (
    ASSETS_DIR,
    BANNER_OUTPUT_DIR,
    BASE_DIR,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    IMAGE_EXTENSIONS,
    MAX_ASSET_FILE_BYTES,
    MAX_FOREGROUND_ELEMENTS,
    MAX_HEIGHT,
    MAX_PIXELS,
    MAX_WIDTH,
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

DEFAULT_BG_STYLE = "background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);"

_DANGEROUS_CSS_PATTERN = re.compile(
    r"(?:;|[{}<>\\\r\n\x00]|/\*|\*/|url\s*\(|expression\s*\(|javascript\s*:|vbscript\s*:|data\s*:|@import|-moz-binding|behavior\s*:)",
    re.IGNORECASE,
)
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_FUNC_COLOR_RE = re.compile(r"^(?:rgb|rgba|hsl|hsla)\(\s*[-+0-9.,%\s/]+\)$", re.IGNORECASE)
_SAFE_COLOR_KEYWORDS = {
    "transparent",
    "currentcolor",
    "inherit",
    "white",
    "black",
    "red",
    "blue",
    "green",
    "yellow",
    "orange",
    "purple",
    "pink",
    "gray",
    "grey",
    "cyan",
    "magenta",
    "gold",
    "silver",
    "navy",
    "teal",
    "indigo",
    "violet",
    "coral",
    "salmon",
    "khaki",
    "ivory",
    "beige",
    "crimson",
    "maroon",
    "olive",
    "aqua",
    "lime",
    "fuchsia",
}
_GRADIENT_START_RE = re.compile(
    r"^(?:repeating-)?(?:linear|radial|conic)-gradient\(",
    re.IGNORECASE,
)
_SAFE_GRADIENT_CHARS_RE = re.compile(r"^[a-zA-Z0-9#(),.%+\-\s/]+$")
_SAFE_FONT_WEIGHTS = {
    "normal",
    "bold",
    "bolder",
    "lighter",
    "100",
    "200",
    "300",
    "400",
    "500",
    "600",
    "700",
    "800",
    "900",
}
_SAFE_ALIGNS = {"left", "center", "right"}
_SAFE_FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9\s,\-'_]+$")
_SAFE_SHADOW_RE = re.compile(r"^[A-Za-z0-9#(),.%+\-\s]+$")
_SAFE_BORDER_RE = re.compile(
    r"^(?P<width>\d+(?:\.\d+)?(?:px|em|rem)?)\s+(?P<style>solid|dashed|dotted|double|none)\s+(?P<color>.+)$",
    re.IGNORECASE,
)
_SAFE_LETTER_SPACING_RE = re.compile(r"^(?:normal|-?\d+(?:\.\d+)?(?:px|em|rem|%))$", re.IGNORECASE)
_DANGEROUS_SVG_PATTERN = re.compile(
    r"(?:<\s*script\b|javascript\s*:|vbscript\s*:|\bon[a-z]+\s*=|<\s*(?:iframe|object|embed|foreignobject)\b|"
    r"(?:xlink:href|href|src)\s*=\s*['\"]?\s*(?:https?:|//|data:text/html)|"
    r"url\s*\(\s*['\"]?\s*(?:https?:|//)|@import)",
    re.IGNORECASE,
)
_DATA_URI_RE = re.compile(
    r"^data:(image/(?:png|jpeg|webp|svg\+xml));base64,([A-Za-z0-9+/=\s]+)$",
    re.IGNORECASE,
)


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    stem = re.sub(r"_+", "_", stem)
    return stem[:64] or "banner"


def _safe_role(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(value or ""))[:64]


def validate_canvas_dimensions(width: int, height: int) -> None:
    """Validate banner canvas width, height, and total pixel count (SEC-005)."""
    if not isinstance(width, int) or isinstance(width, bool) or not (1 <= width <= MAX_WIDTH):
        raise ValueError(f"Invalid width: {width} (must be between 1 and {MAX_WIDTH})")
    if not isinstance(height, int) or isinstance(height, bool) or not (1 <= height <= MAX_HEIGHT):
        raise ValueError(f"Invalid height: {height} (must be between 1 and {MAX_HEIGHT})")
    if width * height > MAX_PIXELS:
        raise ValueError(f"Canvas size {width}x{height} exceeds limit of {MAX_PIXELS} pixels")


def _has_dangerous_css_tokens(value: str) -> bool:
    if not value or len(value) > 512:
        return True
    if _DANGEROUS_CSS_PATTERN.search(value):
        return True
    if value.count("(") != value.count(")"):
        return True
    return False


def validate_css_color(value: Any, fallback: str = "#111827") -> str:
    """Validate a CSS color value against an allowlist of hex, rgb/rgba/hsl/hsla, and safe keywords (SEC-001)."""
    if not isinstance(value, str):
        return fallback
    cleaned = value.strip().rstrip(";").strip()
    if not cleaned or _has_dangerous_css_tokens(cleaned) or ":" in cleaned:
        return fallback
    if _HEX_COLOR_RE.fullmatch(cleaned):
        return cleaned
    if _FUNC_COLOR_RE.fullmatch(cleaned):
        return cleaned
    if cleaned.lower() in _SAFE_COLOR_KEYWORDS:
        return cleaned
    return fallback


def validate_css_background(value: Any, fallback: str = "transparent") -> str:
    """Validate an element CSS background value (color or safe gradient, no url() or injection) (SEC-001)."""
    if not isinstance(value, str):
        return fallback
    cleaned = value.strip().rstrip(";").strip()
    if not cleaned:
        return fallback
    if _has_dangerous_css_tokens(cleaned) or ":" in cleaned:
        return fallback
    if cleaned.lower() in {"none", "transparent"}:
        return cleaned.lower()
    as_color = validate_css_color(cleaned, fallback="")
    if as_color:
        return as_color
    if _GRADIENT_START_RE.match(cleaned) and _SAFE_GRADIENT_CHARS_RE.fullmatch(cleaned):
        return cleaned
    return fallback


def validate_background_css(value: Any, fallback: str = DEFAULT_BG_STYLE) -> str:
    """Validate container background_css declaration against CSS injection (SEC-001, SEC-003)."""
    if not isinstance(value, str) or not value.strip():
        return fallback
    cleaned = value.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    prefix_match = re.match(r"^background(?:-color|-image)?\s*:\s*(.+)$", cleaned, re.IGNORECASE | re.DOTALL)
    inner = prefix_match.group(1).strip() if prefix_match else cleaned
    safe_bg = validate_css_background(inner, fallback="")
    if not safe_bg:
        return fallback
    return f"background: {safe_bg};"


def validate_css_font_weight(value: Any, fallback: str = "700") -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(int(value))
    if isinstance(value, str):
        cleaned = value.strip().rstrip(";").lower()
        if cleaned in _SAFE_FONT_WEIGHTS:
            return cleaned
    return fallback


def validate_css_align(value: Any, fallback: str = "left") -> str:
    if isinstance(value, str):
        cleaned = value.strip().rstrip(";").lower()
        if cleaned in _SAFE_ALIGNS:
            return cleaned
    return fallback


def validate_css_font_family(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().rstrip(";").replace('"', "'").strip()
    if not cleaned or len(cleaned) > 256 or _has_dangerous_css_tokens(cleaned) or ":" in cleaned or "(" in cleaned:
        return None
    if _SAFE_FONT_FAMILY_RE.fullmatch(cleaned):
        return cleaned
    return None


def validate_css_shadow(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().rstrip(";").strip()
    if not cleaned or len(cleaned) > 256 or cleaned.lower() in {"none", "null"}:
        return None
    if _has_dangerous_css_tokens(cleaned) or ":" in cleaned:
        return None
    if _SAFE_SHADOW_RE.fullmatch(cleaned):
        return cleaned
    return None


def validate_css_border(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().rstrip(";").strip()
    if not cleaned or len(cleaned) > 256 or cleaned.lower() in {"none", "null"}:
        return None
    if _has_dangerous_css_tokens(cleaned) or ":" in cleaned:
        return None
    m = _SAFE_BORDER_RE.fullmatch(cleaned)
    if not m:
        return None
    color_part = validate_css_color(m.group("color"), fallback="")
    if not color_part:
        return None
    width_part = m.group("width")
    if width_part.isdigit() and width_part != "0":
        width_part += "px"
    return f"{width_part} {m.group('style').lower()} {color_part}"


def validate_css_letter_spacing(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().rstrip(";").strip()
    if not cleaned or cleaned.lower() in {"none", "null"}:
        return None
    if _SAFE_LETTER_SPACING_RE.fullmatch(cleaned):
        return cleaned
    return None


def _optional_css_value(value: Any, *, key: Optional[str] = None) -> Optional[str]:
    if key == "font_family":
        return validate_css_font_family(value)
    if key in {"text_shadow", "box_shadow"}:
        return validate_css_shadow(value)
    if key == "border":
        return validate_css_border(value)
    if key == "letter_spacing":
        return validate_css_letter_spacing(value)
    if isinstance(value, str):
        cleaned = value.strip().rstrip(";").replace('"', "'")
        if cleaned and cleaned.lower() not in {"none", "null"} and not _has_dangerous_css_tokens(cleaned) and ":" not in cleaned:
            return cleaned
    return None


def _extra_element_styles(el: Dict[str, Any]) -> str:
    parts: List[str] = []
    css_props = (
        ("font_family", "font-family"),
        ("text_shadow", "text-shadow"),
        ("box_shadow", "box-shadow"),
        ("border", "border"),
        ("letter_spacing", "letter-spacing"),
    )
    for key, css_prop in css_props:
        val = _optional_css_value(el.get(key), key=key)
        if val:
            parts.append(f"{css_prop}: {val};")
    return (" " + " ".join(parts)) if parts else ""


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


def _normalize_text_newlines(value: Any) -> str:
    return str(value or "").replace("\\r\\n", "\n").replace("\\n", "\n").replace("¥n", "\n")


def _estimated_text_height(element: Dict[str, Any]) -> int:
    element_type = str(element.get("type") or "text")
    specified_height = _int_value(element.get("height"), 0)
    if element_type == "decorative":
        return specified_height or 40

    text = _normalize_text_newlines(element.get("text") or element.get("label") or "")
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


def _validate_image_bytes(data: bytes, suffix: str) -> None:
    """Validate image file size, binary signature (magic bytes), or SVG markup safety (SEC-002, SEC-003, SEC-004, SEC-005)."""
    if not data or len(data) > MAX_ASSET_FILE_BYTES:
        raise ValueError(f"Asset data size ({len(data)} bytes) is empty or exceeds limit ({MAX_ASSET_FILE_BYTES} bytes)")

    ext = suffix.lower()
    if ext == ".png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid PNG file header")
    elif ext in {".jpg", ".jpeg"}:
        if not data.startswith(b"\xff\xd8\xff"):
            raise ValueError("Invalid JPEG file header")
    elif ext == ".webp":
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            raise ValueError("Invalid WebP file header")
    elif ext == ".svg":
        try:
            svg_text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid UTF-8 encoding in SVG file") from exc
        if "<svg" not in svg_text.lower():
            raise ValueError("File does not contain a valid <svg> root element")
        if _DANGEROUS_SVG_PATTERN.search(svg_text):
            raise ValueError("Unsafe SVG content detected (scripts, event handlers, or external URLs are forbidden)")
    else:
        raise ValueError(f"Unsupported image extension: {ext}")


def validate_asset_path(source: Union[str, Path]) -> Path:
    """Validate that an asset path resolves strictly inside ASSETS_DIR and is a valid, safe image file (SEC-002)."""
    asset_root = ASSETS_DIR.resolve()

    if isinstance(source, str):
        src_str = source.strip()
        if not src_str:
            raise ValueError("Asset path is empty")
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", src_str) or src_str.startswith("//"):
            raise ValueError(f"External URLs or URI schemes are forbidden for asset paths: {src_str}")
        candidate = Path(src_str)
    elif isinstance(source, Path):
        candidate = source
    else:
        raise ValueError(f"Invalid asset path type: {type(source)}")

    if not candidate.is_absolute():
        if len(candidate.parts) == 1 and candidate.suffix == "" and re.fullmatch(r"[A-Za-z0-9_-]+", candidate.name):
            for subdir in ("backgrounds", "logos", "decorations"):
                for ext in IMAGE_EXTENSIONS:
                    possible = asset_root / subdir / f"{candidate.name}{ext}"
                    if possible.is_file():
                        candidate = possible
                        break
                if candidate.is_absolute():
                    break
        resolved = (BASE_DIR / candidate).resolve(strict=False)
        if not resolved.exists() and (Path.cwd() / candidate).exists():
            resolved = (Path.cwd() / candidate).resolve(strict=False)
    else:
        resolved = candidate.resolve(strict=False)

    try:
        is_inside = resolved.is_relative_to(asset_root)
    except ValueError:
        is_inside = False

    if not is_inside:
        raise ValueError(f"Access outside asset directory is forbidden: {source}")

    if not resolved.is_file():
        raise ValueError(f"Invalid asset file (does not exist or is not a file): {source}")

    suffix = resolved.suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported asset extension '{suffix}' for {source}")

    file_size = resolved.stat().st_size
    if file_size <= 0 or file_size > MAX_ASSET_FILE_BYTES:
        raise ValueError(f"Asset file size ({file_size} bytes) exceeds limit")

    _validate_image_bytes(resolved.read_bytes(), suffix)
    return resolved


def validate_data_uri(data_uri: str) -> str:
    """Validate a base64 image data: URI for MIME allowlist, size limit, and content safety (SEC-004)."""
    if not isinstance(data_uri, str):
        raise ValueError("Data URI must be a string")
    cleaned = data_uri.strip()
    m = _DATA_URI_RE.fullmatch(cleaned)
    if not m:
        raise ValueError("Unsupported or malformed data URI (only base64 png/jpeg/webp/svg+xml allowed)")

    mime_type = m.group(1).lower()
    b64_payload = re.sub(r"\s+", "", m.group(2))
    if len(b64_payload) > (MAX_ASSET_FILE_BYTES * 4 // 3) + 64:
        raise ValueError("Data URI payload exceeds maximum allowed size")

    try:
        raw_bytes = base64.b64decode(b64_payload, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 payload in data URI") from exc

    mime_to_ext = {
        "image/svg+xml": ".svg",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    _validate_image_bytes(raw_bytes, mime_to_ext[mime_type])
    return f"data:{mime_type};base64,{b64_payload}"


def _make_svg_stretchable(svg_bytes: bytes) -> bytes:
    """Ensure the root <svg> element stretches to fill any target canvas dimensions (preserveAspectRatio='none')."""
    try:
        svg_text = svg_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return svg_bytes

    def _patch_root_svg(match: re.Match[str]) -> str:
        tag = match.group(0)
        if re.search(r"\bpreserveAspectRatio\s*=", tag, re.IGNORECASE):
            tag = re.sub(
                r'\bpreserveAspectRatio\s*=\s*["\'][^"\']*["\']',
                'preserveAspectRatio="none"',
                tag,
                flags=re.IGNORECASE,
            )
        else:
            tag = tag[:-1] + ' preserveAspectRatio="none">'
        tag = re.sub(r'\bwidth\s*=\s*["\'][^"\']*["\']', 'width="100%"', tag, flags=re.IGNORECASE)
        tag = re.sub(r'\bheight\s*=\s*["\'][^"\']*["\']', 'height="100%"', tag, flags=re.IGNORECASE)
        return tag

    patched = re.sub(r"<svg\b[^>]*>", _patch_root_svg, svg_text, count=1, flags=re.IGNORECASE)
    return patched.encode("utf-8")


def _image_to_data_uri(
    source: Optional[Union[str, Path]],
    *,
    stretch_svg: bool = False,
) -> Optional[str]:
    """Safely convert an allowed asset image path (inside assets/) or validated data: URI to a Base64 Data URI."""
    if not source:
        return None
    if isinstance(source, str):
        src_str = source.strip()
        if not src_str:
            return None
        if src_str.lower().startswith("data:"):
            try:
                validated_uri = validate_data_uri(src_str)
                if stretch_svg and validated_uri.startswith("data:image/svg+xml;base64,"):
                    raw_svg = base64.b64decode(validated_uri.split(",", 1)[1])
                    stretched_b64 = base64.b64encode(_make_svg_stretchable(raw_svg)).decode("ascii")
                    return f"data:image/svg+xml;base64,{stretched_b64}"
                return validated_uri
            except ValueError as exc:
                logger.warning("Blocked unsafe or invalid data URI: %s", exc)
                return None
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", src_str) or src_str.startswith("//"):
            logger.warning("Blocked external URL in _image_to_data_uri: %s", src_str)
            return None

    try:
        path = validate_asset_path(source)
    except (ValueError, OSError) as exc:
        logger.warning("Blocked or invalid asset path '%s': %s", source, exc)
        return None

    suffix = path.suffix.lower()
    mime_types = {
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    mime_type = mime_types.get(suffix) or mimetypes.guess_type(path.name)[0] or "image/png"

    try:
        data = path.read_bytes()
        if stretch_svg and suffix == ".svg":
            data = _make_svg_stretchable(data)
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
    has_logo = bool(logo_asset_path and _image_to_data_uri(logo_asset_path) is not None)
    logo_obstacle = (
        _logo_obstacle_rect(canvas_width=canvas_width, canvas_height=canvas_height)
        if has_logo
        else None
    )

    for element in elements[:MAX_FOREGROUND_ELEMENTS]:
        item = dict(element)
        element_type = str(item.get("type") or "text")
        role = _safe_role(item.get("role")).lower()
        item["role"] = role
        if role == "logo_image":
            continue

        if item.get("text") is not None:
            item["text"] = _normalize_text_newlines(item["text"])[:1000]

        item["width"] = min(
            max(_int_value(item.get("width"), 180), 20),
            canvas_width - ELEMENT_MARGIN * 2,
        )
        item["font_size"] = min(max(_int_value(item.get("font_size"), 20), 10), 500)
        item["height"] = min(_estimated_text_height(item), canvas_height - ELEMENT_MARGIN * 2)
        item["x"] = max(
            ELEMENT_MARGIN,
            min(_int_value(item.get("x"), 24), canvas_width - item["width"] - ELEMENT_MARGIN),
        )
        item["y"] = max(
            ELEMENT_MARGIN,
            min(_int_value(item.get("y"), 24), canvas_height - item["height"] - ELEMENT_MARGIN),
        )
        item["font_weight"] = validate_css_font_weight(item.get("font_weight"), "700")
        item["align"] = validate_css_align(item.get("align"), "left")
        item["color"] = validate_css_color(item.get("color"), "#111827")
        item["background"] = validate_css_background(item.get("background"), "transparent")
        item["border_radius"] = min(max(_int_value(item.get("border_radius"), 0), 0), 2000)
        for opt_key in ("font_family", "text_shadow", "box_shadow", "border", "letter_spacing"):
            if opt_key in item:
                item[opt_key] = _optional_css_value(item.get(opt_key), key=opt_key)

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
    validate_canvas_dimensions(width, height)
    bg_style = validate_background_css(background_css, DEFAULT_BG_STYLE)
    bg_data_uri = _image_to_data_uri(background_asset_path, stretch_svg=True)
    if bg_data_uri:
        bg_element = (
            f'<image href="{bg_data_uri}" xlink:href="{bg_data_uri}" x="0" y="0" '
            f'width="{width}" height="{height}" preserveAspectRatio="none" />'
        )
    else:
        bg_element = (
            f'<foreignObject x="0" y="0" width="{width}" height="{height}">\n'
            f'      <div xmlns="http://www.w3.org/1999/xhtml" class="banner-background" style="width: 100%; height: 100%; {html.escape(bg_style)}"></div>\n'
            f'    </foreignObject>'
        )

    svg_elements: List[str] = [bg_element]

    logo_data_uri = _image_to_data_uri(logo_asset_path)
    if logo_data_uri:
        svg_elements.append(
            f'<image href="{logo_data_uri}" xlink:href="{logo_data_uri}" '
            f'x="{LOGO_LEFT}" y="{LOGO_TOP}" width="{width // 4}" height="{height // 5}" '
            f'preserveAspectRatio="xMidYMid meet" />'
        )

    for el in foreground_elements[:MAX_FOREGROUND_ELEMENTS]:
        el_type = str(el.get("type") or "text")
        role = _safe_role(el.get("role"))
        text = html.escape(_normalize_text_newlines(el.get("text") or el.get("label") or "")[:1000])
        x = _int_value(el.get("x"), 20)
        y = _int_value(el.get("y"), 20)
        w = _int_value(el.get("width"), 200)
        h = _int_value(el.get("height"), 40)
        fs = _int_value(el.get("font_size"), 18)
        color = validate_css_color(el.get("color"), "#ffffff")
        bg = validate_css_background(el.get("background"), "transparent")
        br = max(_int_value(el.get("border_radius"), 0), 0)
        fw = validate_css_font_weight(el.get("font_weight"), "bold")
        align = validate_css_align(el.get("align"), "left")
        dec_path = el.get("decorative_asset_path")

        justify = "center" if align == "center" else ("flex-end" if align == "right" else "flex-start")
        has_bg = bg.strip().lower() not in {"", "none", "transparent"}
        text_padding = " padding: 2px 8px;" if has_bg else ""
        extra_styles = _extra_element_styles(el)

        dec_data_uri = _image_to_data_uri(dec_path)
        if el_type == "decorative" and dec_data_uri:
            content = f'<img src="{dec_data_uri}" alt="Decoration" style="width: 100%; height: 100%; object-fit: contain;" />'
        elif el_type == "cta":
            content = f'<button style="width: 100%; height: 100%; border: none; background: inherit; color: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; letter-spacing: inherit; text-shadow: inherit; cursor: pointer; display: flex; align-items: center; justify-content: center; white-space: pre-line;">{text}</button>'
        else:
            content = f'<div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: {justify}; text-align: {align}; line-height: {CSS_LINE_HEIGHT}; white-space: pre-line;{text_padding}">{text}</div>'

        item_style = (
            f"width: 100%; height: 100%; font-size: {fs}px; color: {color}; background: {bg}; "
            f"border-radius: {br}px; font-weight: {fw}; box-sizing: border-box; overflow: hidden; "
            f"display: flex; align-items: center; justify-content: {justify}; text-align: {align};{extra_styles}"
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
  <title>{html.escape(objective[:2000])}</title>
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
    validate_canvas_dimensions(width, height)
    bg_style = validate_background_css(background_css, DEFAULT_BG_STYLE)
    bg_data_uri = _image_to_data_uri(background_asset_path, stretch_svg=True)
    if bg_data_uri:
        bg_style = (
            f"background-image: url('{bg_data_uri}'); "
            f"background-size: 100% 100%; background-position: center; background-repeat: no-repeat;"
        )

    elements_html: List[str] = []

    logo_data_uri = _image_to_data_uri(logo_asset_path)
    if logo_data_uri:
        elements_html.append(
            f'<div class="banner-logo" style="position: absolute; left: {LOGO_LEFT}px; top: {LOGO_TOP}px; max-width: {width // 4}px; max-height: {height // 5}px; z-index: 10;">'
            f'<img src="{logo_data_uri}" alt="Logo" style="max-width: 100%; max-height: 100%; object-fit: contain;" />'
            f'</div>'
        )

    for el in foreground_elements[:MAX_FOREGROUND_ELEMENTS]:
        el_type = str(el.get("type") or "text")
        role = _safe_role(el.get("role"))
        text = html.escape(_normalize_text_newlines(el.get("text") or el.get("label") or "")[:1000])
        x = _int_value(el.get("x"), 20)
        y = _int_value(el.get("y"), 20)
        w = _int_value(el.get("width"), 200)
        h = _int_value(el.get("height"), 40)
        fs = _int_value(el.get("font_size"), 18)
        color = validate_css_color(el.get("color"), "#ffffff")
        bg = validate_css_background(el.get("background"), "transparent")
        br = max(_int_value(el.get("border_radius"), 0), 0)
        fw = validate_css_font_weight(el.get("font_weight"), "bold")
        align = validate_css_align(el.get("align"), "left")
        dec_path = el.get("decorative_asset_path")

        justify = "center" if align == "center" else ("flex-end" if align == "right" else "flex-start")
        has_bg = bg.strip().lower() not in {"", "none", "transparent"}
        text_padding = " padding: 2px 8px;" if has_bg else ""
        extra_styles = _extra_element_styles(el)

        dec_data_uri = _image_to_data_uri(dec_path)
        if el_type == "decorative" and dec_data_uri:
            content = f'<img src="{dec_data_uri}" alt="Decoration" style="width: 100%; height: 100%; object-fit: contain;" />'
        elif el_type == "cta":
            content = f'<button style="width: 100%; height: 100%; border: none; background: inherit; color: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; letter-spacing: inherit; text-shadow: inherit; cursor: pointer; display: flex; align-items: center; justify-content: center; white-space: pre-line;">{text}</button>'
        else:
            content = f'<div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: {justify}; text-align: {align}; line-height: {CSS_LINE_HEIGHT}; white-space: pre-line;{text_padding}">{text}</div>'

        style = (
            f"position: absolute; left: {x}px; top: {y}px; width: {w}px; height: {h}px; "
            f"font-size: {fs}px; color: {color}; background: {bg}; border-radius: {br}px; "
            f"font-weight: {fw}; box-sizing: border-box; overflow: hidden; z-index: 5;{extra_styles}"
        )
        elements_html.append(f'<div class="banner-element role-{role}" style="{style}">{content}</div>')

    elements_str = "\n    ".join(elements_html)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width={width}, height={height}, initial-scale=1.0">
  <title>{html.escape(objective[:2000])}</title>
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
    validate_canvas_dimensions(width, height)
    safe_iteration = max(1, min(_int_value(iteration, 1), 100))
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
            "iteration": safe_iteration,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    if output_stem:
        dir_name = _safe_stem(output_stem)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        clean_theme = _safe_stem(objective)
        dir_name = f"{timestamp}_{clean_theme}"
    output_root = BANNER_OUTPUT_DIR.resolve()
    output_dir = (BANNER_OUTPUT_DIR / dir_name).resolve()
    if not output_dir.is_relative_to(output_root):
        raise ValueError(f"Output directory escapes BANNER_OUTPUT_DIR: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    html_path = output_dir / "banner.html"
    svg_path = output_dir / "banner.svg"
    png_path = output_dir / "banner.png"

    iter_html_path = output_dir / f"banner_iter{safe_iteration}.html"
    iter_svg_path = output_dir / f"banner_iter{safe_iteration}.svg"

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
    """Render PNG preview from existing banner HTML/SVG, automatically selecting the latest edited file and syncing to banner.html / banner.svg."""
    validate_canvas_dimensions(width, height)
    given_path = Path(html_path)
    if not given_path.exists():
        raise FileNotFoundError(f"Banner file not found: {given_path}")

    # Detect the most recently modified HTML or SVG file in the directory
    if auto_detect_latest:
        source_path = _find_latest_banner_source_file(given_path)
    else:
        source_path = given_path

    directory = source_path.parent
    main_html_path = directory / "banner.html"
    main_svg_path = directory / "banner.svg"
    main_png_path = directory / "banner.png"

    # Synchronize latest HTML to banner.html
    html_source: Optional[Path] = None
    if source_path.suffix.lower() == ".html":
        html_source = source_path
    elif (source_path.with_suffix(".html")).is_file():
        html_source = source_path.with_suffix(".html")
    else:
        html_candidates = [
            p for p in directory.glob("*.html") if p.is_file() and p.resolve() != main_html_path.resolve()
        ]
        if html_candidates:
            html_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            html_source = html_candidates[0]

    if html_source and html_source.is_file() and html_source.resolve() != main_html_path.resolve():
        try:
            shutil.copy2(html_source, main_html_path)
        except Exception as exc:
            logger.warning(f"Failed to sync {html_source} to {main_html_path}: {exc}")

    # Synchronize latest SVG to banner.svg
    svg_source: Optional[Path] = None
    if source_path.suffix.lower() == ".svg":
        svg_source = source_path
    elif (source_path.with_suffix(".svg")).is_file():
        svg_source = source_path.with_suffix(".svg")
    else:
        svg_candidates = [
            p for p in directory.glob("*.svg") if p.is_file() and p.resolve() != main_svg_path.resolve()
        ]
        if svg_candidates:
            svg_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            svg_source = svg_candidates[0]

    if svg_source and svg_source.is_file() and svg_source.resolve() != main_svg_path.resolve():
        try:
            shutil.copy2(svg_source, main_svg_path)
        except Exception as exc:
            logger.warning(f"Failed to sync {svg_source} to {main_svg_path}: {exc}")

    output_png_path = (
        Path(png_path) if png_path is not None else source_path.with_suffix(".png")
    )

    rendered_png: Optional[str] = None
    notes = f"Loaded banner file: {source_path.name}"

    if enable_playwright:
        success = _render_file_to_png(source_path, output_png_path, width=width, height=height)
        if success:
            rendered_png = str(output_png_path)
            # Sync to main banner.png as well
            if output_png_path.resolve() != main_png_path.resolve():
                try:
                    shutil.copy2(output_png_path, main_png_path)
                except Exception:
                    pass
            notes = f"Rendered PNG preview from latest modified {source_path.suffix.upper()} ({source_path.name}) via Playwright."
        else:
            notes = "Playwright rendering failed or browser was unavailable."
    else:
        notes = "Playwright rendering is disabled."

    # Identify companion HTML and SVG files for return dict
    html_target = main_html_path if main_html_path.is_file() else source_path
    svg_target = main_svg_path if main_svg_path.is_file() else (source_path if source_path.suffix.lower() == ".svg" else None)

    return {
        "source_file": str(source_path),
        "source_type": source_path.suffix.lstrip(".").lower(),
        "html_path": str(html_target),
        "svg_path": str(svg_target) if svg_target else None,
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
