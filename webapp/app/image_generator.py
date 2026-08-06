"""Generate poster art for a finished novel — a wide cover + two portrait
thumbnails — with a Gemini image model, with the title rendered by the model and
OCR-verified for correct spelling.

Runs best-effort at story completion: any failure (image gen not enabled, safety
filter, provider without image support) is logged and skipped so the novel export
is never blocked. Images land next to novel.md in the host-mounted output folder.
"""

import io
import logging
import os
import random
import re

from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from .config import AGENT_MODELS, IMAGE_MODEL, PROVIDER
from .db.models import Character, Story
from .llm_json import generate_structured
from .prompts.loader import load_prompt
from .schemas import ImagePromptSetOut, NovelMetadataOut

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    """Lowercase, strip everything but a-z0-9 — for spelling-tolerant compare."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _title_ok(title: str, ocr_text: str) -> bool:
    """True if the full title (normalized) appears in the OCR'd image text."""
    t = _norm(title)
    return bool(t) and t in _norm(ocr_text)

# Saved image format. WebP is a fraction of PNG's size at visually identical quality
# for photographic key art, which adds up when every story ships three images. Set
# IMAGE_FORMAT=png if something downstream cannot read WebP.
_FORMAT = (os.getenv("IMAGE_FORMAT") or "webp").strip().lower()
_SAVE_ARGS = {
    # method=6 is the slowest/best WebP encoder setting; a few hundred ms per image is
    # nothing next to the generation call that produced it.
    "webp": {"format": "WEBP", "quality": 92, "method": 6},
    "png": {"format": "PNG"},
}
if _FORMAT not in _SAVE_ARGS:
    logger.warning("IMAGE_FORMAT=%r not supported, falling back to webp", _FORMAT)
    _FORMAT = "webp"

# (stem, width, height, aspect-ratio, attr, crop-centering) — the extension comes from
# _FORMAT so switching format needs no change here.
# The model now outputs each aspect ratio natively (image_config), so cropping to
# the exact pixel size is minimal and symmetric — faces and title both survive.
_SPECS = [
    ("cover",      686, 424, "16:9", "cover",  (0.5, 0.5)),
    ("thumbnail1", 327, 462, "3:4",  "thumb1", (0.5, 0.5)),
    ("thumbnail2", 498, 642, "3:4",  "thumb2", (0.5, 0.5)),
]

_TIER_ORDER = {"core": 0, "important": 1, "secondary": 2, "minor": 3}

