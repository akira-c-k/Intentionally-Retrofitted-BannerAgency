from __future__ import annotations

from typing import Any, List, Literal, Optional, Union
from pydantic import BaseModel, Field

BannerElementRole = Literal[
    "logo_text",
    "main_copy",
    "body_copy",
    "note",
    "cta",
    "decorative",
]

BannerAssetKind = Literal["background", "logo", "decoration"]


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


class BannerRequirements(BaseModel):
    purpose: str = Field(description="Primary advertising purpose.")
    audience: str = Field(description="Target audience.")
    mood: str = Field(description="Desired mood.")
    tone: str = Field(description="Copy and visual tone.")
    copy_requirements: List[str] = Field(description="Required text or message constraints.")
    width: int = Field(description="Banner width in pixels.")
    height: int = Field(description="Banner height in pixels.")
    notes: str = Field(description="Short strategy notes.")


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
        description=(
            "Semantic foreground role: logo_text, main_copy, body_copy, note, cta, "
            "or decorative. Logo images stay in selected_logo_asset_id/logo_asset_path."
        ),
    )
    text: Optional[str] = Field(default=None, description="Element text.")
    decorative_asset_path: Optional[str] = Field(
        default=None,
        description="Selected decorative image asset path for decorative elements, if any.",
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


class BannerForegroundPlan(BaseModel):
    selected_logo_asset_id: Optional[str] = Field(
        default=None, description="Selected logo asset ID, if any."
    )
    logo_asset_path: Optional[str] = Field(
        default=None, description="Selected logo asset path, if any."
    )
    elements: List[BannerForegroundElement] = Field(
        default_factory=list,
        description=(
            "Foreground element JSON list. Each item should include type, role, x, y, "
            "width, height, and text when applicable."
        ),
    )
    rationale: str = Field(description="Why this layout supports the requirements.")
    notes: str = Field(description="Short foreground design notes.")


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
