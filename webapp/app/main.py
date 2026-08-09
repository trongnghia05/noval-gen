import logging

from fastapi import FastAPI

from .api.routes import router

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

app = FastAPI(title="Novel Gen API")

# Schema is owned by Alembic — `alembic upgrade head` runs on container startup
# (see docker-compose.yml), not create_all here. init_db() is kept in
# app.db.session for tests / non-migration local runs only.

app.include_router(router, prefix="/api")
