"""Generate poster art for a finished novel — a wide cover + two portrait
thumbnails — with Vertex Imagen, then overlay the title with Pillow.

Runs best-effort at story completion: any failure (Imagen not enabled, safety
filter, provider without image support) is logged and skipped so the novel export
is never blocked. Images land next to novel.md in the host-mounted output folder.
"""

import io
import logging

from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from .config import AGENT_MODELS, IMAGE_MODEL, PROVIDER
from .db.models import Character, Story
from .llm_json import generate_structured
from .prompts.loader import load_prompt
from .schemas import ImagePromptSetOut, NovelMetadataOut

logger = logging.getLogger(__name__)

# (filename, width, height, aspect-ratio, attr, crop-centering)
# The model now outputs each aspect ratio natively (image_config), so cropping to
# the exact pixel size is minimal and symmetric — faces and title both survive.
_SPECS = [
    ("cover.png",      343, 212, "16:9", "cover",  (0.5, 0.5)),
    ("thumbnail1.png", 109, 154, "3:4",  "thumb1", (0.5, 0.5)),
    ("thumbnail2.png", 166, 214, "3:4",  "thumb2", (0.5, 0.5)),
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
        f"title (render EXACTLY this text on each image): {story.title}\n"
        f"tags: {tags}\n\n"
        f"## world (setting / genre / tone)\n{(story.story_bible or story.world_bible or '')[:3000]}\n\n"
        f"## MAIN CHARACTERS\n{_character_lines(session, story.id)}\n"
    )
    return generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS.get("image_prompt", AGENT_MODELS["quality_reviewer"]),
        schema=ImagePromptSetOut, max_tokens=2048, thinking=False,
    )


def generate(session: Session, story: Story, out_dir, meta: NovelMetadataOut | None = None) -> list[str]:
    """Generate cover + 2 thumbnails into out_dir. Best-effort; returns filenames written."""
    try:
        prompts = _build_prompts(session, story, meta)
    except Exception as exc:
        logger.warning("[%s] image prompt design failed, skipping images: %s", story.slug, exc)
        return []

    # _SPECS is ordered cover-first on purpose: the cover fixes each character's
    # face, then its raw bytes are fed as a reference into the thumbnails so the
    # SAME people appear consistently (Nano Banana keeps identity from a reference
    # image even when pose/wardrobe/framing changes).
    _CONSISTENCY = (
        "\n\nIMPORTANT: a reference image of these characters is provided. Keep the "
        "SAME people — identical faces, hair, skin tone and identity as in the "
        "reference. You may change their pose, framing, expression and wardrobe to "
        "fit this new composition, but each returning character must be instantly "
        "recognizable as the same person from the reference."
    )
    written: list[str] = []
    cover_ref: bytes | None = None
    for filename, w, h, aspect, attr, centering in _SPECS:
        prompt = getattr(prompts, attr, "") or ""
        if not prompt:
            continue
        is_cover = attr == "cover"
        refs = None if (is_cover or not cover_ref) else [cover_ref]
        full_prompt = prompt if is_cover else prompt + _CONSISTENCY
        # Gemini image gen occasionally returns a text-only response (or trips a
        # transient safety block) — retry a couple times before giving up.
        for attempt in range(3):
            try:
                raw = PROVIDER.generate_image(
                    prompt=full_prompt, model=IMAGE_MODEL,
                    aspect_ratio=aspect, reference_images=refs,
                )
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                # Cover the target box then symmetric center-crop — the model draws
                # the title in the lower third within safe margins, so we must NOT
                # bias the crop toward the bottom or it could clip the title.
                img = ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=centering)
                img.save(out_dir / filename, format="PNG")
                if is_cover:
                    cover_ref = raw  # full-res reference for the thumbnails
                written.append(filename)
                logger.info("[%s] image written: %s (%dx%d)", story.slug, filename, w, h)
                break
            except NotImplementedError:
                logger.warning("[%s] provider has no image support — skipping all images", story.slug)
                return written
            except Exception as exc:
                logger.warning("[%s] image %s attempt %d failed: %s",
                               story.slug, filename, attempt + 1, exc)
    return written
