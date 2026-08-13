import logging

from fastapi import FastAPI

from .api.routes import router
from .api.auth import AuthGuard, router as auth_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

app = FastAPI(title="Novel Gen API")

# Schema is owned by Alembic — `alembic upgrade head` runs on container startup
# (see docker-compose.yml), not create_all here. init_db() is kept in
# app.db.session for tests / non-migration local runs only.

# Login is outside the gate; everything else is behind it. Applying the guard to the
# whole router rather than to each route is deliberate — read and write split exactly
# along HTTP method here, so one dependency states the rule once and a route added
# later cannot forget to carry it.
app.include_router(auth_router, prefix="/api")
app.include_router(router, prefix="/api", dependencies=[AuthGuard])
