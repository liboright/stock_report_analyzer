"""阶段 3.x 跨年度表格合并 → CSV 落盘的请求/响应模型。

服务于 `POST /companies/{name}/tables/merge` 端点。
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TablesMergeRequest(BaseModel):
    """跨年度表格合并请求。"""

    years: Optional[List[int]] = Field(
        default=None,
        description="要合并的年份;None 表示该公司所有已抽表年份。",
    )
    scope: Literal["all", "8core"] = Field(
        default="all",
        description="'all' 全量合并所有 CSV;'8core' 仅 8 类核心表(预留,本期与 all 等价)。",
    )
    force: bool = Field(
        default=False,
        description="True 时重跑前清空 research_file/table/ 目录。",
    )


class GroupSummary(BaseModel):
    """一个跨年合并分组的摘要。"""

    group_key: str = Field(..., description="分组唯一键 `{source_md_stem}|{sanitized_title}`")
    source_md_stem: str
    sanitized_title: str
    status: Literal["strong", "weak", "unmergeable"]
    years: List[int]
    column_similarity: float = Field(..., ge=0.0, le=1.0)
    row_jaccard: float = Field(..., ge=0.0, le=1.0)
    long_csv: Optional[str] = Field(
        default=None,
        description="长表产物相对 REPORT_DATA_PATH 的 POSIX 路径;weak 组在 skill 跑完后填。",
    )
    wide_csv: Optional[str] = Field(default=None, description="宽表产物相对路径。")
    pending_skill: bool = Field(
        default=False,
        description="True 表示该组归到 stage2_table_merge skill 兜底,产物待 skill 跑完才有。",
    )
    reason: str = Field(default="", description="判定理由(一句话)。")


class TablesMergeResponse(BaseModel):
    """跨年度表格合并响应。"""

    company: str
    years: List[int]
    run_id: Optional[int] = Field(
        default=None,
        description="对应 ReportRun.id;前端订阅 SSE 用。同步快速路径无 run_id。",
    )
    total_csvs: int = Field(..., description="扫描到的 CSV 总数(N 年 × M 表)。")
    total_groups: int
    strong_count: int
    weak_count: int
    unmergeable_count: int
    groups: List[GroupSummary] = Field(default_factory=list)
    duration_ms: int = 0
    status: Literal["queued", "done", "failed"]
    message: str = ""
