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
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from ..core import storage
from ..core.config import AGENT_MODELS, IMAGE_MODEL, PROVIDER
from ..db.models import Character, Story, StoryImage
from ..llm.structured import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ImagePromptSetOut, ImagePromptVerifyOut, NovelMetadataOut

logger = logging.getLogger(__name__)


def _strip_to_alnum(s: str) -> str:
    """Lowercase, strip everything but a-z0-9 — for spelling-tolerant compare."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _title_ok(title: str, ocr_text: str) -> bool:
    """True if the full title (normalized) appears in the OCR'd image text."""
    t = _strip_to_alnum(title)
    return bool(t) and t in _strip_to_alnum(ocr_text)

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

# Redraft attempts before accepting whatever the prompt writer last produced. Five is
# generous — the loop also stops early the moment the same fault set repeats, which is
# the real signal that further attempts are wasted.
_MAX_PROMPT_VERIFY = 5

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
    from ..db.models import StoryGraphNode, StoryGraphEdge
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
# The palettes below all say "the signature hue" and leave the actual colour to the
# prompt designer — who, with no memory between runs, picks blue, teal or emerald
# almost every time. Same mode-seeking as the male lead's face. Drawing the hue fixes
# it; the blue family is present but is now one option in twelve, not the default.
_SIGNATURE_HUE = [
    "deep crimson red",
    "hot magenta",
    "burnt orange",
    "gold and amber",
    "emerald green",
    "sapphire blue",
    "violet and plum",
    "coral and warm pink",
    "acid yellow-green",
    "oxblood and wine",
    "turquoise",
    "silver and cold white",
    # Restrained hues. Half the covers of any real bookshelf are quiet, and a catalogue
    # where every poster shouts is as monotonous as one where every poster is blue.
    # These still OWN the frame — a warm sand poster reads as a sand poster — they just
    # do it without maximum chroma.
    "warm sand and cream",
    "soft dusty rose",
    "sage and pale eucalyptus",
    "warm greys with a single warm accent",
    "muted olive and stone",
    "ivory and pale gold",
]

_PALETTES = [
    "punchy saturated colour, the signature hue blazing through lights and wardrobe",
    "warm-cool split complementary, each half strongly colour-graded against the other",
    "deep jewel tones, maximum richness and saturation, brightly lit",
    "the signature hue glowing from this era's own practical lights, frame still bright",
    "sunlit high-chroma colour, everything vivid and glowing",
    "bold two-tone scheme: the signature hue against one strong contrasting accent",
    "candy-bright high-key colour, cheerful and heavily saturated",
    # Quieter grades. Every entry above pushes chroma hard, which made the catalogue
    # uniformly loud. These stay BRIGHT and clearly lit — the mandatory exposure rule
    # is untouched — but let the colour be restrained, the way much premium key art is.
    "soft natural colour, gently graded, the signature hue clear but not shouting",
    "warm neutral palette — creams, sand and skin tones, one quiet accent of the hue",
    "airy pastel grade, low chroma and high brightness, clean and modern",
    "near-monochrome in the signature hue: many tones of one colour, quietly rich",
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
    # Registers where HE is the one on display. The menu used to dress everyone up,
    # which is why he arrived in tailoring on nearly every poster; correcting that with
    # five undressed registers then produced runs where he was stripped in all three.
    # These undo him without taking the shirt off — an open shirt, a wet shirt, a
    # half-fastened collar suits far more stories than a bare chest, and does not spend
    # the reveal. Only the first two go as far as bare skin, and the prompt caps him at
    # ONE bare-chested image per set regardless.
    "the morning after — a sheet, a shirt hanging open over bare skin, bare feet, hair "
    "still wrecked from sleep",
    "straight from exertion — a fight, a forge, a sparring floor or a gym: sweat, "
    "wrapped hands, a shirt soaked through and clinging, muscle and effort visible",
    "shirt open at the chest — buttons undone to the sternum, no tie, collar loose, "
    "chain or pendant against skin",
    "undone after a long night — bow tie hanging loose, top buttons open, shirt "
    "half-untucked, jacket over one shoulder",
    "silk and skin at rest — a robe or unfastened overshirt worn loose, sleeves pushed "
    "high, nothing beneath fastened",
    "caught in the rain — soaked shirt clinging to every line of him, hair dripping, "
    "fabric gone translucent",
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
    "he has her backed up with nowhere to go, one arm braced past her head, his body "
    "closing the last inch",
    "a table or desk between them, both leaning across it, faces closer than the "
    "furniture should allow",
    "one seated and one standing over them, a hand on the chair back, the height "
    "difference doing the work",
    "on a staircase — she is a step above, so she looks down at him for once, and "
    "neither will move first",
    "back to back, shoulders touching, facing opposite ways as if surrounded",
    "he is holding something she wants just out of reach, and she has come in close "
    "to take it",
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
    # Skin contact — the fifteen above stage the pair close, but every one of them
    # keeps him clothed, and the only "mid-undress" entry undresses HER. Without these
    # the couple shot is always two dressed people standing near each other.
    "her palms flat on his bare chest, either holding him off or holding him there — "
    "his own hands closed over hers",
    "he is shirtless with his arms around her from behind, her back against his skin, "
    "her head tipped onto his shoulder, both of them looking at the same thing",
    "she is winding a bandage around his bare ribs and neither of them is looking at "
    "the wound",
    "carried — she is up in his arms or on his back, his shirt gone, her arms locked "
    "around his neck, both mid-laugh or mid-argument",
    "wrapped in one blanket, coat or cloak together, his chest bare under it, only "
    "their faces and her hands showing",
]


