from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional, Union
from pydantic import BaseModel, Field, WithJsonSchema, model_validator

from src.config import (
    BACKGROUND_ASSET_DIR,
    DECORATION_ASSET_DIR,
    IMAGE_EXTENSIONS,
    LOGO_ASSET_DIR,
    MAX_FOREGROUND_ELEMENTS,
    MAX_HEIGHT,
    MAX_PIXELS,
    MAX_WIDTH,
)

BannerElementRole = Literal[
    "logo_text",
    "main_copy",
    "body_copy",
    "note",
    "cta",
    "decorative",
]

BannerAssetKind = Literal["background", "logo", "decoration"]

# Schema-light optional string: emits {"type": "string"} in JSON Schema to avoid
# anyOf union state explosion in LLM structured outputs while remaining Optional[str] in Python.
OptionalStyleStr = Annotated[Optional[str], WithJsonSchema({"type": "string"})]


def _normalize_multiline_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("¥n", "\n")


def _resolve_known_asset_path(asset_id_or_path: Optional[str], asset_dir: Path) -> Optional[str]:
    if not asset_id_or_path or not isinstance(asset_id_or_path, str):
        return None
    cleaned = asset_id_or_path.strip()
    if not cleaned or cleaned.lower() in {"null", "none"}:
        return None
    stem = Path(cleaned).stem
    for ext in IMAGE_EXTENSIONS:
        candidate = asset_dir / f"{stem}{ext}"
        if candidate.is_file():
            return str(candidate)
    return None


class BannerInput(BaseModel):
    objective: str = Field(description="Advertising banner objective from the user.")
    width: int = Field(default=300, description="Banner width in pixels.")
    height: int = Field(default=250, description="Banner height in pixels.")
    notes: Optional[str] = Field(default=None, description="Optional user notes or constraints.")
    banner_id: str = Field(default="banner", description="Stable banner run identifier.")
    iteration: int = Field(default=1, description="Current iteration number.")
    reviewer_feedback: Optional[str] = Field(default=None, description="Latest human review comment.")
    previous_summary: Optional[str] = Field(
        default=None,
        description="Summary from previous iterations.",
    )

    @model_validator(mode="after")
    def _validate_bounds(self) -> BannerInput:
        if not self.objective or not self.objective.strip() or len(self.objective) > 2000:
            raise ValueError("Invalid objective length (must be 1..2000 chars)")
        if not (1 <= self.width <= MAX_WIDTH):
            raise ValueError(f"Invalid width: {self.width} (must be 1..{MAX_WIDTH})")
        if not (1 <= self.height <= MAX_HEIGHT):
            raise ValueError(f"Invalid height: {self.height} (must be 1..{MAX_HEIGHT})")
        if self.width * self.height > MAX_PIXELS:
            raise ValueError(f"Canvas size {self.width}x{self.height} exceeds limit of {MAX_PIXELS} pixels")
        if self.notes is not None and len(self.notes) > 4000:
            raise ValueError("Notes exceed maximum length of 4000 chars")
        if not (1 <= self.iteration <= 100):
            raise ValueError("Iteration must be between 1 and 100")
        return self


class BannerRequirements(BaseModel):
    purpose: str = Field(description="Primary advertising purpose.")
    audience: str = Field(description="Target audience.")
    mood: str = Field(description="Desired mood.")
    tone: str = Field(description="Copy and visual tone.")
    copy_requirements: List[str] = Field(description="Required text or message constraints.")
    width: int = Field(description="Banner width in pixels.")
    height: int = Field(description="Banner height in pixels.")
    notes: str = Field(description="Short strategy notes.")

    @model_validator(mode="after")
    def _validate_bounds(self) -> BannerRequirements:
        if not self.purpose or not self.purpose.strip() or len(self.purpose) > 2000:
            raise ValueError("Invalid purpose length (must be 1..2000 chars)")
        if not self.audience or not self.audience.strip() or len(self.audience) > 1000:
            raise ValueError("Invalid audience length")
        if not self.mood or not self.mood.strip() or len(self.mood) > 500:
            raise ValueError("Invalid mood length")
        if not self.tone or not self.tone.strip() or len(self.tone) > 500:
            raise ValueError("Invalid tone length")
        if not (1 <= self.width <= MAX_WIDTH):
            raise ValueError(f"Invalid width: {self.width} (must be 1..{MAX_WIDTH})")
        if not (1 <= self.height <= MAX_HEIGHT):
            raise ValueError(f"Invalid height: {self.height} (must be 1..{MAX_HEIGHT})")
        if self.width * self.height > MAX_PIXELS:
            raise ValueError(f"Canvas size {self.width}x{self.height} exceeds limit of {MAX_PIXELS} pixels")
        if len(self.notes) > 4000:
            raise ValueError("Notes exceed maximum length of 4000 chars")
        return self


class BannerBackgroundInput(BaseModel):
    requirements: BannerRequirements = Field(description="Strategist requirements.")
    banner_id: str = Field(description="Stable banner run identifier.")
    iteration: int = Field(default=1, description="Current iteration number.")
    reviewer_feedback: Optional[str] = Field(default=None, description="Latest human review comment.")
    previous_summary: Optional[str] = Field(
        default=None,
        description="Summary from previous iterations.",
    )


