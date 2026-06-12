"""应用配置：读 .env，启动时把外部代码路径注入 sys.path。

设计要点：
- 单 Settings 实例（lru_cache），避免重复 IO
- 关键路径用 Path 暴露，方便下游 service 使用
- lifespan 钩子负责把 DEEP_RESEARCH_PATH / SCRIPT_PATH 加入 sys.path
  （MinerU 解析器已内嵌到 app.services.mineru_parser，不再需要外部路径）
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，从 .env / 环境变量加载。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 路径（统一单棵树：REPORT_DATA_PATH 为唯一根）----
    REPORT_DATA_PATH: Path = Path("D:/quant/report_data")
    DEEP_RESEARCH_PATH: Path = Path("D:/quant/deep-research-report")
    SCRIPT_PATH: Path = Path("D:/quant/report_analyzer/scripts")
    MAPPING_PATH: Path = Path("D:/quant/report_data/mapping.json")
    DB_PATH: Path = Path("D:/quant/report_data/.claude_state/state.db")
    LOG_DIR: Path = Path("D:/quant/report_data/.claude_state/logs")

    # ---- LLM ----
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"

    # ---- MinerU ----
    MINERU_API_KEY: str = ""
    MINERU_API_BASE: str = "https://mineru.net/api/v4"

    # ---- 服务 ----
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- 派生 ----
    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def db_url(self) -> str:
        """SQLite 路径必须用三斜杠 + 绝对路径。"""
        p = Path(self.DB_PATH).resolve()
        return f"sqlite:///{p.as_posix()}"

    def ensure_runtime_dirs(self) -> None:
        """建好 runtime 需要的目录。幂等。"""
        for d in [self.DB_PATH.parent, self.LOG_DIR, self.REPORT_DATA_PATH]:
            d.mkdir(parents=True, exist_ok=True)

    def inject_external_paths(self) -> None:
        """把外部工具路径插入 sys.path，供 service import 用。

        当前依赖：
        - DEEP_RESEARCH_PATH：section3_split_service 需要 annual_report_reader
        - SCRIPT_PATH：section3_split_service 动态加载 split_section3.py
        （MinerU 解析器已内嵌到 app.services.mineru_parser，不再需要外部路径。）
        """
        for p in [self.DEEP_RESEARCH_PATH, self.SCRIPT_PATH]:
            sp = str(p)
            if sp not in sys.path:
                sys.path.insert(0, sp)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
