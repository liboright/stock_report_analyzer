"""FastAPI 依赖注入。"""
from app.db.session import get_session

__all__ = ["get_session"]