_ASPECT_RATIOS = {"16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "3:4": 3 / 4, "9:16": 9 / 16}


def _safe_margin_note(width: int, height: int, aspect: str) -> str:
    """Tell the model how much side margin to leave, given that we crop afterwards.

    The model renders one of a few fixed aspect ratios, and ImageOps.fit then crops
    to the exact pixel size — which for the cover means asking for 16:9 (1.778) and
    keeping 1.618, throwing away 4.5% off EACH side. So a title drawn with the 8%
    margin the prompt asks for arrives with 3.5%, reading as "touching the edge".
    That is why cover titles kept looking clipped no matter how the prompt was
    worded: the geometry ate the margin, not the model.

    Rather than hard-coding a bigger number, derive it, so this stays correct if the
    output sizes in _SPECS ever change.
    """
    target, generated = width / height, _ASPECT_RATIOS.get(aspect, width / height)
    side_loss = max(0.0, (1 - target / generated) / 2) if generated > target else 0.0
    top_loss = max(0.0, (1 - generated / target) / 2) if generated < target else 0.0
    required = round((0.08 + side_loss) * 100)
    head_required = round((0.04 + top_loss) * 100)
    return (
        f"\n\nHEADROOM: leave at least {head_required}% of the image height empty above "
        f"the tallest head. Every subject's whole head — crown, hair and all — stays "
        f"inside the frame; a scalp or forehead touching or crossing the top edge is a "
        f"failed image, however good the rest is. On a tight portrait, pull the camera "
        f"back rather than cropping the top of the head."
        f"\n\nIMAGE SAFETY MARGIN: this render will be cropped to {width}x{height}, "
        f"losing {side_loss * 100:.0f}% off each side, so the outer {required}% of the "
        f"width on the left AND right will not survive. Keep every LETTER of the title "
        f"inside the central {100 - 2 * required}%. This is a boundary, not a layout: "
        f"arcs, curves, swashes, long descenders, script tails and stacked or offset "
        f"lines are all encouraged — design freely, then size and place the whole "
        f"lettering so it lands inside that boundary. Purely decorative flourishes "
        f"(a tail, a rule, a swash) may run wider; the readable words may not."
    )

# Appended to every non-cover prompt when the cover is passed in as a reference
# image. Module-level so a one-off regeneration of a single thumbnail can reuse the
# exact same wording instead of drifting from it.
_CONSISTENCY_HINT = (
    "\n\nIMPORTANT: the attached reference image is ONLY a face guide for keeping "
    "characters consistent. Use it solely to match the FACES / hair / identity of "
    "whichever characters appear in THIS image. Do NOT copy the reference's "
    "composition, layout, or the NUMBER of people — this image has its own subject "
    "list and framing described above. If this prompt calls for a single-person "
    "portrait, show ONLY that one person even though the reference has several. "
    "Any character who does appear must match their reference face."
)


def _relationship_dynamics(session: Session, story_id: int) -> str:
    """The main power dynamics between top characters — who pursues/controls/is
    captive to whom — so the poster staging reflects the story instead of a random
    (possibly reversed) pose."""
    from .db.models import StoryGraphNode, StoryGraphEdge
    nodes = {
        n.node_key: n for n in session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="new", node_type="character").all()
    }
    if not nodes:
        return ""
    _tier = {"protagonist": 0, "antagonist": 1, "love_interest": 1,
             "antagonist/love_interest": 1, "supporting": 2}
    top = sorted(nodes.values(),
                 key=lambda n: _tier.get((n.properties or {}).get("role", ""), 3))[:5]
    top_keys = {n.node_key for n in top}
    lines = []
    for e in session.query(StoryGraphEdge).filter_by(
            story_id=story_id, graph_type="new", edge_type="RELATION").all():
        if e.source_key in top_keys and e.target_key in top_keys:
            a, b = nodes.get(e.source_key), nodes.get(e.target_key)
            if not a or not b:
                continue
            rel = (e.properties or {}).get("rel_type", e.label or "")
            cond = (e.condition or e.label or "")[:90]
            lines.append(f"- {a.label} ({(a.properties or {}).get('role','')}) → "
                         f"{b.label} ({(b.properties or {}).get('role','')}): {rel}. {cond}")
    return "\n".join(lines[:8])


def _character_lines(session: Session, story_id: int) -> str:
    chars = (
        session.query(Character)
        .filter_by(story_id=story_id)
        .all()
    )
    chars.sort(key=lambda c: _TIER_ORDER.get(c.tier or "minor", 9))
    lines = []
    for c in chars[:8]:
        prof = (c.profile_md or "").replace("\n", " ")[:420]
        lines.append(f"- {c.name} [{c.tier or 'minor'}]: {prof}")
    return "\n".join(lines)


