"""Fill in author / tags / logline / summary / cast_blurbs for stories that predate them.

Those five columns arrived in migration 0006. Every book finished before it has them
NULL, which matters most for `summary`: the poster designer reads it to know what the
plot actually contains, and without it cannot reject a randomly drawn wardrobe or
staging the story rules out.

One LLM call per story, and only for stories missing the fields — safe to re-run.

    docker compose exec api python scripts/backfill_front_matter.py [--dry-run] [id ...]
"""
import argparse
import logging
import sys

sys.path.insert(0, "/app")

from app.db.session import SessionLocal          # noqa: E402
from app.db.models import Story                  # noqa: E402
from app.services.orchestrator import _generate_novel_metadata, _main_cast  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backfill")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ids", nargs="*", type=int, help="story ids; default = every story")
    ap.add_argument("--dry-run", action="store_true", help="list what would change")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even where the fields are already set")
    args = ap.parse_args()

    with SessionLocal() as session:
        q = session.query(Story)
        if args.ids:
            q = q.filter(Story.id.in_(args.ids))
        stories = q.order_by(Story.id).all()

        todo = [s for s in stories
                if args.force or not (s.logline and s.summary)]
        skipped = len(stories) - len(todo)

        log.info("%d stories, %d already complete, %d to fill",
                 len(stories), skipped, len(todo))
        if args.dry_run:
            for s in todo:
                log.info("  would fill #%s %s", s.id, s.title)
            return 0

        filled = failed = 0
        for s in todo:
            meta = _generate_novel_metadata(s, _main_cast(session, s.id))
            if not meta:
                log.warning("  #%s %s — generation failed, left as-is", s.id, s.title)
                failed += 1
                continue
            s.author = meta.author
            s.tags = list(meta.tags or [])
            s.logline = meta.logline
            s.summary = meta.summary
            s.cast_blurbs = [c.model_dump() for c in (meta.characters or [])]
            session.commit()
            filled += 1
            log.info("  #%s %s\n      %s", s.id, s.title, (meta.logline or "")[:100])

        log.info("done: %d filled, %d failed, %d skipped", filled, failed, skipped)
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
