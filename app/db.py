"""Kết nối cơ sở dữ liệu. DATABASE_URL lấy từ biến môi trường / .env; mặc định SQLite cho phát triển và kiểm thử."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
DEFAULT_URL = f"sqlite:///{(ROOT / 'data' / 'processed' / 'app.db').as_posix()}"


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or os.getenv('DATABASE_URL', DEFAULT_URL)
    kwargs = {'connect_args': {'check_same_thread': False}} if url.startswith('sqlite') else {'pool_pre_ping': True}
    return create_engine(url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session():
    with SessionLocal() as s:
        yield s
