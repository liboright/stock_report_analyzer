"""报告生成 & 阅读相关 API。"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_session
from app.models import Company, ReportRun
from app.schemas.report import ReportContent, ReportRunRead
from app.workers import report_pipeline, skill_pipeline

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/by-company/{name}", response_model=List[ReportRunRead])
def list_runs_by_company(name: str, db: Session = Depends(get_session)) -> List[ReportRun]:
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")
    return (
        db.query(ReportRun)
        .filter(ReportRun.company_id == company.id)
        .order_by(ReportRun.id.desc())
        .all()
    )


@router.get("/{run_id}", response_model=ReportRunRead)
def get_run(run_id: int, db: Session = Depends(get_session)) -> List[ReportRun]:
    run = db.query(ReportRun).filter(ReportRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
    return run


# ---------------- M3：报告生成（subprocess 调 claude skill）----------------


class GenerateRequest(BaseModel):
    company: str = Field(..., min_length=1, description="公司中文名")
    year: Optional[int] = Field(None, description="目标年份；None=latest")
    skill: str = Field(
        default="stage1_business_understanding",
        description="要跑的 claude skill（M3 仅支持 stage1）",
    )


class GenerateResponse(BaseModel):
    run_id: int
    company: str
    year: Optional[int] = None
    skill: str
    status: str
    message: str


@router.post(
    "/generate",
    response_model=GenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_generate(
    payload: GenerateRequest,
    bg: BackgroundTasks,
    db: Session = Depends(get_session),
) -> GenerateResponse:
    """触发报告生成（异步）。

    当前 M3 实现：调 ``claude`` CLI subprocess 跑 ``/<skill> <company> [year]``。
    产物落在 ``md/{公司}/output/research_file/{公司}_业务概况.md``（stage1）。
    """
    company = db.query(Company).filter(Company.name == payload.company).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {payload.company}")

    # 新建 ReportRun
    run = ReportRun(
        company_id=company.id,
        year=payload.year,
        template=payload.skill,  # M3 用 skill 名作为 template 标识
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    bg.add_task(
        report_pipeline.run_report_pipeline,
        run_id=run.id,
        company_id=company.id,
        year=payload.year,
        skill=payload.skill,
    )

    return GenerateResponse(
        run_id=run.id,
        company=company.name,
        year=payload.year,
        skill=payload.skill,
        status="queued",
        message=f"已入队，订阅 /tasks/{run.id}/stream 获取进度",
    )


@router.get("/{run_id}/content", response_model=ReportContent)
def get_report_content(run_id: int, db: Session = Depends(get_session)) -> ReportContent:
    """读最终报告 markdown 内容。"""
    run = db.query(ReportRun).filter(ReportRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
    if run.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"报告未完成（status={run.status}），请先订阅 /tasks/{run_id}/stream",
        )
    if not run.final_path:
        raise HTTPException(status_code=500, detail="运行完成但 final_path 为空")

    settings = get_settings()
    # final_path 可能是相对路径（report_pipeline 写的），也可能是绝对路径
    p = Path(run.final_path)
    if not p.is_absolute():
        # 相对路径相对 REPORT_DATA_PATH（report_pipeline 算的）
        p = settings.REPORT_DATA_PATH / p
    if not p.exists():
        raise HTTPException(status_code=500, detail=f"报告文件不存在: {p}")

    content = p.read_text(encoding="utf-8")
    return ReportContent(run_id=run.id, path=str(p), content=content)


# ---------------- 年报下载（Playwright worker，async 自动跑）----------------


class DownloadRequest(BaseModel):
    company: str = Field(..., min_length=1, description="公司中文名")
    years: List[int] = Field(..., min_length=1, max_length=5, description="要下载的年份（升序或乱序均可）")


class DownloadResponse(BaseModel):
    run_id: int
    company: str
    years: List[int]
    status: str
    message: str


@router.post(
    "/download",
    response_model=DownloadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_download(
    payload: DownloadRequest,
    bg: BackgroundTasks,
    db: Session = Depends(get_session),
) -> DownloadResponse:
    """触发年报下载（异步）。

    后台 worker 会用 Playwright Python 跑 SZSE/SSE 流程，下载完成后
    复用 pdf_upload_service.upload_pdf 写 annual_report 表。

    订阅 /tasks/{run_id}/stream 拿实时进度。
    """
    company = db.query(Company).filter(Company.name == payload.company).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {payload.company}")
    if not company.stock_code:
        raise HTTPException(
            status_code=400,
            detail=f"公司 {payload.company} 缺 stock_code，请先在「搜索/上传」页补全",
        )
    for y in payload.years:
        if not (1990 <= y <= 2100):
            raise HTTPException(status_code=400, detail=f"非法年份: {y}")

    sorted_years = sorted(set(payload.years), reverse=True)
    run = ReportRun(
        company_id=company.id,
        year=sorted_years[0],  # 最新年
        template="annual_report_download",
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    bg.add_task(
        skill_pipeline.run_skill_pipeline,
        run_id=run.id,
        company_id=company.id,
        years=sorted_years,
    )

    return DownloadResponse(
        run_id=run.id,
        company=company.name,
        years=sorted_years,
        status="queued",
        message=f"已入队，订阅 /tasks/{run.id}/stream 获取进度",
    )
