import logging

from fastapi import FastAPI

from .api.routes import router
from .db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

app = FastAPI(title="Novel Gen API")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.include_router(router, prefix="/api")