# ── Emotional register ────────────────────────────────────────────────────────
# The only emotional direction in the whole prompt was "charged, not polite", "tension
# and heat", "a look held a beat too long" — and 17 of the 20 intimacy poses stage a
# confrontation. So every poster came back the same: he stares at her, she gives one
# unreadable look, nobody feels anything else. A romance catalogue needs joy, laughter,
# grief and relief on its covers too, not one smouldering note repeated.
#
# These are registers to interpret, not captions: a mafia story and a small-town
# romance play "the moment it all lands" completely differently.
# The feeling only. Two earlier versions both over-specified and both shipped bad art:
# first as scenarios ("the second the news landed"), which the writer copied into plots
# that could not contain them; then as facial mechanics ("shock — mouth open, eyes
# wide"), which the image model executed literally and returned a whole set of posters
# with everyone's mouth hanging open. Naming the emotion and stopping is what leaves the
# writer free to express it in a way this particular story and scene can carry.
_EMOTION = [
    "joy",
    "contentment",
    "grief",
    "triumph",
    "longing",
    "fury",
    "tenderness",
    "shock",
    "mischief",
    "relief",
    "defiance",
    "fear",
    "smouldering restraint — the charged, held look",
]

# Where the eyes go. He was gazing at her in essentially every shipped poster, which
# flattens all three images into the same beat. Every entry describes TWO people, so
# this axis belongs to the cover and thumb2; thumb1 draws from _SOLO_POSE instead.
_EYELINE = [
    "both looking straight down the lens, daring the viewer",
    "she looks at the camera; he is looking at her",
    "he looks at the camera; she is looking at him",
    "neither looks at the other — both fixed on something outside the frame",
    "eyes closed, faces close, the moment held",
    "one looking down at the other, who is looking up",
    "both looking at the same off-frame thing, shoulder to shoulder",
    "she is looking away and he is watching her decide",
]

