"""Move existing poster art from output/<slug>/image/ into object storage.

Run once after 0007_add_story_images. Idempotent: a story that already has live rows
is skipped, so re-running after a partial pass costs nothing and fixes only the gap.
The local files are LEFT IN PLACE — they are the rollback if something is wrong with
the upload, and nothing reads them once the rows exist.

    docker compose exec api python scripts/migrate_images_to_gcs.py [--dry-run] [id...]
"""

import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app.core import storage             # noqa: E402
from app.core.config import OUTPUT_BASE  # noqa: E402
from app.db.models import Story, StoryImage   # noqa: E402
from app.db.session import SessionLocal       # noqa: E402

STEMS = ("cover", "thumbnail1", "thumbnail2")
EXTS = ("webp", "png", "jpg", "jpeg")


def _image_dir(story: Story) -> Path | None:
    """The story's own export folder, identified by the ownership marker so a slug
    collision cannot pull in another story's art."""
    folder = story.slug or f"story-{story.id}"
    for cand in (OUTPUT_BASE / folder, OUTPUT_BASE / f"{folder}-{story.id}"):
        if (cand / f".story-{story.id}").exists():
            return cand / "image"
    d = OUTPUT_BASE / folder / "image"
    return d if d.is_dir() else None


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not storage.enabled():
        sys.exit("GCS_BUCKET is not set — nothing to migrate to")

    session = SessionLocal()
    q = session.query(Story).order_by(Story.id)
    if args:
        q = q.filter(Story.id.in_([int(a) for a in args]))

    moved = skipped = 0
    for story in q.all():
        have = (
            session.query(StoryImage)
            .filter_by(story_id=story.id, state="live")
            .count()
        )
        if have:
            print(f"[{story.id}] {story.slug}: da co {have} anh trong DB — bo qua")
            skipped += 1
            continue

        img_dir = _image_dir(story)
        if not img_dir or not img_dir.is_dir():
            continue

        found = []
        for stem in STEMS:
            for ext in EXTS:
                p = img_dir / f"{stem}.{ext}"
                if p.exists():
                    found.append((stem, ext, p))
                    break
        if not found:
            continue

        print(f"[{story.id}] {story.slug}: {len(found)} anh"
              + (" (dry-run)" if dry else ""))
        if dry:
            for stem, ext, p in found:
                print(f"    {p.name} -> {storage.object_key(story.id, stem, ext)}")
            continue

        for stem, ext, p in found:
            key = storage.object_key(story.id, stem, ext)
            storage.put(key, p.read_bytes(), f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}")
            session.add(StoryImage(
                story_id=story.id, stem=stem, state="live", object_key=key,
                content_type=f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}",
            ))
        session.commit()
        moved += 1

    print(f"\nxong: {moved} truyen chuyen, {skipped} bo qua")


main()
