"""Artifact-related Pydantic schemas.

Corresponds to ArtifactType, ArtifactContent types from src/shared/types.ts
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# ─── Artifact Types ─────────────────────────────────────
ArtifactType = Literal[
    "web_app", "code_file", "diff", "document", "image", "ppt", "diagram", "project"
]
WritableArtifactType = Literal["web_app", "document", "image", "ppt", "diagram"]
DispatchExpectedOutputType = Literal[
    "web_app", "document", "image", "ppt", "diagram", "project"
]


# ─── DiffHunk ─────────────────────────────────────
class DiffHunk(BaseModel):
    """Diff hunk for code_file artifacts."""

    old_start: int = Field(alias="oldStart")
    old_lines: int = Field(alias="oldLines")
    new_start: int = Field(alias="newStart")
    new_lines: int = Field(alias="newLines")
    lines: list[str]

    model_config = {"populate_by_name": True}


# ─── PPT Types ─────────────────────────────────────
MermaidTheme = Literal["default", "base", "dark", "forest", "neutral"]
PptLayout = Literal[
    "title",
    "title-bullets",
    "section",
    "blank",
    "content",
    "two-column",
    "metrics",
    "timeline",
    "quote",
]
PptTone = Literal["neutral", "positive", "negative", "info", "warning"]


class PptTheme(BaseModel):
    """PPT theme configuration."""

    primary: str | None = None
    background: str | None = None
    surface: str | None = None
    text_body: str | None = Field(default=None, alias="textBody")
    text_muted: str | None = Field(default=None, alias="textMuted")
    accent_positive: str | None = Field(default=None, alias="accentPositive")
    accent_negative: str | None = Field(default=None, alias="accentNegative")
    divider: str | None = None
    font_heading: str | None = Field(default=None, alias="fontHeading")
    font_body: str | None = Field(default=None, alias="fontBody")
    # Deprecated fields
    primary_color: str | None = Field(default=None, alias="primaryColor")
    font_face: str | None = Field(default=None, alias="fontFace")

    model_config = {"populate_by_name": True}


class PptTimelineItem(BaseModel):
    """PPT timeline item."""

    label: str
    title: str | None = None
    text: str | None = None


class PptColumnBlock(BaseModel):
    """PPT column block."""

    type: Literal["paragraph", "bullets", "metric", "callout"]
    text: str | None = None
    items: list[str] | None = None
    ordered: bool | None = None
    label: str | None = None
    value: str | None = None
    change: str | None = None
    tone: PptTone | None = None
    title: str | None = None


class PptColumn(BaseModel):
    """PPT column."""

    title: str | None = None
    blocks: list[PptColumnBlock] | None = None


class PptBlock(BaseModel):
    """PPT content block."""

    type: Literal[
        "heading",
        "paragraph",
        "bullets",
        "metric",
        "quote",
        "timeline",
        "columns",
        "callout",
        "divider",
        "spacer",
    ]
    text: str | None = None
    level: Literal[1, 2] | None = None
    items: list[str] | None = None
    ordered: bool | None = None
    label: str | None = None
    value: str | None = None
    change: str | None = None
    tone: PptTone | None = None
    attribution: str | None = None
    timeline_items: list[PptTimelineItem] | None = Field(default=None, alias="items")
    columns: list[PptColumn] | None = None
    title: str | None = None
    size: Literal["sm", "md", "lg"] | None = None


class PptSlide(BaseModel):
    """PPT slide."""

    title: str | None = None
    subtitle: str | None = None
    bullets: list[str] | None = None
    blocks: list[PptBlock] | None = None
    notes: str | None = None
    layout: PptLayout | None = None


class ProjectFile(BaseModel):
    """Project file entry."""

    path: str
    size_bytes: int = Field(alias="sizeBytes")

    model_config = {"populate_by_name": True}


# ─── ArtifactContent Union Types ─────────────────────────────────
class WebAppContent(BaseModel):
    """Web app artifact content."""

    type: Literal["web_app"] = "web_app"
    files: dict[str, str]
    entry: str


class CodeFileContent(BaseModel):
    """Code file artifact content."""

    type: Literal["code_file"] = "code_file"
    workspace_path: str = Field(alias="workspacePath")
    language: str
    size_bytes: int = Field(alias="sizeBytes")
    checksum: str

    model_config = {"populate_by_name": True}


class DiffContent(BaseModel):
    """Diff artifact content."""

    type: Literal["diff"] = "diff"
    target_artifact_id: str = Field(alias="targetArtifactId")
    hunks: list[DiffHunk]
    applied: bool

    model_config = {"populate_by_name": True}


class DocumentContent(BaseModel):
    """Document artifact content."""

    type: Literal["document"] = "document"
    format: Literal["markdown"] = "markdown"
    content: str


class ImageContent(BaseModel):
    """Image artifact content."""

    type: Literal["image"] = "image"
    url: str
    alt: str
    width: int | None = None
    height: int | None = None


class DiagramContent(BaseModel):
    """Diagram artifact content."""

    type: Literal["diagram"] = "diagram"
    syntax: Literal["mermaid"] = "mermaid"
    source: str
    theme: MermaidTheme | None = None


class PptContent(BaseModel):
    """PPT artifact content."""

    type: Literal["ppt"] = "ppt"
    title: str | None = None
    theme: PptTheme | None = None
    slides: list[PptSlide]


class ProjectContent(BaseModel):
    """Project artifact content."""

    type: Literal["project"] = "project"
    files: list[ProjectFile]
    task_id: str | None = Field(default=None, alias="taskId")
    agent_id: str | None = Field(default=None, alias="agentId")

    model_config = {"populate_by_name": True}


# Union type for all artifact content types
ArtifactContent = Annotated[
    WebAppContent | CodeFileContent | DiffContent | DocumentContent | ImageContent | DiagramContent | PptContent | ProjectContent,
    Field(discriminator="type"),
]


# ─── Artifact Record ─────────────────────────────────────
class ArtifactRecord(BaseModel):
    """Artifact record for events."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    type: ArtifactType
    title: str
    content: dict  # Will be parsed to specific content type
    version: int
    parent_artifact_id: str | None = Field(default=None, alias="parentArtifactId")
    created_by_agent_id: str = Field(alias="createdByAgentId")
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}