# How the lone portrait stands. The cover had _COMPOSITIONS and thumb2 had _INTIMACY,
# but thumb1's body was never described by anything — so the model returned its modal
# solo portrait every run: standing, torso square to the lens, head level, eyes front,
# hands out of frame. Six shipped posters were that same picture. Each entry fixes body
# orientation, head angle, hands and gaze together, because naming only one of the four
# leaves the rest to default back.
_SOLO_POSE = [
    "three-quarter turn away, face coming back over her near shoulder to the lens",
    "full profile, chin lifted, eyes on something past the edge of the frame",
    "chin resting on the back of her hand, elbow propped, gaze level and unhurried",
    "seated, leaning in with forearms crossed, shoulders rolled toward the lens",
    "head tipped back, throat bared, eyes half-lowered",
    "one hand at the base of her throat, body squared, head angled away",
    "her back to the lens, face turned just enough to read the near cheek and eye",
    "fingers pushing back through her hair, elbow high, eyes into the lens",
    "shoulder against a wall or door frame taking her weight, head tilted onto it",
    "hands busy with something this story owns, eyes down on it",
    "arms folded, weight on one hip, head lowered so she looks up through her lashes",
    "seated side-on, one arm along the chair back, torso twisted to the lens",
    "caught mid-turn, hair still carrying the movement, eyes just arriving at the lens",
    "shot from slightly below, face three-quarters away, jaw and cheekbone leading",
    "a curtain, door or window edge pushed aside by one hand, half-framing her face",
]


# ── Casting the male lead ─────────────────────────────────────────────────────
# Measured across three independent runs, every male lead came back as the same man:
# 35-38 years old, "strong jawline", dark or grey eyes that "miss nothing". The
# character enricher writes that description and has no memory of what it wrote for
# other stories, so it returns the modal romance hero every time and the image model
# faithfully draws him. The female leads did NOT need this — their faces already vary
# between stories.
#
# Three orthogonal axes rather than one flat list: a dozen entries each multiply out
# to hundreds of combinations, where a single list of fifteen would itself start
# repeating by the fifteenth book. Drawn once per run (like wardrobe and intimacy) so
# the cover and both thumbnails cast the same man.
#
# EVERY entry below is a HANDSOME man. They differ in TYPE, never in how attractive he
# is — the verifier flags a male lead who is not described as attractive and magnetic,
# so an entry that reads as plain or declining would fight it and stall the loop.
# These are DIRECTIONS to interpret in the story's own world, not costumes to paste.
_MALE_BUILD = [
    "lean and wiry — narrow through the hips, more speed than mass, every line of him defined",
    "tall and powerfully built — thick through the neck and shoulders, a body that fills a doorway",
    "broad-shouldered and V-tapered, a swimmer's frame",
    "compact and dense — not the tallest man in the room, but built low and solid and impossible to move",
    "rangy and rawboned — long limbs, visible tendon, hard and stripped down",
    "long-limbed and elegant — narrow, upright, moves like he was trained to",
    "heavy and strong rather than sculpted — a big man's body, warm and imposing",
    "athletic and mid-sized — nothing extreme, but every part of him is used",
    "leonine — wide chest, thick arms, a slow heavy grace",
    "slight and deceptively strong — people underestimate him exactly once",
]

_MALE_FACE = [
    "a broad, blunt, handsome face and a heavy brow; close-cropped hair",
    "high cheekbones and a narrow jaw; dark hair long enough to fall in his eyes",
    "an open, boyish face that reads younger than he is; soft thick hair, clean-shaven",
    "hollow cheeks and a hard beautiful mouth; hair shaved close at the sides, longer on top",
    "a full well-kept beard and deep-set eyes; thick hair worn pushed back",
    "clean-shaven, a long straight nose and a wide mouth; hair tied back off his face",
    "heavy stubble and a nose broken once and set slightly off; short unruly waves",
    "a square face and a soft mouth; tight curls kept short",
    "fine, almost beautiful features that sit strikingly on a hard frame; straight hair, side-parted",
    "a weathered outdoor face, sun lines at the eyes; hair going silver early at the temples",
    "a wide face and quick dark eyes; hair worn short and pushed straight back off a high forehead",
    "a narrow aristocratic face and a neat trimmed moustache; hair combed back",
]