# Art-direction menus. The prompt designer sees the same story every run, so with a
# single fixed recipe it returns near-identical art (every cover a row of big faces
# shot at 85mm). One option is drawn from each menu per run and passed in as ART
# DIRECTION, which is what actually moves the output between runs.
_COMPOSITIONS = [
    "ensemble montage — the cast's faces packed large and overlapping, classic key-art wall",
    "single hero off-centre, large, with the world opening up in the empty half of the frame",
    "two figures in symmetrical opposition, the frame split between their worlds",
    "one figure small against an overwhelming environment, epic scale, tiny silhouette",
    "extreme close-up of the protagonist's face filling the frame, the rest of the cast small and soft behind",
    "layered depth — one figure sharp in the foreground, others receding at decreasing scale",
    "low-angle hero shot looking up at the cast, the sky and architecture towering behind",
    "over-the-shoulder framing: the protagonist's back to us, facing the world she must enter",
    "wide negative-space composition, the cast pushed to one edge, most of the frame atmosphere",
    "tight two-shot, faces close and nearly touching, everything else fallen away",
]
_LENSES = [
    "24mm wide angle, sweeping and immersive",
    "35mm, environmental and grounded",
    "50mm, natural and unforced",
    "85mm portrait, shallow depth of field",
    "135mm telephoto, compressed and intimate",
    "anamorphic wide with horizontal flares and oval bokeh",
]
# Every option is a DAYLIGHT-FORWARD, well-exposed look. Moody-but-murky setups
# (chiaroscuro holding the frame in shadow, underlighting, bleached flat grades) were
# removed: they read as dark and stylised rather than photographic, which is the
# opposite of what these posters want. Contrast still varies — via light DIRECTION and
# quality, not by taking light away.
_LIGHTING = [
    "hard backlight rim-lighting the figures, faces lifted by strong bounced fill",
    "bright window light from one side, open shadows, faces clearly modelled",
    "clear daylight haze separating each plane, luminous and airy",
    "cool daylight on one side, warm practical glow on the other, both well exposed",
    "golden-hour sun raking low across the scene, warm and radiant",
    "bright overcast softbox light, even and clean on every face",
    "sunlit interior, light spilling across the scene from a large opening",
    "high-key bright light, airy and open",
]
# Commercial short-drama key art is VIVID, not naturalistic — colour is pushed hard
# and one signature hue owns the poster. Photorealism applies to the PEOPLE (real
# skin, real actors); the colour grade is deliberately stylised on top of them.
_PALETTES = [
    "punchy saturated colour, the signature hue blazing through lights and wardrobe",
    "warm-cool split complementary, each half strongly colour-graded against the other",
    "deep jewel tones, maximum richness and saturation, brightly lit",
    "the signature hue glowing from this era's own practical lights, frame still bright",
    "sunlit high-chroma colour, everything vivid and glowing",
    "bold two-tone scheme: the signature hue against one strong contrasting accent",
    "candy-bright high-key colour, cheerful and heavily saturated",
]


# Wardrobe is drawn per run like every other knob here, and for the same reason: a
# fixed instruction ("put her in a slip dress") would make every book in the catalogue
# look like the same shoot. Each entry is a REGISTER of glamour, not a garment list —
# the prompt designer realises it in whatever this story's own world would wear, so a
# crime family, a fashion house and a resort dynasty read differently while all
# clearing the same bar. The bar itself — shoulders, neckline or shape visible, never
# office or uniform — lives in the prompt, not here.
_WARDROBE = [
    "black-tie formal — floor-length gown, tuxedo or dinner jacket, serious jewellery",
    "silk and satin after midnight — slip dresses, bare shoulders, fabric that catches light",
    "the night undone — expensive eveningwear worn carelessly, jacket discarded, shirt open",
    "red-carpet shine — sequins, metallics, a dress built to be photographed",
    "warm-weather luxe — light fabrics, sun on skin, backless and strapless cuts",
    "sleek monochrome — one colour head to toe, sharp lines, deep neckline",
    "soft luxury at rest — cashmere and silk off one shoulder, barefoot, unhurried",
    "old-money elegance — restrained cuts in fine cloth, one bold slit or open back",
    "sharp tailoring worn bare — a jacket with nothing under it, sleeves pushed up",
    "caught in the weather — rain-damp hair, fabric clinging, coat pulled open",
]


# Title treatments, drawn per run for the same reason as wardrobe: left to itself the
# designer settles on plain white sans every time. Each entry is a treatment, not a
# font name — the designer realises it in the run's signature colour.
_TYPOGRAPHY = [
    "two-tone split: the first phrase heavy upright caps, the rest in large flowing "
    "italic script beneath, its tail sweeping back under the first line",
    "calligraphic script across the whole title, generous swashes on the first and "
    "last letters, the baseline dipping and rising like handwriting",
    "gently arched lettering following a wide curve, tallest at the centre, letters "
    "tilting with the arc",
    "one key word in enormous brushed script laid diagonally over the smaller "
    "upright rest of the title",
    "elegant high-contrast serif with long extended swashes and a thin rule that "
    "curves under the words",
    "gold foil-effect lettering with a soft inner glow, the very last letter drawn "
    "with a long curling tail that sweeps back beneath the words",
    "art-deco display caps with thin hairlines, the middle word dropped into a "
    "curved ribbon banner",
    "soft romantic serif, the first letter oversized and looping, the rest tucked "
    "into its curve",
    "the title split across two styles — the strongest word in bold condensed caps, "
    "the remaining words in delicate handwritten script, each word appearing once",
    "wave-set lettering: the words rise and fall along a gentle S-curve, each line "
    "offset from the last",
]