class BannerBackgroundSelection(BaseModel):
    selected_asset_id: Optional[str] = Field(default=None, description="Selected background asset ID, if any.")
    asset_path: Optional[str] = Field(default=None, description="Selected background asset path, if any.")
    metadata: str = Field(description="Selected background metadata summary or JSON string.")
    background_css: str = Field(description="CSS background fallback or supporting style.")
    rationale: str = Field(description="Why this background suits the requirements.")
    notes: str = Field(description="Short background selection notes.")

    @model_validator(mode="after")
    def _resolve_background_asset(self) -> BannerBackgroundSelection:
        if self.selected_asset_id and (not self.asset_path or not self.asset_path.strip()):
            resolved = _resolve_known_asset_path(self.selected_asset_id, BACKGROUND_ASSET_DIR)
            if resolved:
                self.asset_path = resolved
        return self


class BannerForegroundInput(BaseModel):
    requirements: BannerRequirements = Field(description="Strategist requirements.")
    background: BannerBackgroundSelection = Field(description="Background selection.")
    banner_id: str = Field(description="Stable banner run identifier.")
    iteration: int = Field(default=1, description="Current iteration number.")
    reviewer_feedback: Optional[str] = Field(default=None, description="Latest human review comment.")
    previous_summary: Optional[str] = Field(
        default=None,
        description="Summary from previous iterations.",
    )


class BannerForegroundElement(BaseModel):
    type: Literal["text", "cta", "decorative"] = Field(description="Foreground element type.")
    role: Optional[BannerElementRole] = Field(
        default=None,
        description="Semantic foreground role.",
    )
    text: Optional[str] = Field(default=None, description="Element text.")
    decorative_asset_path: Optional[str] = Field(
        default=None,
        description="Decorative asset path, if any.",
    )
    x: int = Field(description="Left position in pixels.")
    y: int = Field(description="Top position in pixels.")
    width: int = Field(description="Element width in pixels.")
    height: int = Field(description="Element height in pixels.")
    font_size: int = Field(default=20, description="Font size in pixels.")
    color: str = Field(default="#111827", description="CSS text color.")
    background: str = Field(default="transparent", description="CSS background value.")
    border_radius: int = Field(default=0, description="Border radius in pixels.")
    font_weight: str = Field(default="700", description="CSS font weight.")
    align: Literal["left", "center", "right"] = Field(
        default="left",
        description="Text alignment.",
    )
    font_family: OptionalStyleStr = Field(default=None, description="Optional CSS font-family.")
    text_shadow: OptionalStyleStr = Field(default=None, description="Optional CSS text-shadow.")
    box_shadow: OptionalStyleStr = Field(default=None, description="Optional CSS box-shadow.")
    border: OptionalStyleStr = Field(default=None, description="Optional CSS border.")
    letter_spacing: OptionalStyleStr = Field(default=None, description="Optional CSS letter-spacing.")

    @model_validator(mode="after")
    def _validate_element(self) -> BannerForegroundElement:
        if not (1 <= self.width <= MAX_WIDTH):
            raise ValueError(f"Invalid element width: {self.width}")
        if not (1 <= self.height <= MAX_HEIGHT):
            raise ValueError(f"Invalid element height: {self.height}")
        if not (1 <= self.font_size <= 500):
            raise ValueError(f"Invalid font_size: {self.font_size}")
        if not (0 <= self.border_radius <= 2000):
            raise ValueError(f"Invalid border_radius: {self.border_radius}")
        self.text = _normalize_multiline_text(self.text)
        if self.decorative_asset_path and not Path(self.decorative_asset_path).is_file():
            resolved_dec = _resolve_known_asset_path(self.decorative_asset_path, DECORATION_ASSET_DIR)
            if resolved_dec:
                self.decorative_asset_path = resolved_dec
        for attr in ("font_family", "text_shadow", "box_shadow", "border", "letter_spacing"):
            val = getattr(self, attr)
            if isinstance(val, str) and not val.strip():
                setattr(self, attr, None)
        return self


class BannerForegroundPlan(BaseModel):
    selected_logo_asset_id: Optional[str] = Field(
        default=None, description="Selected logo asset ID, if any."
    )
    logo_asset_path: Optional[str] = Field(
        default=None, description="Selected logo asset path, if any."
    )
    elements: List[BannerForegroundElement] = Field(
        default_factory=list,
        description="Foreground element list.",
    )
    rationale: str = Field(description="Why this layout supports the requirements.")
    notes: str = Field(description="Short foreground design notes.")

    @model_validator(mode="after")
    def _validate_plan(self) -> BannerForegroundPlan:
        if len(self.elements) > MAX_FOREGROUND_ELEMENTS:
            raise ValueError(f"Too many foreground elements (max {MAX_FOREGROUND_ELEMENTS})")
        if self.selected_logo_asset_id and (not self.logo_asset_path or not self.logo_asset_path.strip()):
            resolved_logo = _resolve_known_asset_path(self.selected_logo_asset_id, LOGO_ASSET_DIR)
            if resolved_logo:
                self.logo_asset_path = resolved_logo
        return self