_MALE_MARK = [
    "an old scar through one eyebrow",
    "a scar along one forearm, usually covered",
    "hands that are visibly a working man's — scarred knuckles, short nails",
    "he does not smile with his teeth, only with his eyes",
    "a tattoo running up one side of his neck",
    "a gap between his front teeth that shows when he finally laughs",
    "one ear scarred at the rim",
    "eyes of two slightly different colours",
    "a habit of holding perfectly still while everyone else moves",
    "ink or callus on the fingers of one hand from his trade",
    "a jaw he keeps clenched, so the muscle shows",
    "always fractionally under-groomed compared to what he is wearing",
]


def _art_direction(seed: int | None = None) -> str:
    """House style plus the dice for one run. Reasons live next to each menu above."""
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
        # Colour STRENGTH is drawn below, not fixed here. This line used to demand
        # "VIVID, high-saturation… never muted", which cancelled every restrained
        # palette in the menu and made the whole catalogue shout at one volume.
        "- Colour: deliberate and commercial, following the palette drawn below — which "
        "may be candy-bright or deliberately restrained. Either way the colour is "
        "CHOSEN and consistent, never an accidental wash of nothing.\n"
        "- Signature colour: the hue is drawn below. Name it explicitly, then anchor it "
        "in TWO OR THREE places — the light, one key garment, one element of the "
        "setting, the title lettering — never in everything at once. A gold poster is "
        "gold light and one gold dress, not gold walls, gold sky and gold clothes on "
        "everyone; that is a colour cast, not art direction. Same hue in all three "
        "images.\n"
        # Every example here is conditional on the era, never absolute. An earlier
        # version listed the pre-modern case as if it were universal ("no skyscrapers…
        # not a hotel suite, an office tower") — which, once stories became
        # present-day American, told the writer that the story's OWN world was
        # forbidden, and gave the verifier a rule to fault it with every round.
        "- Period integrity: NOTHING below may drag the story out of its own era — not "
        "the palette, not the wardrobe, not the setting. Read the era from the world "
        "description and hold every image to it. The era can be any period, so the "
        "examples below apply only to the era that matches this story.\n"
        "  · Palette and light: brightness and saturation come from sources that "
        "already exist in THIS world. If the story is pre-modern, that means lanterns, "
        "sunlight, fire, dyed silk and painted wood, with no neon or electric glow; if "
        "it is present-day, city light, neon and glass are all fair game.\n"
        "  · Wardrobe: the register named below is a LEVEL OF DRESS, not a set of "
        "garments. Translate it into what this era actually wore. 'Black-tie' in a "
        "19th-century world means tailcoats, waistcoats, cravats and corseted ball "
        "gowns — never a modern tuxedo or a satin slip dress; in a present-day story "
        "the modern tuxedo and slip dress are exactly right. Allure comes from what "
        "that era's own clothes can do: bare shoulders, a low back, a cinched waist.\n"
        "  · Setting: every prop, room and view belongs to the era — a drawing room, "
        "an atelier or a gaslit street for a period story; an office tower, a penthouse "
        "or a hotel suite for a present-day one. Wrong century, not wrong century.\n"
        "- People: they stay photoreal — real actors, real skin with pores and texture, "
        "never illustrated, painted or CGI. The bold grade sits on top of a real "
        "photograph; it does not turn the people into artwork.\n"
        # Values only. Every rule about how to use them lives in image_prompt.md, once
        # — this text is rebuilt and sent on every run, and each past fix used to append
        # another paragraph of justification here (the emotion line reached 487
        # characters, the casting line 1074, against 40 for the lens line nobody had
        # ever had to fix).
        "\nTHIS RUN — drawn at random. Check each against STORY DIRECTION and discard "
        "any the plot cannot contain:\n"
        f"- composition (anchor for the cover): {rnd.choice(_COMPOSITIONS)}\n"
        f"- lens: {rnd.choice(_LENSES)}\n"
        f"- lighting: {rnd.choice(_LIGHTING)}\n"
        f"- signature hue: {rnd.choice(_SIGNATURE_HUE)}\n"
        f"- palette: {rnd.choice(_PALETTES)}\n"
        f"- wardrobe register: {rnd.choice(_WARDROBE)}\n"
        f"- title treatment: {rnd.choice(_TYPOGRAPHY)}\n"
        f"- emotional register: {rnd.choice(_EMOTION)}\n"
        f"- eyelines (cover and thumb2): {rnd.choice(_EYELINE)}\n"
        f"- solo portrait pose (thumb1 only): {rnd.choice(_SOLO_POSE)}\n"
        f"- male lead build: {rnd.choice(_MALE_BUILD)}\n"
        f"- male lead face and hair: {rnd.choice(_MALE_FACE)}\n"
        f"- male lead distinguishing feature: {rnd.choice(_MALE_MARK)}\n"
        f"- intimacy staging (thumb2 only): {rnd.choice(_INTIMACY)}\n"
    )


