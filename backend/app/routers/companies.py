"""公司 & 上传/下载/解析相关 API。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_session
from app.models import AnnualReport, Company, ReportRun
from app.schemas.annual_report import AnnualReportRead, AnnualReportUploadResponse
from app.schemas.company import CompanyCreate, CompanyDetail, CompanyRead
from app.schemas.file_tree import (
    ChapterFile,
    FileTreeResponse,
    MergedTableFile,
    ResearchFile,
    Section3File,
    TableCsvFile,
)
from app.schemas.pdf_split import SplitPDFResponse
from app.schemas.parse_split import ParseSplitTriggerResponse
from app.schemas.table_merge import (
    TablesMergeRequest,
    TablesMergeResponse,
)
from app.schemas.tables_extract import SectionSummary, TablesExtractResponse
from app.services import (
    chapter_split_service,
    company_search_service,
    pdf_split_service,
    section3_split_service,
    tables_extract_service,
)
from app.workers import parse_split_pipeline, table_merge_pipeline

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=List[CompanyRead])
def list_companies(db: Session = Depends(get_session)) -> List[Company]:
    return db.query(Company).order_by(Company.id.desc()).all()


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
def create_company(payload: CompanyCreate, db: Session = Depends(get_session)) -> Company:
    """新建公司，自动从 mapping.json 查 stock_code（如有）。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="公司名不能为空")

    existing = db.query(Company).filter(Company.name == name).first()
    if existing:
        return existing  # 幂等

    # 自动查 stock_code
    stock_code = payload.stock_code or company_search_service.lookup_stock_code(name)

    company = Company(
        name=name,
        stock_code=stock_code,
        industry=payload.industry,
    )
    db.add(company)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Company).filter(Company.name == name).first()
        if existing:
            return existing
        raise HTTPException(status_code=500, detail="创建公司失败")
    db.refresh(company)
    return company


@router.get("/{name}", response_model=CompanyDetail)
def get_company(name: str, db: Session = Depends(get_session)) -> Company:
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")
    return company


