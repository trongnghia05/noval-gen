"""Generate poster art for a finished novel — a wide cover + two portrait
thumbnails — with Vertex Imagen, then overlay the title with Pillow.

Runs best-effort at story completion: any failure (Imagen not enabled, safety
filter, provider without image support) is logged and skipped so the novel export
is never blocked. Images land next to novel.md in the host-mounted output folder.
"""

import io
import logging

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy.orm import Session

from .config import AGENT_MODELS, IMAGE_MODEL, PROVIDER
from .db.models import Character, Story
from .llm_json import generate_structured
from .prompts.loader import load_prompt
from .schemas import ImagePromptSetOut, NovelMetadataOut

logger = logging.getLogger(__name__)

_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# (filename, width, height, Imagen aspect ratio, ImagePromptSetOut attribute)
_SPECS = [
    ("cover.png",      343, 212, "16:9", "cover"),
    ("thumbnail1.png", 109, 154, "3:4",  "thumb1"),
    ("thumbnail2.png", 166, 214, "3:4",  "thumb2"),
]

_TIER_ORDER = {"core": 0, "important": 1, "secondary": 2, "minor": 3}


def _character_lines(session: Session, story_id: int) -> str:
    chars = (
        session.query(Character)
        .filter_by(story_id=story_id)
        .all()
    )
    chars.sort(key=lambda c: _TIER_ORDER.get(c.tier or "minor", 9))
    lines = []
    for c in chars[:8]:
        prof = (c.profile_md or "").replace("\n", " ")[:220]
        lines.append(f"- {c.name} [{c.tier or 'minor'}]: {prof}")
    return "\n".join(lines)


def _build_prompts(session: Session, story: Story, meta: NovelMetadataOut | None) -> ImagePromptSetOut:
    tags = ", ".join(meta.tags) if (meta and meta.tags) else (story.genre or "")
    system = load_prompt("image_prompt")
    user_content = (
        f"title: {story.title}\n"
        f"tags: {tags}\n\n"
        f"## world (setting / genre / tone)\n{(story.story_bible or story.world_bible or '')[:3000]}\n\n"
        f"## MAIN CHARACTERS\n{_character_lines(session, story.id)}\n"
    )
    return generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS.get("image_prompt", AGENT_MODELS["quality_reviewer"]),
        schema=ImagePromptSetOut, max_tokens=2048, thinking=False,
    )


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(_FONT_BOLD, size)
    except Exception:
        return ImageFont.load_default()


def _wrap(text: str, font, draw: ImageDraw.ImageDraw, max_w: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if not cur or draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _overlay_title(img: Image.Image, title: str) -> None:
    """Bottom scrim + centered bold wrapped title, sized to the image."""
    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")

    # Darken the bottom band for legibility (transparent → opaque toward the edge).
    scrim_h = max(1, int(H * 0.45))
    grad = Image.new("L", (1, scrim_h))
    for y in range(scrim_h):
        grad.putpixel((0, y), int(210 * (y / scrim_h)))
    grad = grad.resize((W, scrim_h))
    scrim = Image.new("RGBA", (W, scrim_h), (0, 0, 0, 255))
    scrim.putalpha(grad)
    img.paste(scrim, (0, H - scrim_h), scrim)

    text = (title or "").upper()
    max_w = W * 0.90
    size = max(11, int(W / 8))
    lines: list[str] = [text]
    while size >= 10:
        font = _load_font(size)
        lines = _wrap(text, font, draw, max_w)
        line_h = size + max(2, size // 6)
        fits_w = all(draw.textlength(l, font=font) <= max_w for l in lines)
        if fits_w and len(lines) * line_h <= H * 0.40:
            break
        size -= 1
    font = _load_font(size)
    line_h = size + max(2, size // 6)
    total_h = len(lines) * line_h
    y = H - total_h - max(4, int(H * 0.05))
    stroke = max(1, size // 14)
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text(
            ((W - w) / 2, y), line, font=font,
            fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 235),
        )
        y += line_h


def generate(session: Session, story: Story, out_dir, meta: NovelMetadataOut | None = None) -> list[str]:
    """Generate cover + 2 thumbnails into out_dir. Best-effort; returns filenames written."""
    try:
        prompts = _build_prompts(session, story, meta)
    except Exception as exc:
        logger.warning("[%s] image prompt design failed, skipping images: %s", story.slug, exc)
        return []

    written: list[str] = []
    for filename, w, h, aspect, attr in _SPECS:
        prompt = getattr(prompts, attr, "") or ""
        if not prompt:
            continue
        try:
            raw = PROVIDER.generate_image(prompt=prompt, model=IMAGE_MODEL, aspect_ratio=aspect)
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            # Cover to target box then center-crop (faces sit slightly above center).
            img = ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=(0.5, 0.4))
            _overlay_title(img, story.title)
            path = out_dir / filename
            img.save(path, format="PNG")
            written.append(filename)
            logger.info("[%s] image written: %s (%dx%d)", story.slug, filename, w, h)
        except NotImplementedError:
            logger.warning("[%s] provider has no image support — skipping all images", story.slug)
            break
        except Exception as exc:
            logger.warning("[%s] image %s failed: %s", story.slug, filename, exc)
            continue
    return written