def _world_block(story: Story) -> str:
    """Era, setting and premise for the prompt designer.

    This used to be `story_bible[:3000]`, which was the wrong half of the wrong file:
    the bible is a plot summary whose opening is a run of relationship statements
    ("A is married to B, C is the cousin of D"), so the window closed before any
    physical setting appeared — on one story it contained zero words describing a
    place. Worse, those relationships are already supplied above under STORY
    DIRECTION, so the space was being spent twice on the same facts.

    The world bible is the file that actually describes the world, and the era lives
    on the bible's first line, so both are needed — taking either alone loses the
    other. Neither is truncated: this text is read by the prompt designer, which
    distils it into a short paragraph, and never reaches the image model itself.
    The bible contributes only its prose; everything from its first `## ` heading on
    is the per-chapter plot map and the relationship table, neither of which tells a
    poster anything.
    """
    bible = story.story_bible or ""
    era = ""
    m = re.search(r"^ERA:[ \t]*(.+)$", bible, re.MULTILINE)
    if m:
        era = m.group(1).strip()

    cut = bible.find("\n## ")
    premise = (bible[:cut] if cut > 0 else bible).strip()
    if m:  # the ERA line is reported separately; don't repeat it in the premise
        premise = premise.replace(m.group(0), "", 1).strip()

    parts = []
    if era:
        parts.append(f"## ERA — every image must belong to this period\n{era}")
    if story.world_bible:
        parts.append(f"## WORLD (setting, places, atmosphere)\n{story.world_bible.strip()}")
    if premise:
        parts.append(f"## PREMISE & TONE\n{premise}")
    return "\n\n".join(parts) + "\n\n" if parts else ""


def _user_direction(notes: str) -> str:
    """The caller's own instructions for this run, and what they may override.

    Both the prompt writer and the verifier get this block. Giving it to only one of
    them is what makes the verify loop oscillate: the writer follows the request, the
    verifier has never heard of it and reports the result as a fault, every round.
    """
    notes = (notes or "").strip()
    if not notes:
        return ""
    return (
        "## USER DIRECTION — what the person requesting these images asked for\n"
        f"{notes}\n\n"
        "This OVERRIDES the ART DIRECTION menu above wherever the two disagree: the "
        "menu was drawn at random, this was asked for deliberately. It does NOT "
        "override the story's own era, the protagonist holding the cover foreground, "
        "the cast's names, the title being complete and drawn once inside the safe "
        "area, or the poster-safe limits — those hold regardless.\n"
        "A line beginning `cover:`, `thumb1:` or `thumb2:` applies to that image only; "
        "anything else applies to all three.\n\n"
    )


