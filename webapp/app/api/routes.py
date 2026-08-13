"""One router assembled from the five resource routers.

Kept as `app.api.routes:router` so main.py and anything else importing it does not
have to care that the endpoints now live in five files. Order matters only where
paths could shadow each other, and none here do.
"""

from fastapi import APIRouter

from .routers import export, images, run, settings, stories

router = APIRouter()
router.include_router(settings.router)
router.include_router(stories.router)
router.include_router(run.router)
router.include_router(images.router)
router.include_router(export.router)
