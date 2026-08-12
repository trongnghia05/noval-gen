from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..config import DB_URL
from .models import Base

connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
# pool_pre_ping recycles connections Postgres dropped while idle (a long
# background run can sit between DB hits longer than the server keeps a conn).
engine = create_engine(DB_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create all tables directly from the models — schema is normally owned by
    Alembic (`alembic upgrade head`); this is a shortcut for tests and bare local
    runs only, not used by the app at startup."""
    Base.metadata.create_all(engine)