def _build_prompts(session: Session, story: Story, meta: NovelMetadataOut | None,
                   notes: str = "", seed: int | None = None) -> ImagePromptSetOut:
    tags = ", ".join(meta.tags) if (meta and meta.tags) else (story.genre or "")
    system = load_prompt("image_prompt")
    art_direction = _art_direction(seed)
    logger.info("[%s] art direction for this run:\n%s", story.slug, art_direction)
    if notes:
        logger.info("[%s] user direction: %s", story.slug, notes.strip()[:300])
    # Front matter is stored on the story, so a regenerate (which has no `meta`) still
    # gets it. Without the summary the writer had no idea what the plot contains and
    # could not reject a drawn line the book rules out — that shipped a bandaged,
    # bare-chested wound onto a fake-engagement story with no violence in it.
    # A book finished before those columns existed has neither. Rather than minting
    # them here — which would charge an LLM call to the first image run of every old
    # story — scripts/backfill_front_matter.py fills them in one pass.
    logline = (meta.logline if meta and meta.logline else story.logline) or ""
    summary = (meta.summary if meta and meta.summary else story.summary) or ""
    dynamics = _relationship_dynamics(session, story.id)
    cast = _character_lines(session, story.id)
    user_content = (
        f"title (render EXACTLY this text on each image): {story.title}\n"
        f"tags: {tags}\n\n"
        f"## STORY DIRECTION (what this book is, and its power dynamic — ALL three "
        f"images must honour this; never reverse who pursues/controls/is captive to "
        f"whom, and never show something the plot does not contain)\n"
        f"logline: {logline or '(derive from world below)'}\n"
        f"what happens: {summary or '(derive from world below)'}\n"
        f"main relationships:\n{dynamics or '(derive from characters below)'}\n\n"
        f"## ART DIRECTION (visual style for this run — composition/lens/lighting/palette; "
        f"vary the three images from each other around them)\n{art_direction}\n"
        f"{_world_block(story)}"
        f"## MAIN CHARACTERS\n{cast}\n\n"
        # The cast lines carry each character's age in the novel, and the writer copies
        # the lead's verbatim ("in her late twenties") unless told otherwise right
        # here — costing three verify rounds on every single run to undo.
        f"{_CASTING_AGE_NOTE}\n"
        f"{_user_direction(notes)}"
    )

    def draft(feedback: str = "") -> ImagePromptSetOut:
        return generate_structured(
            PROVIDER, system=system, user_content=user_content + feedback,
            model=AGENT_MODELS.get("image_prompt", AGENT_MODELS["quality_reviewer"]),
            schema=ImagePromptSetOut, max_tokens=2048, thinking=False,
        )

    prompts = draft()
    facts = (
        f"## FACTS\n{_world_block(story)}"
        f"## POWER DYNAMIC\n{dynamics or '(none recorded)'}\n\n"
        f"## CAST\n{cast}\n\n"
        f"{_CASTING_AGE_NOTE}\n"
        f"## ART DIRECTION DRAWN FOR THIS RUN\n{art_direction}\n"
        f"{_user_direction(notes)}"
        f"## TITLE (exact)\n{story.title}\n"
    )
    return _verify_prompts(story, prompts, facts, draft)


# The cast list carries the character's age *in the novel*; the poster deliberately
# casts the female lead younger (see image_prompt.md). Without saying so here, the
# verifier reads the two as a contradiction and demands a "fix" every single round —
# it fought this for three attempts on a real run before converging.
_CASTING_AGE_NOTE = (
    "## CASTING NOTE — this is policy, not an error\n"
    "The poster casts the FEMALE LEAD as a young woman of **18 to 20**, stated as a "
    "specific number. This deliberately overrides whatever age the cast list gives "
    "her: a prompt saying she is 19 while the cast says late twenties is CORRECT and "
    "must not be reported as a fault. There is no plot that changes this — being "
    "married, a mother or an executive does not make her older here. Any number from "
    "18 to 20 is acceptable and final; only a missing number, or one outside that "
    "range, is wrong. Everyone else is rendered at the age they are written as.\n"
)