class BannerForegroundRefinementInput(BaseModel):
    requirements: BannerRequirements = Field(description="Strategist requirements.")
    background: BannerBackgroundSelection = Field(description="Background selection.")
    foreground_draft: BannerForegroundPlan = Field(description="Draft foreground layout plan.")
    banner_id: str = Field(description="Stable banner run identifier.")
    iteration: int = Field(default=1, description="Current iteration number.")
    reviewer_feedback: Optional[str] = Field(default=None, description="Latest human review comment.")
    previous_summary: Optional[str] = Field(
        default=None,
        description="Summary from previous iterations.",
    )


class BannerDeveloperInput(BaseModel):
    objective: str = Field(description="Advertising banner objective from the user.")
    requirements: BannerRequirements = Field(description="Strategist requirements.")
    background: BannerBackgroundSelection = Field(description="Background selection.")
    foreground: BannerForegroundPlan = Field(description="Foreground layout plan.")
    banner_id: str = Field(description="Stable banner run identifier.")
    iteration: int = Field(default=1, description="Current iteration number.")
    reviewer_feedback: Optional[str] = Field(default=None, description="Latest human review comment.")
    previous_summary: Optional[str] = Field(
        default=None,
        description="Summary from previous iterations and previous rendered HTML.",
    )
    previous_html_path: Optional[str] = Field(
        default=None,
        description="Previous iteration rendered HTML path, when available.",
    )
    previous_html: Optional[str] = Field(
        default=None,
        description="Previous iteration rendered HTML content, when available.",
    )


class BannerDeveloperOutput(BaseModel):
    html_path: str = Field(description="Rendered editable HTML path.")
    png_path: Optional[str] = Field(default=None, description="Rendered PNG preview path (if rendered).")
    svg_path: Optional[str] = Field(default=None, description="Optional SVG path.")
    notes: str = Field(description="Short implementation notes.")


class BannerCreationOutput(BaseModel):
    requirements: BannerRequirements = Field(description="Strategist requirements.")
    background: BannerBackgroundSelection = Field(description="Background selection.")
    foreground: BannerForegroundPlan = Field(description="Foreground layout plan.")
    developer: BannerDeveloperOutput = Field(description="Rendered banner output.")
    iteration: int = Field(default=1, description="Current iteration number.")
    notes: str = Field(description="Short orchestration notes.")


class BannerRenderElement(BaseModel):
    type: str = Field(description="Element type: text, cta, or decorative.")
    role: Optional[str] = Field(default=None, description="Semantic foreground role.")
    text: Optional[str] = Field(default=None, description="Element text.")
    label: Optional[str] = Field(default=None, description="Optional element label fallback.")
    decorative_asset_path: Optional[str] = Field(
        default=None,
        description="Optional image path for decorative foreground elements.",
    )
    x: int = Field(default=24, description="Left position in pixels.")
    y: int = Field(default=24, description="Top position in pixels.")
    width: int = Field(default=180, description="Element width in pixels.")
    height: int = Field(default=40, description="Element height in pixels.")
    font_size: int = Field(default=20, description="Font size in pixels.")
    color: str = Field(default="#111827", description="CSS text color.")
    background: str = Field(default="transparent", description="CSS background value.")
    border_radius: int = Field(default=0, description="Border radius in pixels.")
    font_weight: str = Field(default="700", description="CSS font weight.")
    align: str = Field(default="left", description="CSS text-align value.")
    font_family: OptionalStyleStr = Field(default=None, description="Optional CSS font-family.")
    text_shadow: OptionalStyleStr = Field(default=None, description="Optional CSS text-shadow.")
    box_shadow: OptionalStyleStr = Field(default=None, description="Optional CSS box-shadow.")
    border: OptionalStyleStr = Field(default=None, description="Optional CSS border.")
    letter_spacing: OptionalStyleStr = Field(default=None, description="Optional CSS letter-spacing.")

    @model_validator(mode="after")
    def _validate_render_element(self) -> BannerRenderElement:
        if not (1 <= self.width <= MAX_WIDTH):
            raise ValueError(f"Invalid element width: {self.width}")
        if not (1 <= self.height <= MAX_HEIGHT):
            raise ValueError(f"Invalid element height: {self.height}")
        if not (1 <= self.font_size <= 500):
            raise ValueError(f"Invalid font_size: {self.font_size}")
        if not (0 <= self.border_radius <= 2000):
            raise ValueError(f"Invalid border_radius: {self.border_radius}")
        self.text = _normalize_multiline_text(self.text)
        self.label = _normalize_multiline_text(self.label)
        if self.decorative_asset_path and not Path(self.decorative_asset_path).is_file():
            resolved_dec = _resolve_known_asset_path(self.decorative_asset_path, DECORATION_ASSET_DIR)
            if resolved_dec:
                self.decorative_asset_path = resolved_dec
        for attr in ("font_family", "text_shadow", "box_shadow", "border", "letter_spacing"):
            val = getattr(self, attr)
            if isinstance(val, str) and not val.strip():
                setattr(self, attr, None)
        return self