# Staging for the couple shot, drawn per run. A single fixed instruction would make
# every book in the catalogue the same embrace; these are ten different ways for two
# people to be a breath apart. Heat here comes from contact, weight and proximity —
# never from removing clothes, which only returns safety-filtered images.
_INTIMACY = [
    "his hand at her jaw or throat, tilting her face up, their mouths a breath apart",
    "taken from behind — his chest against her back, his mouth at her ear, both his "
    "hands spread across her waist while she leans into him",
    "she is backed against a wall or door, his forearm braced above her head, his body "
    "closing the last inch",
    "her fists knotted in his shirt, dragging him down to her; his hands framing her face",
    "she is sitting on a desk or counter, he stands between her knees, her legs "
    "bracketing him",
    "mid-undress: his jacket sliding off her bare shoulders, one strap already fallen",
    "dancing far too close — one of his hands splayed on her bare back, foreheads "
    "touching, eyes shut",
    "she was walking away and he has caught her wrist and pulled her back into him",
    "both caught in rain, clothes soaked and clinging, gripping each other like the "
    "argument just ended",
    "she is draped back across his lap or a couch arm, looking up at him as he leans over",
]


def _art_direction(seed: int | None = None) -> str:
    """One randomly-drawn composition / lens / lighting / palette recipe. These are
    VISUAL-style knobs only (dynamic-neutral) — the pair's staging is decided by the
    LLM from the story's power dynamic, not randomised, so it never reverses it."""
    rnd = random.Random(seed)
    # The NON-NEGOTIABLE block comes FIRST. When it sat at the bottom, a single
    # evocative palette draw ("neon-lit…") beat it and produced a dark night poster —
    # and dragged a period story into a modern skyscraper skyline along with it.
    return (
        "MANDATORY (these four override any menu choice below that conflicts):\n"
        "- Exposure: a BRIGHT, generously exposed, DAYLIGHT-BRIGHT image. Open shadows "
        "with visible detail, no crushed blacks, no murky or dim frame, no night scene "
        "unless the story is unavoidably nocturnal, no heavy vignette, no dark "
        "teal-orange grade. Faces fully and evenly lit.\n"
        "- Colour: VIVID, high-saturation commercial poster colour — pushed well past "
        "naturalistic, glowing and eye-catching, like a streaming short-drama "
        "thumbnail. Never flat, dull, washed out or muted.\n"
        "- Signature colour: choose ONE dominant hue for this story from its own world "
        "and let it OWN the poster. Name the hue explicitly in the prompt, then put it "
        "in ALL of: the lighting or the air itself, a key costume, the environment, "
        "AND the title lettering. A viewer must be able to name the poster's colour in "
        "one word at a glance — if the result reads as neutral, grey, navy-and-beige "
        "or generally 'natural', the hue was too weak. Same signature hue in all three "
        "images.\n"
        "- Period integrity: the palette NEVER changes the era. Bright, saturated and "
        "glowing must be achieved with light sources and materials that already exist "
        "in this story's world. A pre-modern story gets NO neon signs, no skyscrapers, "
        "no electric city glow — use lanterns, sunlight, dyed silk, fire, painted wood.\n"
        "- People: they stay photoreal — real actors, real skin with pores and texture, "
        "never illustrated, painted or CGI. The bold grade sits on top of a real "
        "photograph; it does not turn the people into artwork.\n"
        "\nSTYLE FOR THIS RUN (vary within the mandatory rules above):\n"
        f"- Composition (anchor for the cover): {rnd.choice(_COMPOSITIONS)}\n"
        f"- Lens: {rnd.choice(_LENSES)}\n"
        f"- Lighting: {rnd.choice(_LIGHTING)}\n"
        f"- Palette direction: {rnd.choice(_PALETTES)}\n"
        f"- Wardrobe register: {rnd.choice(_WARDROBE)} — realise it in THIS story's "
        f"world and era, and vary the three images within it\n"
        f"- Title treatment: {rnd.choice(_TYPOGRAPHY)} — coloured from the signature "
        f"hue, same treatment across all three images\n"
        f"- Intimacy staging (thumb2 only): {rnd.choice(_INTIMACY)} — stage the couple "
        f"shot this way, in this story's world\n"
    )


