"""/settings API（M1 阶段：仅返回当前配置快照，不允许改 API Key）。"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def get_runtime_settings() -> dict:
    s = get_settings()
    return {
        "report_data_path": str(s.REPORT_DATA_PATH),
        "deep_research_path": str(s.DEEP_RESEARCH_PATH),
        "script_path": str(s.SCRIPT_PATH),
        "mapping_path": str(s.MAPPING_PATH),
        "db_path": str(s.DB_PATH),
        "log_dir": str(s.LOG_DIR),
        "anthropic_model": s.ANTHROPIC_MODEL,
        "anthropic_key_set": bool(s.ANTHROPIC_API_KEY) and s.ANTHROPIC_API_KEY != "sk-ant-placeholder-replace-me",
        "mineru_api_base": s.MINERU_API_BASE,
        "mineru_key_set": bool(s.MINERU_API_KEY) and s.MINERU_API_KEY != "placeholder-replace-me",
        "host": s.HOST,
        "port": s.PORT,
        "cors_origins": s.cors_origins_list,
    }
