"""Object storage for poster art — the ONLY place that talks to a storage bucket.

Everything else (image_generator, the API, the zip builder) goes through the five
functions below, so swapping provider later is confined to this file.

Currently Google Cloud Storage, authenticated by the same service account Vertex
already uses. There is no second credential: `google-cloud-storage` reads
GOOGLE_APPLICATION_CREDENTIALS, which docker-compose already mounts at /auth.

## Why signed URLs and not a public bucket

The browser fetches poster art directly, and it carries no service-account token —
so the bucket either has to allow anonymous reads, or hand out signed URLs. Public
reads would let anyone with a link see the art without logging in, including the
preview set that has not been approved yet, which would put the images outside the
viewer/admin split entirely. Signing costs nothing extra here: the service account
is a JSON key with a private key, so a URL is signed locally with no API call and
no additional IAM permission.

## Why keys carry a version suffix

`101/cover-a3f9c1d0.webp`, not `101/cover.webp`. Overwriting a fixed key would keep
the URL string identical (see GCS_URL_TTL on the rounding), and the browser would
serve the OLD image from cache for up to the whole window — art regenerated and
approved would appear not to change. A fresh suffix per version means the URL
changes exactly when the bytes change, and never otherwise, which is also what
makes caching safe.
"""

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from .config import GCS_BUCKET, GCS_URL_TTL

logger = logging.getLogger(__name__)

_client = None


def enabled() -> bool:
    """False when no bucket is configured — callers fall back to local files."""
    return bool(GCS_BUCKET)


def _bucket():
    global _client
    if not enabled():
        raise RuntimeError("GCS_BUCKET is not set")
    if _client is None:
        from google.cloud import storage  # imported lazily: only needed with a bucket

        _client = storage.Client()
    return _client.bucket(GCS_BUCKET)


def object_key(story_id: int, stem: str, ext: str = "webp") -> str:
    """A fresh key for one image. The random suffix is the version — see module docstring.

    Live and pending art share one key space on purpose. Putting previews under their
    own prefix reads well until a preview is ACCEPTED: promoting it is a change of
    state, not a move, so the object would keep sitting under `preview/` while being
    the live image — and any later cleanup that deleted that prefix would take the
    live art with it. Which set an object belongs to is `StoryImage.state`, and only
    that.
    """
    return f"{story_id}/{stem}-{secrets.token_hex(4)}.{ext}"


def put(key: str, data: bytes, content_type: str) -> str:
    """Upload bytes; returns the key back for storing on the row."""
    blob = _bucket().blob(key)
    blob.upload_from_string(data, content_type=content_type)
    logger.info("storage: put %s (%d bytes)", key, len(data))
    return key


def get_bytes(key: str) -> bytes | None:
    """Read an object back — used by the zip builder and by image_generator, which
    feeds the live cover back in as a face reference when redrawing a thumbnail."""
    blob = _bucket().blob(key)
    try:
        return blob.download_as_bytes()
    except Exception as exc:  # noqa: BLE001 — a missing reference must not kill a run
        logger.warning("storage: get %s failed: %s", key, exc)
        return None


def delete(key: str) -> None:
    """Delete one object. A key that is already gone is not an error — accept/discard
    must stay idempotent so a retried request cannot fail on the second pass."""
    try:
        _bucket().blob(key).delete()
        logger.info("storage: deleted %s", key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("storage: delete %s failed: %s", key, exc)


def delete_prefix(prefix: str) -> int:
    """Delete everything under a prefix (a story's whole preview set, or a whole
    story on removal). Returns how many objects went."""
    n = 0
    for blob in _client_list(prefix):
        try:
            blob.delete()
            n += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("storage: delete %s failed: %s", blob.name, exc)
    logger.info("storage: deleted %d object(s) under %s", n, prefix)
    return n


def _client_list(prefix: str):
    return _bucket().list_blobs(prefix=prefix)


def signed_url(key: str, ttl: int | None = None) -> str:
    """A time-limited read URL the browser can fetch.

    The deadline is rounded DOWN to a multiple of the TTL rather than being
    `now + ttl`, so repeated calls inside one window produce a byte-identical URL.
    Streamlit reruns on every interaction; without the rounding each rerun would
    mint a new URL and the browser would re-download all three posters (~180 KB)
    every time a button is pressed.
    """
    ttl = ttl or GCS_URL_TTL
    now = int(datetime.now(tz=timezone.utc).timestamp())
    deadline = ((now // ttl) + 2) * ttl  # +2 so a URL minted late in a window still has a full window of life
    return _bucket().blob(key).generate_signed_url(
        version="v4",
        expiration=datetime.fromtimestamp(deadline, tz=timezone.utc),
        method="GET",
    )
