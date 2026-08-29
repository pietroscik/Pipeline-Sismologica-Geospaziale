import os
from typing import Final
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from path_utils import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


def _required_env(name: str, *aliases: str) -> str:
    value = next((os.getenv(key) for key in (name, *aliases) if os.getenv(key)), None)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def _required_int_env(name: str) -> int:
    raw = _required_env(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid integer environment variable: {name}={raw!r}"
        ) from exc


DB_HOST: Final[str] = os.getenv("DB_HOST") or os.getenv("POSTGRES_HOST") or "localhost"
DB_PORT: Final[int] = (
    _required_int_env("DB_PORT")
    if os.getenv("DB_PORT")
    else _required_int_env("POSTGRES_PORT")
)
DB_NAME: Final[str] = _required_env("DB_NAME", "POSTGRES_DB")
DB_USER: Final[str] = _required_env("DB_USER", "POSTGRES_USER")
DB_PASSWORD: Final[str] = _required_env("DB_PASSWORD", "POSTGRES_PASSWORD")

SQLALCHEMY_DATABASE_URL = (
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
