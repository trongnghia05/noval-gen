"""The ONE place that says which market these books are written for.

Every agent whose output has to belong to that market — the world designer, the name
lexicon, the character enricher, the poster prompt writer and its verifier — is given
this same block, appended to its system prompt by `prompts.loader`. Stating it once is
the point: when the rule lived in each prompt separately, the copies drifted and
contradicted each other, and the parts nobody thought to update simply did not steer.
That is not hypothetical — the world designer was told to stay in the United States
while the name lexicon, which is what actually mints every place name, was never told
at all, and produced "Choreia Movement Hall" and "Thorne Manor" for a story set in
present-day America.

Set MARKET to switch. It is a module constant for now; when the generation tool grows
a settings screen this becomes a per-story field, and nothing else has to move.
"""

import os

MARKET = (os.getenv("MARKET") or "US").upper()


_US = """
# MARKET CONTRACT — present-day United States

These books are written for a **US audience**, so the story is set in **present-day
America**. This is not a stylistic preference; a book that reads as belonging to
another country or another century does not sell in this market. Everything below
follows from it, and applies to whatever part of your own task it touches.

## Place

- **Contemporary America, always.** Present day, or at most the last few years — no
  historical period, no future, no secondary world.
- **Say which part of America**, so that everything downstream has something to work
  from: a specific city (Chicago, Nashville, Brooklyn), or a region described precisely
  enough to picture (a Gulf Coast port town, a Rust Belt steel city, a Colorado ski
  resort, the Texas hill country).
- **A real American place name and an invented one are equally fine.** "Nashville" and
  "Harbor Point, Maine" are both correct. What is NOT acceptable is a placeless
  "glittering modern metropolis" with no country in it — that leaves the cast, the
  dialogue and the cover art with nothing to be American about, and they drift.

## Names

Everything named in the story — people, streets, schools, companies, bars, venues,
neighbourhoods — must sound like it belongs in that American place. Invented names are
expected and welcome; they just have to sound American.

- **People**: American register, and vary it between stories — old-money
  surnames-as-first-names (Sutton, Beckett), plain working-class (Dana Kowalski, Ray
  Alvarez), Southern (Wyatt, Maybelle), urban (Marisol Ortega, Deshawn Carter), preppy
  East Coast (Whitney Ellis). Pick what fits this story's region and class.
- **Places and institutions**: name them the way Americans name things — after streets,
  founders, neighbourhoods, landmarks or plain function. Riverside Dance Academy, the
  Fillmore Street studio, Harbor Point, Thorne & Associates, the Blue Room.
- **These are wrong and have shipped before**: "Choreia Movement Hall", "Harmonia Grand
  Chamber", "Academia Grand Corridor", "The Nocturne Backpassage", "Trolley Terminal
  Nexus", "Thorne Manor". Greek and Latin coinages, "Grand/Great Hall", "Chamber",
  "Nexus", "Manor" and "Estate" belong to fantasy and to European period drama, not to
  an American city. A rich family lives in a *house* — in Brentwood, on the lake, off
  Highway 12 — not in a manor.
- **No aristocratic titles.** No Lady, Lord, Duke, Baroness or Madame. America has no
  aristocracy; status here is money, fame, a name on the building, a seat on the board.

## People

- The cast reads as **Americans of the place named above**. That population is
  genuinely mixed and the supporting cast should reflect it — but it is an American
  mix, drawn from who actually lives there, never a heritage picked because it sounded
  striking.
- **A character's look must match their name** without needing an explanation. If you
  want a lead of a particular heritage, give them a name that goes with it rather than
  pairing an unrelated surname with the look. Reaching for "Eurasian", "exotic" or any
  non-American styling to make a lead distinctive reads as the wrong market — this
  exact drift has shipped on a real cover.
""".strip()


_BLOCKS = {"US": _US}


def market_block() -> str:
    """The market contract to append to a market-aware agent's system prompt."""
    return _BLOCKS.get(MARKET, _US)