@router.post(
    "/{name}/upload",
    response_model=AnnualReportUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_annual_report(
    name: str,
    file: UploadFile = File(..., description="PDF 文件"),
    year: int = Form(..., ge=1990, le=2100),
    db: Session = Depends(get_session),
) -> AnnualReportUploadResponse:
    """上传 PDF 年报，自动按 SHA-256 去重。"""
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 .pdf 文件")

    # 暂存到 tmp，再走 service 落盘 + 去重
    settings = get_settings()
    tmp_dir = settings.LOG_DIR.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"{company.name}_{year}_{file.filename}"
    try:
        with tmp_path.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        from app.services.pdf_upload_service import upload_pdf

        outcome = upload_pdf(
            db=db,
            company=company,
            year=year,
            src_path=tmp_path,
            original_filename=file.filename,
        )
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    return AnnualReportUploadResponse(
        report=AnnualReportRead.model_validate(outcome.report),
        deduplicated=outcome.deduplicated,
        message=outcome.message,
    )


@router.get("/{name}/reports", response_model=List[AnnualReportRead])
def list_annual_reports(name: str, db: Session = Depends(get_session)) -> List[AnnualReport]:
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")
    return (
        db.query(AnnualReport)
        .filter(AnnualReport.company_id == company.id)
        .order_by(AnnualReport.year.desc())
        .all()
    )


# ---------------- 解析产物文件树（M4）----------------


def _rel_posix(p: Path, base: Path) -> str:
    """返回 p 相对 base 的 POSIX 路径，base 不存在时仍按字符串相对计算。"""
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return p.as_posix()


@router.get(
    "/{name}/reports/{year}/files",
    response_model=FileTreeResponse,
)
def list_parsed_files(
    name: str,
    year: int,
    db: Session = Depends(get_session),
) -> FileTreeResponse:
    """列出 (公司, 年份) 的解析产物文件树，供前端解析页预览。

    四个根（相对 ``REPORT_DATA_PATH``，按 docs/artifacts.md 规范）：
    - ``md/clean/{公司}{年份}年年报/by_section/*.md`` — 章节（阶段 2.3）
    - ``md/clean/{公司}{年份}年年报/管理层讨论/*.md`` — 第三节 H2 拆分（阶段 2.4）
    - ``md/research_file/*.md`` — 阶段 3 业务概况 / 行业分析（不分年）
    - ``md/clean/{公司}{年份}年年报/table/*.csv`` + ``table/其他/*.csv`` — 阶段 2.5 抽表
    """
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")

    base = get_settings().REPORT_DATA_PATH
    chapters: list[ChapterFile] = []
    section3: list[Section3File] = []
    research: list[ResearchFile] = []

    chap_dir = base / name / "md" / "clean" / f"{name}{year}年年报" / "by_section"
    if chap_dir.is_dir():
        for p in sorted(chap_dir.glob("*.md")):
            stem = p.stem
            # 文件名约定 "NN_标题"，取下划线前两位当 section_num
            if len(stem) >= 3 and stem[:2].isdigit() and stem[2] == "_":
                num = stem[:2]
                title = stem[3:]
            else:
                num = ""
                title = stem
            chapters.append(
                ChapterFile(
                    section_num=num,
                    title=title,
                    path=_rel_posix(p, base),
                    subsections=[],
                )
            )

    sec3_dir = base / name / "md" / "clean" / f"{name}{year}年年报" / "管理层讨论"
    if sec3_dir.is_dir():
        for p in sorted(sec3_dir.glob("*.md")):
            section3.append(Section3File(title=p.stem, path=_rel_posix(p, base)))

    research_dir = base / name / "md" / "research_file"
    if research_dir.is_dir():
        # 按文件名识别业务概况 vs 行业分析；详见 docs/artifacts.md §1
        business_prefix = f"{name}_业务概况"
        industry_prefix = f"{name}_行业分析"
        for p in sorted(research_dir.glob("*.md")):
            stem = p.stem
            if stem.startswith(business_prefix):
                kind = "business"
            elif stem.startswith(industry_prefix):
                kind = "industry"
            else:
                kind = "unknown"
            research.append(
                ResearchFile(
                    title=stem,
                    path=_rel_posix(p, base),
                    kind=kind,
                )
            )

    # 阶段 2.5：抽取表格产物（按源 md stem 分子目录，每张表一个 csv）
    tables: list[TableCsvFile] = []
    table_dir = base / name / "md" / "clean" / f"{name}{year}年年报" / "table"
    if table_dir.is_dir():
        for sub in sorted(p for p in table_dir.iterdir() if p.is_dir()):
            for csv in sorted(sub.glob("*.csv")):
                tables.append(
                    TableCsvFile(
                        category=sub.name,
                        name=csv.name,
                        path=_rel_posix(csv, base),
                    )
                )

    # 阶段 3.x：跨年合并表格（{公司}/md/research_file/table/）
    # 一个 group = {group_key}_long.csv + {group_key}_wide.csv，可能只有 long
    merged_tables: list[MergedTableFile] = []
    merged_dir = base / name / "md" / "research_file" / "table"
    if merged_dir.is_dir():
        # 先按 group_key 聚合：key = stem 去 _long/_wide 后缀
        groups: dict[str, dict[str, Path]] = {}
        for csv in merged_dir.glob("*.csv"):
            stem = csv.stem
            if stem.endswith("_long"):
                key = stem[: -len("_long")]
                bucket = groups.setdefault(key, {})
                bucket["long"] = csv
            elif stem.endswith("_wide"):
                key = stem[: -len("_wide")]
                bucket = groups.setdefault(key, {})
                bucket["wide"] = csv
        # 过滤 sidecar 等非 _long/_wide 后缀的（groups 只来自有后缀匹配的文件）
        for key in sorted(groups.keys()):
            bucket = groups[key]
            merged_tables.append(
                MergedTableFile(
                    group_key=key,
                    sanitized_title=key,
                    long_csv=_rel_posix(bucket["long"], base) if "long" in bucket else None,
                    wide_csv=_rel_posix(bucket["wide"], base) if "wide" in bucket else None,
                )
            )

    return FileTreeResponse(
        chapters=chapters,
        section3=section3,
        research=research,
        tables=tables,
        merged_tables=merged_tables,
    )


# ---------------- 解析流水线 ----------------


class ParseTriggerResponse(BaseModel):
    run_id: int
    company: str
    year: int
    status: str
    use_mock: bool
    message: str


# ---------------- 章节切分（M5 新增）----------------


class ChaptersTriggerResponse(BaseModel):
    run_id: int
    company: str
    year: int
    status: str
    annotation_status: Optional[str] = None  # '' / 'annotated' / 'failed'（业务 MD 标注状态）
    message: str


# ---------------- 业务 MD 标注（独立步骤，从 /parse-split 拆出）----------------


class AnnotateTriggerResponse(BaseModel):
    run_id: int
    company: str
    year: int
    status: str
    annotation_status: Optional[str] = None
    message: str


@router.post(
    "/{name}/annotate",
    response_model=AnnotateTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_annotate(
    bg: BackgroundTasks,
    name: str,
    year: int = Query(..., ge=1990, le=2100),
    force: bool = Query(
        False,
        description="True：强制重跑标注（重置 annotation_status 后再跑）",
    ),
    db: Session = Depends(get_session),
) -> AnnotateTriggerResponse:
    """业务报告 MD 标题标注（从 /parse-split 拆出的独立步骤）。

    前置条件：业务报告 MD 已生成（``business_md_path`` 非空）。
    产物：原地改写 ``raw/业务报告/{公司}{年份}年年度报告/..._业务报告.md``。
    DB 字段：``annotation_status='annotated'`` + ``annotated_at``。
    """
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")

    report = (
        db.query(AnnualReport)
        .filter(AnnualReport.company_id == company.id, AnnualReport.year == year)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail=f"{year} 年年报未上传")
    if not report.business_md_path:
        raise HTTPException(
            status_code=409,
            detail="业务报告 MD 未生成，请先 POST /split-pdf + /parse-split",
        )

    if force:
        report.annotation_status = None
        report.annotated_at = None
        db.commit()
        db.refresh(report)

    run = ReportRun(
        company_id=company.id,
        year=year,
        template="annotate_pipeline",
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    from app.workers import annotate_pipeline

    bg.add_task(
        annotate_pipeline.run_annotate_pipeline,
        run_id=run.id,
        company_id=company.id,
        year=year,
    )

    return AnnotateTriggerResponse(
        run_id=run.id,
        company=name,
        year=year,
        status="queued",
        annotation_status=report.annotation_status,
        message=f"已入队，订阅 /tasks/{run.id}/stream 获取进度",
    )


@router.post(
    "/{name}/chapters",
    response_model=ChaptersTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_chapters(
    bg: BackgroundTasks,
    name: str,
    year: int = Query(..., ge=1990, le=2100),
    force: bool = Query(
        False,
        description=(
            "True：强制重跑（先清空 by_section/ 和 管理层讨论/{year}/ 目录）。"
            "用于「再点击重新执行」场景。"
        ),
    ),
    db: Session = Depends(get_session),
) -> ChaptersTriggerResponse:
    """触发章节切分 + 第三节 H2 拆分（依赖业务报告 MD 已就绪）。

    前置条件：
      - 年报已上传（``annual_report`` 存在）
      - 业务报告 MD 已生成（``business_md_path`` 非空）

    产物落点（按 docs/artifacts.md 规范）：
      - ``md/clean/{公司}{年份}年年报/by_section/*.md`` — 章节（阶段 2.3）
      - ``md/clean/{公司}{年份}年年报/管理层讨论/{年份}/*.md`` — 第三节 H2 拆分（阶段 2.4）

    返回 run_id，前端用 GET /tasks/{run_id}/stream 订阅 SSE 进度。
    """
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")

    report = (
        db.query(AnnualReport)
        .filter(AnnualReport.company_id == company.id, AnnualReport.year == year)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail=f"{year} 年年报未上传，请先 POST /upload")
    if not report.business_md_path:
        raise HTTPException(
            status_code=409,
            detail=f"业务报告 MD 未生成，请先 POST /split-pdf + /parse-split",
        )
    if not report.finance_md_path:
        raise HTTPException(
            status_code=409,
            detail=(
                "财务报告 MD 未生成，请先 POST /split-pdf + /parse-split。"
                "（财务 MD 复制到 by_section/ 是 /chapters 的 sub-step）"
            ),
        )

    # force=True：清空 by_section/ 和 管理层讨论/{year}/ 目录
    # 注：by_section 是覆盖写（安全），但 section3 用 before/after 快照判增量，
    # 旧文件残留会导致返回的新文件列表为空；管理层讨论目录必须清
    if force:
        import shutil

        settings = get_settings()
        base = settings.REPORT_DATA_PATH
        clean_root = base / name / "md" / "clean" / f"{name}{year}年年报"
        for sub in ("by_section", "管理层讨论"):
            d = clean_root / sub
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)

    run = ReportRun(
        company_id=company.id,
        year=year,
        template="chapters_pipeline",
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    from app.workers import chapters_pipeline

    bg.add_task(
        chapters_pipeline.run_chapters_pipeline,
        run_id=run.id,
        company_id=company.id,
        year=year,
    )

    return ChaptersTriggerResponse(
        run_id=run.id,
        company=name,
        year=year,
        status="queued",
        annotation_status=report.annotation_status,
        message=f"已入队，订阅 /tasks/{run.id}/stream 获取进度",
    )


@router.post("/{name}/split-pdf", response_model=SplitPDFResponse)
def trigger_split_pdf(
    name: str,
    year: int = Query(..., ge=1990, le=2100),
    force: bool = Query(
        False,
        description="True：忽略已有产物，强制重新切分（再点按钮场景）",
    ),
    db: Session = Depends(get_session),
) -> SplitPDFResponse:
    """按章节切分年报 PDF：最后一节'财务报告'单独成文。

    同步执行（PDF < 10MB，几秒完事）。
    产物：
      - RAW_BASE_PATH/{公司}/pdf/split/{原名}_财务报告.pdf
      - RAW_BASE_PATH/{公司}/pdf/split/{原名}_业务报告.pdf

    用途：解决 MinerU API 200 页限制（年报多在 200+ 页，切完后两份均 < 200 页）。

    注：本端点的实现本身就是覆盖写，force 参数仅用于和其他步骤接口风格一致。
    """
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")

    report = (
        db.query(AnnualReport)
        .filter(AnnualReport.company_id == company.id, AnnualReport.year == year)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail=f"{year} 年年报未上传，请先 POST /upload")

    _ = force  # 本端点天然覆盖，参数仅用于风格一致
    result = pdf_split_service.split_annual_report_pdf(report, name)

    settings = get_settings()
    # split 产物现在按新 base（REPORT_DATA_PATH）落盘，路径字段也按新 base 算相对路径
    base = settings.REPORT_DATA_PATH.resolve()
    rel = lambda p: str(p.resolve().relative_to(base)).replace("\\", "/")

    return SplitPDFResponse(
        company=name,
        year=year,
        finance_pdf=rel(result.finance_pdf_path),
        other_pdf=rel(result.other_pdf_path),
        finance_start_page=result.finance_start_page,
        total_pages=result.total_pages,
        title_text=result.title_text,
    )


@router.post(
    "/{name}/parse-split",
    response_model=ParseSplitTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_parse_split(
    bg: BackgroundTasks,
    name: str,
    year: int = Query(..., ge=1990, le=2100),
    use_mock: bool = Query(False, description="测试用：跳过真实 MinerU，使用内置 mock markdown"),
    include_other_years: bool = Query(
        True,
        description=(
            "True：单次 MinerU 批收该公司所有未完成 (year, kind) PDF（推荐）；"
            "False：只收 year 触发年的（向后兼容旧行为）"
        ),
    ),
    force: bool = Query(
        False,
        description=(
            "True：强制重跑当前 year 的解析（删除已落盘的业务/财务 MD 后再批 MinerU）。"
            "force=True 时自动收敛为只跑当前 year（include_other_years=False），避免连带影响其他年份。"
        ),
    ),
    db: Session = Depends(get_session),
) -> ParseSplitTriggerResponse:
    """「切分 + 解析」组合端点：先校验已切分，再单次 MinerU 批提交 N 份 PDF。

    前置条件：年报已通过 `POST /split-pdf` 切分（`split_status='done'`，且
    `finance_pdf_path` + `other_pdf_path` 都有值），否则 **409** 引导先调 /split-pdf。

    行为（`include_other_years=True` 默认）：
      - 扫该公司所有 `split_status='done'` 且 MD 缺失的 (year, kind)
      - 单次 `POST /v4/file-urls/batch` 提交 N 份 PDF
      - 单 batch_id 轮询，N 个 zip 一次性回拉
      - 业务 MD 走 ContextAwareHeadingAnnotator 标注；财务 MD 原样保留
      - **不**跑章节切分（step2）、不跑 section3 拆分（step3）

    返回 run_id，前端用 `GET /tasks/{run_id}/stream` 订阅 SSE 进度。
    SSE 事件 `payload.file` 字段标识具体文件（`{year}_{kind}`）。

    断点续跑：用 `output_md_path.exists()` 兜底，MD 已落盘的文件不重解析。
    """
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")

    report = (
        db.query(AnnualReport)
        .filter(AnnualReport.company_id == company.id, AnnualReport.year == year)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail=f"{year} 年年报未上传，请先 POST /upload")

    # 前置条件：split_status='done' 或两个 split 路径都存在
    split_ok = (
        report.split_status == "done"
        and report.finance_pdf_path
        and report.other_pdf_path
    )
    if not split_ok:
        raise HTTPException(
            status_code=409,
            detail=(
                f"年报未切分（split_status={report.split_status}），"
                f"请先 POST /split-pdf"
            ),
        )

    # force=True：先清掉当前 year 的两份 MD，让 pipeline 重新提交 MinerU
    # 同时强制 include_other_years=False，避免连带触发其他年份
    if force:
        include_other_years = False
        settings = get_settings()
        base = settings.REPORT_DATA_PATH
        for rel in (report.business_md_path, report.finance_md_path):
            if not rel:
                continue
            p = base / rel
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        # 重置 DB 字段，让 worker 重新写回路径与状态
        report.business_md_path = None
        report.finance_md_path = None
        report.parse_split_status = "pending"
        db.commit()
        db.refresh(report)

    # 新建 ReportRun 记录（status=queued）
    run = ReportRun(
        company_id=company.id,
        year=year,
        template="parse_split",
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # BackgroundTask 启动
    bg.add_task(
        parse_split_pipeline.run_parse_split_pipeline,
        run_id=run.id,
        company_id=company.id,
        year=year,
        use_mock=use_mock,
        include_other_years=include_other_years,
    )

    return ParseSplitTriggerResponse(
        run_id=run.id,
        company=name,
        year=year,
        status="queued",
        use_mock=use_mock,
        business_pdf=report.other_pdf_path,
        finance_pdf=report.finance_pdf_path,
        annotation_status=report.annotation_status,
        message=f"已入队，订阅 /tasks/{run.id}/stream 获取进度",
    )


@router.post(
    "/{name}/tables/extract",
    response_model=TablesExtractResponse,
)
def trigger_tables_extract(
    name: str,
    year: int = Query(..., ge=1990, le=2100),
    force: bool = Query(
        False,
        description=(
            "True：强制重跑（先清空 table/ 目录）。CSV 写入是 append 模式，"
            "重跑必须清目录，否则会出现重复行。"
        ),
    ),
    db: Session = Depends(get_session),
) -> TablesExtractResponse:
    """阶段 2.5 表格抽取 → CSV 落盘（同步）。

    输入：`md/clean/{公司}{年份}年年报/管理层讨论/*.md`（阶段 2.4 产物）
    输出：`md/clean/{公司}{年份}年年报/table/{源 md stem}/*.csv`（一表一文件，按章节分组）

    行为：
    - 公司不存在 → 404
    - 年报记录不存在 → 404
    - 管理层讨论目录不存在 → 404（提示先跑章节切分）
    - 目录存在但无 md → 200 + total=0（status='empty'）
    - 同步执行（参考 /split-pdf 而非 /parse-split），不返回 run_id
    """
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")

    report = (
        db.query(AnnualReport)
        .filter(AnnualReport.company_id == company.id, AnnualReport.year == year)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail=f"{year} 年年报未上传: {name}")

    settings = get_settings()
    # 一表一文件，无 append；force 与否结果一致。但残留旧文件可能误导用户，
    # force=True 时清空 table/ 目录，避免上一次的章节子目录残留。
    if force:
        import shutil

        table_dir = settings.REPORT_DATA_PATH / tables_extract_service.table_dir_rel(
            name, year
        )
        if table_dir.is_dir():
            shutil.rmtree(table_dir, ignore_errors=True)
    try:
        outcome = tables_extract_service.extract_tables_to_csv(
            settings=settings, company=name, year=year,
        )
    except tables_extract_service.MdSectionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except tables_extract_service.TablesExtractError as exc:
        raise HTTPException(status_code=500, detail=f"表格抽取失败: {exc}")

    # 落库
    report.extract_tables_status = outcome.status
    report.tables_extracted_at = datetime.now()
    report.tables_dir_path = tables_extract_service.table_dir_rel(name, year)
    db.commit()

    # 按源 md stem 排序输出（与目录名一致，方便前端展示）
    sections = [
        SectionSummary(section=stem, count=cnt)
        for stem, cnt in sorted(outcome.sections.items())
    ]

    return TablesExtractResponse(
        company=name,
        year=year,
        total=outcome.total,
        sections=sections,
        csv_paths=outcome.csv_paths,
        duration_ms=outcome.duration_ms,
        extract_tables_status=outcome.status,
        message=("管理层讨论目录无 md 输入" if outcome.status == "empty" else ""),
    )


# ---------------- 跨年表格合并（阶段 3.x）----------------


@router.post(
    "/{name}/tables/merge",
    response_model=TablesMergeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_tables_merge(
    name: str,
    payload: TablesMergeRequest,
    bg: BackgroundTasks,
    db: Session = Depends(get_session),
) -> TablesMergeResponse:
    """阶段 3.x 跨年度表格合并（异步）。

    行为：
      - 公司不存在 → 404
      - 入队 ReportRun(template='table_merge')，bg 跑 ``table_merge_pipeline.run_table_merge_pipeline``
      - 返回 202 + queued 状态的 TablesMergeResponse（groups 全 0，前端订阅 SSE 拿汇总）

    前端订阅：`GET /tasks/{run_id}/stream`。
    终态汇总查询：从 sidecar ``research_file/table/.merge_run_{run_id}.json`` 读，或从 SSE last_event.payload 拿。
    """
    company = db.query(Company).filter(Company.name == name).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"公司不存在: {name}")

    run = ReportRun(
        company_id=company.id,
        year=None,  # 跨年
        template="table_merge",
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    years_list = list(payload.years) if payload.years else None
    bg.add_task(
        table_merge_pipeline.run_table_merge_pipeline,
        run_id=run.id,
        company_id=company.id,
        years=years_list,
        force=payload.force,
        scope=payload.scope,
    )

    return TablesMergeResponse(
        company=name,
        years=years_list or [],
        run_id=run.id,
        total_csvs=0,
        total_groups=0,
        strong_count=0,
        weak_count=0,
        unmergeable_count=0,
        groups=[],
        duration_ms=0,
        status="queued",
        message=(
            f"已入队，订阅 /tasks/{run.id}/stream 获取进度"
            + ("（force=True）" if payload.force else "")
        ),
    )