def _build_prompts(session: Session, story: Story, meta: NovelMetadataOut | None) -> ImagePromptSetOut:
    tags = ", ".join(meta.tags) if (meta and meta.tags) else (story.genre or "")
    system = load_prompt("image_prompt")
    art_direction = _art_direction()
    logger.info("[%s] art direction for this run:\n%s", story.slug, art_direction)
    logline = (meta.logline if meta and meta.logline else "")
    dynamics = _relationship_dynamics(session, story.id)
    user_content = (
        f"title (render EXACTLY this text on each image): {story.title}\n"
        f"tags: {tags}\n\n"
        f"## STORY DIRECTION (the plot's power dynamic — ALL three images must honour "
        f"this; never reverse who pursues/controls/is captive to whom)\n"
        f"logline: {logline or '(derive from world below)'}\n"
        f"main relationships:\n{dynamics or '(derive from characters below)'}\n\n"
        f"## ART DIRECTION (visual style for this run — composition/lens/lighting/palette; "
        f"vary the three images from each other around them)\n{art_direction}\n"
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
    _CONSISTENCY = _CONSISTENCY_HINT
    ocr_model = AGENT_MODELS.get("quality_reviewer") or IMAGE_MODEL
    _MAX_ATTEMPTS = 4
    written: list[str] = []
    cover_ref: bytes | None = None
    for stem, w, h, aspect, attr, centering in _SPECS:
        filename = f"{stem}.{_FORMAT}"
        prompt = getattr(prompts, attr, "") or ""
        if not prompt:
            continue
        is_cover = attr == "cover"
        refs = None if (is_cover or not cover_ref) else [cover_ref]
        full_prompt = (prompt if is_cover else prompt + _CONSISTENCY) + _safe_margin_note(w, h, aspect)

        best_raw: bytes | None = None  # keep a usable image even if none verify
        verified_raw: bytes | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                raw = PROVIDER.generate_image(
                    prompt=full_prompt, model=IMAGE_MODEL,
                    aspect_ratio=aspect, reference_images=refs,
                )
            except NotImplementedError:
                logger.warning("[%s] provider has no image support — skipping all images", story.slug)
                return written
            except Exception as exc:
                logger.warning("[%s] image %s attempt %d gen failed: %s",
                               story.slug, filename, attempt + 1, exc)
                continue

            best_raw = best_raw or raw
            # OCR-verify the rendered title spelling; regenerate if it's wrong.
            try:
                ocr = PROVIDER.read_image_text(image_bytes=raw, model=ocr_model)
                if _title_ok(story.title, ocr):
                    verified_raw = raw
                    break
                logger.info("[%s] %s attempt %d: title misspelled in art (ocr=%r), retrying",
                            story.slug, filename, attempt + 1, (ocr or "")[:60])
            except NotImplementedError:
                verified_raw = raw  # no OCR available → accept first good image
                break
            except Exception as exc:
                logger.warning("[%s] %s OCR check failed: %s — accepting image",
                               story.slug, filename, exc)
                verified_raw = raw
                break

        chosen = verified_raw or best_raw
        if not chosen:
            continue
        img = Image.open(io.BytesIO(chosen)).convert("RGB")
        img = ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=centering)
        img.save(out_dir / filename, **_SAVE_ARGS[_FORMAT])
        # Drop the same image in any other format left over from an earlier run, so a
        # folder never ends up with both cover.png and cover.webp.
        for other in _SAVE_ARGS:
            if other != _FORMAT:
                (out_dir / f"{stem}.{other}").unlink(missing_ok=True)
        if is_cover:
            cover_ref = chosen  # reference for thumbnail character consistency
        written.append(filename)
        logger.info("[%s] image written: %s (%dx%d)%s", story.slug, filename, w, h,
                    "" if verified_raw else " [title unverified]")
    return written
