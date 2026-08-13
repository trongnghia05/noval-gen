"""Helpers shared by more than one router.

Only what two or more routers genuinely need. A helper used by a single router
stays in that router — moving it here would spread one feature across two files
for no gain.
"""

from ..core.config import OUTPUT_BASE
from ..db.models import Story, StoryImage
from ..core import storage




def story_output_dir(story: Story):
    """This story's export dir, resolved via the same ownership marker the compiler
    uses so a slug collision can't point at another story's folder."""
    folder = story.slug or f"story-{story.id}"
    for cand in (OUTPUT_BASE / folder, OUTPUT_BASE / f"{folder}-{story.id}"):
        if (cand / f".story-{story.id}").exists():
            return cand
    return OUTPUT_BASE / folder



def story_image_dir(story: Story):
    return _story_output_dir(story) / "image"



def preview_dir(story: Story):
    """Where a regenerated set waits to be accepted. A dot-prefixed name so the
    poster readers, which glob `image/*`, never pick it up as live art."""
    return _story_image_dir(story) / ".preview"



def image_urls(session, story_id: int, state: str) -> dict[str, str]:
    """{stem: signed URL} for one set. Empty when there is no bucket — the playground
    then falls back to reading the files it has mounted."""
    if not storage.enabled():
        return {}
    rows = session.query(StoryImage).filter_by(story_id=story_id, state=state).all()
    return {r.stem: storage.signed_url(r.object_key) for r in rows}
