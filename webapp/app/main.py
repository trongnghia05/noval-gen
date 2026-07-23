from fastapi import FastAPI

from .api.routes import router
from .db.session import init_db

app = FastAPI(title="Novel Gen API")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


app.include_router(router, prefix="/api")