def _verify_prompts(story: Story, prompts: ImagePromptSetOut, facts: str,
                    draft) -> ImagePromptSetOut:
    """Check the drafted prompts against the story's facts, and redraft on faults.

    Verifying text before generating is far cheaper than the alternative: every fault
    caught here — a modern tuxedo in a period story, a supporting character taking the
    cover, a title drawn twice — otherwise costs three image generations and is only
    noticed by eye afterwards.

    The redraft goes back to the same writer with the faults quoted, rather than to a
    patcher: the graph verifier taught that a targeted correction converges where a
    blind rebuild oscillates, and naming the fault is what makes this targeted.

    Never fatal. A verifier error, or prompts that will not converge, fall through to
    whatever the writer last produced — a flawed poster beats no poster.
    """
    previous: str | None = None
    for attempt in range(_MAX_PROMPT_VERIFY):
        try:
            report = generate_structured(
                PROVIDER, system=load_prompt("image_prompt_verifier"),
                user_content=(f"{facts}\n## DRAFTED PROMPTS\n"
                              f"cover:\n{prompts.cover}\n\nthumb1:\n{prompts.thumb1}\n\n"
                              f"thumb2:\n{prompts.thumb2}\n"),
                model=AGENT_MODELS.get("image_prompt_verifier",
                                       AGENT_MODELS["quality_reviewer"]),
                schema=ImagePromptVerifyOut, max_tokens=4096, thinking=False,
            )
        except Exception as exc:
            logger.warning("[%s] image prompt verify failed, using prompts as-is: %s",
                           story.slug, exc)
            return prompts

        if not report.issues:
            logger.info("[%s] image prompts verified clean (attempt %d)",
                        story.slug, attempt + 1)
            return prompts

        signature = " | ".join(sorted(f"{i.image}:{i.check}" for i in report.issues))
        for i in report.issues:
            logger.info("[%s]   [%s/%s] %s", story.slug, i.image, i.check,
                        i.description[:160])
        if signature == previous:
            logger.warning("[%s] image prompts not converging (%s) — accepting",
                           story.slug, signature)
            return prompts
        previous = signature

        feedback = "\n\n## FIX THESE FAULTS IN YOUR PREVIOUS DRAFT\n" + "\n".join(
            f"- [{i.image} / {i.check}] {i.description}\n  FIX: {i.fix}"
            for i in report.issues
        ) + "\nReturn the corrected JSON for all three images, changing only what is named here."
        try:
            prompts = draft(feedback)
        except Exception as exc:
            logger.warning("[%s] redraft failed, keeping previous prompts: %s",
                           story.slug, exc)
            return prompts

    logger.warning("[%s] image prompts still flagged after %d attempts — accepting",
                   story.slug, _MAX_PROMPT_VERIFY)
    return prompts


_ALL_STEMS = ("cover", "thumbnail1", "thumbnail2")


def _existing_image_bytes(out_dir, stem: str) -> bytes | None:
    """Raw bytes of an already-saved image (any supported extension), or None."""
    for ext in ("webp", "png", "jpg", "jpeg"):
        p = Path(out_dir) / f"{stem}.{ext}"
        if p.exists():
            return p.read_bytes()
    return None


def _live_image_bytes(session: Session, story: Story, out_dir, stem: str) -> bytes | None:
    """The live image's bytes, from storage when configured and from disk otherwise.

    Load-bearing for face consistency: redrawing one thumbnail feeds the current
    cover back to the model as the identity reference, so the same people come back.
    Reading it from disk after the art moved to object storage would silently return
    None and every regenerated thumbnail would come back with different faces.
    """
    if storage.enabled():
        row = (
            session.query(StoryImage)
            .filter_by(story_id=story.id, stem=stem, state="live")
            .one_or_none()
        )
        return storage.get_bytes(row.object_key) if row else None
    return _existing_image_bytes(out_dir, stem)


