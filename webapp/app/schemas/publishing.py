"""Schemas for what ships with a finished book: front matter and the poster prompts."""
from pydantic import BaseModel, Field

# ── novel metadata (front matter for the exported file) ─────────────────────────

class CharacterBlurbOut(BaseModel):
    """One cast entry for the end of summarize.txt."""
    name: str = Field(description="copied exactly from the cast list given in the prompt")
    role: str = Field(description="protagonist | love_interest | antagonist | supporting")
    blurb: str = Field(description="2-3 sentences: what this character DOES in the plot")


class NovelMetadataOut(BaseModel):
    author: str = Field(description="a fitting pen name (invented), in the story's language/culture")
    tags: list[str] = Field(default=[], description='3-6 genre/theme tags, e.g. ["Dark Fantasy", "Gothic Romance"]')
    logline: str = Field(description="the plot — a 1-2 sentence premise/hook")
    summary: str = Field(description="back-cover blurb, 120-180 words, no ending spoilers")
    characters: list[CharacterBlurbOut] = Field(default=[], description="main cast, minor walk-ons excluded")


class ImagePromptIssueOut(BaseModel):
    """One fault found in a drafted poster prompt, phrased so it can be fixed."""
    image: str = Field(description="cover | thumb1 | thumb2 | all")
    check: str = Field(description="era | protagonist | age | dynamic | title | wardrobe | variety | colour | cast — the rule that was broken")
    description: str = Field(description="where the prompt is currently wrong — quote it")
    fix: str = Field(description="the specific fix")


class ImagePromptVerifyOut(BaseModel):
    issues: list[ImagePromptIssueOut] = Field(default=[], description="empty ⇒ passes")
    verdict_note: str = Field("", description="a short verdict")


class ImagePromptSetOut(BaseModel):
    """Three text-to-image prompts (English) for the poster art. Each describes a
    cinematic, photorealistic drama-poster composition in the STORY'S world, with
    NO text/letters/watermarks (the title is overlaid separately by Pillow)."""
    cover: str = Field(description="wide: a montage of the main characters in the story's world")
    thumb1: str = Field(description="portrait: the lead (optionally with one supporting character)")
    thumb2: str = Field(description="portrait: the central pair / the pivotal relationship")