def _persist_image(session: Session, story: Story, stem: str, data: bytes,
                   w: int, h: int, out_dir, preview: bool) -> None:
    """Store one finished image and record it, or write a file when there is no bucket."""
    if not storage.enabled():
        (Path(out_dir) / f"{stem}.{_FORMAT}").write_bytes(data)
        # Drop the same image in any other format left over from an earlier run, so a
        # folder never ends up with both cover.png and cover.webp.
        for other in _SAVE_ARGS:
            if other != _FORMAT:
                (Path(out_dir) / f"{stem}.{other}").unlink(missing_ok=True)
        return

    state = "preview" if preview else "live"
    key = storage.object_key(story.id, stem, _FORMAT)
    storage.put(key, data, f"image/{_FORMAT}")

    row = (
        session.query(StoryImage)
        .filter_by(story_id=story.id, stem=stem, state=state)
        .one_or_none()
    )
    superseded = row.object_key if row else None
    if row is None:
        row = StoryImage(story_id=story.id, stem=stem, state=state)
        session.add(row)
    row.object_key = key
    row.width, row.height = w, h
    row.content_type = f"image/{_FORMAT}"
    session.flush()
    # Only after the row points at the new key, so a crash in between leaves an
    # orphaned object rather than a row pointing at something already deleted.
    if superseded and superseded != key:
        storage.delete(superseded)


def generate(session: Session, story: Story, out_dir,
             meta: NovelMetadataOut | None = None, only: str | None = None,
             notes: str = "", seed: int | None = None, preview: bool = False) -> list[str]:
    """Generate cover + 2 thumbnails. Best-effort; returns the stems written.

    Art goes to object storage when a bucket is configured, and to `out_dir` as files
    otherwise — `out_dir` is still required for that fallback and for callers that
    stage art outside a story's own folder.

    preview: write as the pending set rather than the live one, so the current art
    stays untouched until someone accepts the new set.

    only: None/"all" regenerates every image; or one of "cover"/"thumbnail1"/
    "thumbnail2" to regenerate just that one. When a thumbnail is regenerated
    without the cover, the current live cover is loaded as the identity
    reference so the same faces carry over.

    notes: free-text steering for this run ("warmer, put her in red", "thumb2:
    less contact"). Overrides the randomly drawn art direction, never the story's
    era, the cast, or the title rules.
    seed: fixes the art-direction draw, so a run can be reproduced. Left None the
    menus are drawn fresh and two runs of the same story look different.
    """
    targets = set(_ALL_STEMS) if (not only or only == "all") else {only}
    if not targets <= set(_ALL_STEMS):
        raise ValueError(f"unknown image target {only!r}")
    try:
        prompts = _build_prompts(session, story, meta, notes=notes, seed=seed)
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
    # Regenerating only a thumbnail: reuse the existing cover as the identity
    # reference so the same faces carry over (the cover isn't being redrawn).
    if "cover" not in targets:
        cover_ref = _live_image_bytes(session, story, out_dir, "cover")
        if cover_ref is None:
            logger.warning("[%s] regenerate %s: no existing cover to use as reference",
                           story.slug, targets)
    for stem, w, h, aspect, attr, centering in _SPECS:
        if stem not in targets:
            continue
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
        buf = io.BytesIO()
        img.save(buf, **_SAVE_ARGS[_FORMAT])
        _persist_image(session, story, stem, buf.getvalue(), w, h, out_dir, preview)
        if is_cover:
            cover_ref = chosen  # reference for thumbnail character consistency
        written.append(filename)
        logger.info("[%s] image written: %s (%dx%d)%s", story.slug, filename, w, h,
                    "" if verified_raw else " [title unverified]")
    return written
