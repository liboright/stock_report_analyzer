"""阶段 3.x 跨年度表格合并 → CSV 落盘 service（强路径 + skill 兜底）。

职责：
- 扫描 N 年 `table/{源 md stem}/*.csv`（阶段 2.5 产物）
- 按 `(源 md stem, sanitize_title)` 自动分组跨年同名表
- 评估组强度（columns Jaccard + rows Jaccard）→ strong / weak / unmergeable
- strong 组：直接走本 service 拼长表 + 宽表 → `{公司}/md/research_file/table/`
- weak 组：返回 SkillTaskSpec 描述，待 worker 调 `stage2_table_merge` skill 兜底
- 不做：跨公司对比、Parquet、可视化、增量合并

路径规范严格遵守 `docs/artifacts.md §1/§3.x`。
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from app.config import Settings
from app.services.tables_extract_service import (
    _ILLEGAL_CHARS,
    _HEADER_END_MARKER,
    sanitize_title,
)

_log = logging.getLogger(__name__)


# ============================================================
# 异常
# ============================================================


class TableMergeError(Exception):
    """阶段 3.x 表格合并 service 错误基类。"""


class CompanyNotFoundError(TableMergeError):
    def __init__(self, company: str):
        super().__init__(f"公司不存在: {company}")
        self.company = company


class NoTablesToMergeError(TableMergeError):
    """该公司所有年份都没有可抽表产物。"""


# ============================================================
# 路径工具
# ============================================================


def merged_table_dir(company: str, settings: Settings) -> Path:
    """跨年合并产物目录：`{公司}/md/research_file/table/`。"""
    return (
        settings.REPORT_DATA_PATH / company / "md" / "research_file" / "table"
    )


def merged_table_dir_rel(company: str) -> str:
    """DB 存的相对 REPORT_DATA_PATH 的 POSIX 路径。"""
    return f"{company}/md/research_file/table"


def per_year_table_dir(company: str, year: int, settings: Settings) -> Path:
    """单年表格目录：`{公司}/md/clean/{公司}{年份}年年报/table/`（阶段 2.5 产物）。"""
    return (
        settings.REPORT_DATA_PATH
        / company
        / "md"
        / "clean"
        / f"{company}{year}年年报"
        / "table"
    )


# ============================================================
# CSV 元数据头解析（消费阶段 2.5 产物）
# ============================================================


@dataclass
class ParsedTable:
    """从阶段 2.5 落盘的 CSV 反解出的表数据 + 元数据。"""

    csv_path: Path
    source_md_stem: str       # 源 md stem，对应 `table/{stem}/{title}.csv` 的子目录名
    sanitized_title: str      # 来自 CSV 元数据 # title 字段（已经 sanitize 过的最终文件名 stem）
    year: int
    unit: str
    year_mapping: Dict[str, int] = field(default_factory=dict)
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    col_count: int = 0
    row_count: int = 0


def _parse_metadata_block(meta_lines: List[str]) -> Dict[str, str]:
    """把 `# key, value` 行解析为 dict。"""
    out: Dict[str, str] = {}
    for line in meta_lines:
        if not line.startswith("#"):
            continue
        body = line[1:].strip()
        if "," not in body:
            continue
        k, _, v = body.partition(",")
        out[k.strip()] = v.strip()
    return out


def _parse_year_mapping(s: str) -> Dict[str, int]:
    """`current=2025,previous=2024,ybp=2023` → `{"current": 2025, ...}`。"""
    out: Dict[str, int] = {}
    for part in s.split(","):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        try:
            out[k.strip()] = int(v.strip())
        except ValueError:
            continue
    return out


def parse_table_csv(csv_path: Path, *, year: int) -> ParsedTable:
    """解析单张阶段 2.5 CSV。"""
    if not csv_path.exists():
        raise TableMergeError(f"CSV 不存在: {csv_path}")
    text = csv_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    end_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == _HEADER_END_MARKER:
            end_idx = i
            break
    if end_idx is None:
        raise TableMergeError(f"未找到元数据头结束标记: {csv_path}")

    meta = _parse_metadata_block(lines[:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    reader = csv.reader(io.StringIO(body))
    rows_all = [r for r in reader if r != []]  # 去空行
    if not rows_all:
        headers, data_rows = [], []
    else:
        headers, data_rows = rows_all[0], rows_all[1:]

    # 推断 source_md_stem：csv 路径的父目录名 = 源 md stem
    source_md_stem = csv_path.parent.name
    sanitized_title = meta.get("title") or csv_path.stem
    unit = meta.get("unit", "")
    ym = _parse_year_mapping(meta.get("year_mapping", ""))

    return ParsedTable(
        csv_path=csv_path,
        source_md_stem=source_md_stem,
        sanitized_title=sanitized_title,
        year=year,
        unit=unit,
        year_mapping=ym,
        headers=headers,
        rows=data_rows,
        col_count=len(headers),
        row_count=len(data_rows),
    )


# ============================================================
# 扫描
# ============================================================


def list_company_years(company: str, settings: Settings) -> List[int]:
    """扫该公司所有 `table/` 子目录对应的年份（去重 + 排序）。"""
    base = settings.REPORT_DATA_PATH / company / "md" / "clean"
    if not base.is_dir():
        return []
    years: Set[int] = set()
    pat = re.compile(rf"^{re.escape(company)}(\d{{4}})年年报$")
    for p in base.iterdir():
        if not p.is_dir():
            continue
        m = pat.match(p.name)
        if m and (p / "table").is_dir():
            years.add(int(m.group(1)))
    return sorted(years)


def scan_year_tables(
    company: str, year: int, settings: Settings
) -> List[ParsedTable]:
    """扫单年所有 CSV → ParsedTable 列表。空目录返回 []。"""
    root = per_year_table_dir(company, year, settings)
    if not root.is_dir():
        return []
    out: List[ParsedTable] = []
    for csv in sorted(root.rglob("*.csv")):
        try:
            out.append(parse_table_csv(csv, year=year))
        except TableMergeError as exc:
            _log.warning("解析 CSV 失败: %s (%s)", csv, exc)
    return out


# ============================================================
# 分组
# ============================================================


def group_key_of(t: ParsedTable) -> str:
    """分组 key：`{source_md_stem}|{sanitized_title}`。"""
    return f"{t.source_md_stem}|{t.sanitized_title}"


@dataclass
class TableGroup:
    group_key: str
    source_md_stem: str
    sanitized_title: str
    tables: List[ParsedTable] = field(default_factory=list)

    @property
    def years(self) -> List[int]:
        return sorted({t.year for t in self.tables})


def group_across_years(tables: Sequence[ParsedTable]) -> List[TableGroup]:
    """按 (source_md_stem, sanitized_title) 聚合跨年同名表。"""
    buckets: Dict[str, TableGroup] = {}
    for t in tables:
        k = group_key_of(t)
        if k not in buckets:
            buckets[k] = TableGroup(
                group_key=k,
                source_md_stem=t.source_md_stem,
                sanitized_title=t.sanitized_title,
            )
        buckets[k].tables.append(t)
    # 组内按年排序
    for g in buckets.values():
        g.tables.sort(key=lambda x: x.year)
    return sorted(buckets.values(), key=lambda g: g.group_key)


# ============================================================
# 强度评估
# ============================================================


# 阈值
COLUMN_SIM_THRESHOLD = 0.8
ROW_JACCARD_THRESHOLD = 0.5
UNMERGEABLE_MIN_YEARS = 2  # 至少 N 年同表才进入合并池


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    if not u:
        return 0.0
    return len(a & b) / len(u)


def _first_col_values(t: ParsedTable) -> Set[str]:
    """第 1 列非空值集合（行索引），空值 / 占位 '-' 排除。"""
    out: Set[str] = set()
    for r in t.rows:
        if not r:
            continue
        v = (r[0] or "").strip()
        if v and v not in {"-", "—", "/"}:
            out.add(v)
    return out


def _headers_set(t: ParsedTable) -> Set[str]:
    return {h.strip() for h in t.headers if h and h.strip()}


def _pairwise_jaccard(sets: List[Set[str]]) -> float:
    """N 个集合两两 Jaccard 的平均。N<2 → 0.0。"""
    if len(sets) < 2:
        return 0.0
    pairs = 0
    s = 0.0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            s += _jaccard(sets[i], sets[j])
            pairs += 1
    return s / pairs


@dataclass
class GroupVerdict:
    group_key: str
    source_md_stem: str
    sanitized_title: str
    status: str  # 'strong' / 'weak' / 'unmergeable'
    years: List[int]
    column_similarity: float
    row_jaccard: float
    reason: str = ""


def assess_group(group: TableGroup) -> GroupVerdict:
    years = group.years
    if len(years) < UNMERGEABLE_MIN_YEARS:
        return GroupVerdict(
            group_key=group.group_key,
            source_md_stem=group.source_md_stem,
            sanitized_title=group.sanitized_title,
            status="unmergeable",
            years=years,
            column_similarity=0.0,
            row_jaccard=0.0,
            reason=f"仅 {len(years)} 年，< {UNMERGEABLE_MIN_YEARS} 年阈值",
        )
    col_sets = [_headers_set(t) for t in group.tables]
    row_sets = [_first_col_values(t) for t in group.tables]
    col_sim = _pairwise_jaccard(col_sets)
    row_jac = _pairwise_jaccard(row_sets)
    if col_sim >= COLUMN_SIM_THRESHOLD and row_jac >= ROW_JACCARD_THRESHOLD:
        status, reason = "strong", "列+行均高 Jaccard，可程序化合并"
    else:
        weak_reasons = []
        if col_sim < COLUMN_SIM_THRESHOLD:
            weak_reasons.append(f"列相似度 {col_sim:.2f}<{COLUMN_SIM_THRESHOLD}")
        if row_jac < ROW_JACCARD_THRESHOLD:
            weak_reasons.append(f"行 Jaccard {row_jac:.2f}<{ROW_JACCARD_THRESHOLD}")
        status, reason = "weak", "；".join(weak_reasons) or "列/行弱一致，走 skill 兜底"
    return GroupVerdict(
        group_key=group.group_key,
        source_md_stem=group.source_md_stem,
        sanitized_title=group.sanitized_title,
        status=status,
        years=years,
        column_similarity=round(col_sim, 4),
        row_jaccard=round(row_jac, 4),
        reason=reason,
    )


# ============================================================
# 强路径：长表 + 宽表
# ============================================================


# 长表统一列（与下游 pandas / SQL 兼容）
LONG_COLUMNS: List[str] = ["_row_type", "year", "source_md_stem", "subject", "metric", "value", "unit"]


def _is_section_header(row: List[str]) -> bool:
    """第 1 列非空、其余列全空 → 分节标题（如 "按产品档次,,,,,,"）。"""
    if not row or not (row[0] or "").strip():
        return False
    return all(not (c or "").strip() for c in row[1:])


def _classify_table_kind(t: ParsedTable) -> str:
    """判定宽表是"单指标多年"还是"多指标同年"。
    启发式：headers 中若含 "年" / "本期" / "上期" / "比上年" → 多指标同年；
    否则视作单指标多年（headers 即"指标名"）。
    """
    joined = " ".join(t.headers)
    multi_markers = ["年", "本期", "上期", "比上年", "YoY", "yoy"]
    if any(m in joined for m in multi_markers):
        return "multi_metric"
    return "single_metric"


def _row_to_long_records(
    t: ParsedTable, row: List[str], is_section: bool
) -> List[Dict[str, str]]:
    """一行 → 一组 long 记录。"""
    common = {
        "year": str(t.year),
        "source_md_stem": t.source_md_stem,
        "unit": t.unit,
    }
    if is_section:
        return [
            {
                **common,
                "_row_type": "section_header",
                "subject": (row[0] or "").strip(),
                "metric": "",
                "value": "",
            }
        ]
    subject = (row[0] or "").strip()
    out: List[Dict[str, str]] = []
    for ci in range(1, len(t.headers)):
        metric = t.headers[ci] if ci < len(t.headers) else ""
        if not metric.strip():
            continue
        value = (row[ci] if ci < len(row) else "").strip()
        out.append(
            {
                **common,
                "_row_type": "data",
                "subject": subject,
                "metric": metric.strip(),
                "value": value,
            }
        )
    return out


def build_long_records(group: TableGroup) -> List[Dict[str, str]]:
    """把整组（跨年）转成 long records 列表（已含 year, subject, metric, value, unit）。"""
    records: List[Dict[str, str]] = []
    for t in group.tables:
        for row in t.rows:
            records.extend(_row_to_long_records(t, row, _is_section_header(row)))
    return records


def build_wide_records(
    group: TableGroup,
) -> Tuple[List[str], List[List[str]]]:
    """把整组转成宽表（subject 行索引，列=metric_year）。
    返回 (headers(含 'subject' 列), data_rows)。
    """
    # 收集所有 (metric, year) 列；保持插入顺序
    columns: List[Tuple[str, int]] = []
    seen: Set[Tuple[str, int]] = set()
    for t in group.tables:
        for h in t.headers[1:]:
            h = h.strip()
            if not h:
                continue
            key = (h, t.year)
            if key not in seen:
                seen.add(key)
                columns.append(key)

    # 收集所有 subject
    subject_rows: Dict[str, Dict[Tuple[str, int], str]] = {}
    section_rows: Dict[str, None] = {}
    for t in group.tables:
        for row in t.rows:
            if _is_section_header(row):
                sec = (row[0] or "").strip()
                if sec:
                    section_rows[sec] = None
                continue
            subj = (row[0] or "").strip()
            if not subj:
                continue
            bucket = subject_rows.setdefault(subj, {})
            for ci in range(1, len(t.headers)):
                metric = t.headers[ci].strip() if ci < len(t.headers) else ""
                if not metric:
                    continue
                bucket[(metric, t.year)] = (row[ci] if ci < len(row) else "").strip()

    # 行顺序：先 section_headers（按出现顺序），再 data subjects（按字典序）
    all_data_subjects = sorted(subject_rows.keys())
    section_order = [s for s in section_rows if s not in subject_rows]
    ordered_subjects = section_order + all_data_subjects

    headers = ["subject"] + [f"{m}_{y}" for m, y in columns]
    rows: List[List[str]] = []
    for subj in ordered_subjects:
        if subj in section_rows:
            rows.append([subj] + ["" for _ in columns])
        else:
            bucket = subject_rows[subj]
            rows.append(
                [subj] + [bucket.get(col, "") for col in columns]
            )
    return headers, rows


def _format_long_csv(records: List[Dict[str, str]], group: TableGroup) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(LONG_COLUMNS)
    # 稳定排序：year desc, subject, metric
    for r in sorted(
        records,
        key=lambda r: (-int(r["year"]), r["subject"], r["metric"]),
    ):
        writer.writerow([r.get(c, "") for c in LONG_COLUMNS])
    return buf.getvalue()


def _format_wide_csv(headers: List[str], rows: List[List[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(headers)
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def _group_meta_header(
    group: TableGroup, verdict: GroupVerdict, kind: str
) -> str:
    """每个产物 CSV 顶部的元数据头。"""
    cols: List[Tuple[str, str]] = [
        ("source_md_stem", group.source_md_stem),
        ("sanitized_title", group.sanitized_title),
        ("group_key", group.group_key),
        ("kind", kind),  # 'long' / 'wide'
        ("years", ",".join(str(y) for y in verdict.years)),
        ("column_similarity", f"{verdict.column_similarity:.4f}"),
        ("row_jaccard", f"{verdict.row_jaccard:.4f}"),
        ("merged_at", datetime.now().isoformat(timespec="seconds")),
    ]
    lines = [f"# {k}, {v}" for k, v in cols]
    lines.append(_HEADER_END_MARKER)
    lines.append("")
    return "\n".join(lines)


def _safe_stem(s: str, max_len: int = 100) -> str:
    s = _ILLEGAL_CHARS.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len].rstrip() or "未命名"


@dataclass
class MergeResult:
    group_key: str
    long_csv: Path
    wide_csv: Path


def merge_strong_group(
    group: TableGroup,
    verdict: GroupVerdict,
    company: str,
    settings: Settings,
) -> MergeResult:
    """strong 组：写 long + wide CSV 到 research_file/table/，返回路径。"""
    out_dir = merged_table_dir(company, settings)
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = _safe_stem(f"{group.source_md_stem}_{group.sanitized_title}")
    long_path = out_dir / f"{stem}_long.csv"
    wide_path = out_dir / f"{stem}_wide.csv"

    long_body = _format_long_csv(build_long_records(group), group)
    long_content = _group_meta_header(group, verdict, "long") + long_body + "\n"

    wide_headers, wide_rows = build_wide_records(group)
    wide_body = _format_wide_csv(wide_headers, wide_rows)
    wide_content = _group_meta_header(group, verdict, "wide") + wide_body + "\n"

    long_path.write_text(long_content, encoding="utf-8-sig")
    wide_path.write_text(wide_content, encoding="utf-8-sig")
    return MergeResult(group_key=group.group_key, long_csv=long_path, wide_csv=wide_path)


# ============================================================
# 弱路径：给 worker 拼 skill 任务
# ============================================================


@dataclass
class SkillTaskSpec:
    group_key: str
    source_md_stem: str
    sanitized_title: str
    years: List[int]
    csv_paths: List[str]  # 相对 REPORT_DATA_PATH 的 POSIX


def build_skill_task(group: TableGroup, base: Path) -> SkillTaskSpec:
    rels = [
        str(t.csv_path.relative_to(base)).replace("\\", "/") for t in group.tables
    ]
    return SkillTaskSpec(
        group_key=group.group_key,
        source_md_stem=group.source_md_stem,
        sanitized_title=group.sanitized_title,
        years=group.years,
        csv_paths=rels,
    )


# ============================================================
# 主入口
# ============================================================


@dataclass
class GroupReport:
    """dispatch 结果中给前端看的单组摘要。"""

    group_key: str
    source_md_stem: str
    sanitized_title: str
    status: str
    years: List[int]
    column_similarity: float
    row_jaccard: float
    long_csv: Optional[str] = None
    wide_csv: Optional[str] = None
    pending_skill: bool = False
    reason: str = ""


@dataclass
class DispatchResult:
    company: str
    years: List[int]
    total_csvs: int
    total_groups: int
    strong_count: int
    weak_count: int
    unmergeable_count: int
    groups: List[GroupReport] = field(default_factory=list)
    skill_tasks: List[SkillTaskSpec] = field(default_factory=list)
    duration_ms: int = 0
    status: str = "done"  # 'done' / 'empty' / 'failed'
    message: str = ""


def _to_rel(p: Path, base: Path) -> str:
    try:
        return str(p.relative_to(base)).replace("\\", "/")
    except ValueError:
        return p.as_posix()


def scan_and_dispatch(
    company: str,
    years: Optional[List[int]],
    settings: Settings,
    *,
    force: bool = False,
) -> DispatchResult:
    """主入口：扫 N 年 CSV → 分组 → 评估 → strong 写盘 / weak 返回 spec。

    不依赖 DB：公司存在性、年份过滤由 router 层做。
    """
    started = datetime.now()
    base = settings.REPORT_DATA_PATH
    if years is None:
        years = list_company_years(company, settings)
    if not years:
        return DispatchResult(
            company=company,
            years=[],
            total_csvs=0,
            total_groups=0,
            strong_count=0,
            weak_count=0,
            unmergeable_count=0,
            duration_ms=int((datetime.now() - started).total_seconds() * 1000),
            status="empty",
            message="该公司没有任何已抽表年份",
        )

    if force:
        out_dir = merged_table_dir(company, settings)
        if out_dir.is_dir():
            import shutil

            shutil.rmtree(out_dir, ignore_errors=True)

    out_dir = merged_table_dir(company, settings)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_tables: List[ParsedTable] = []
    for y in years:
        all_tables.extend(scan_year_tables(company, y, settings))
    if not all_tables:
        return DispatchResult(
            company=company,
            years=years,
            total_csvs=0,
            total_groups=0,
            strong_count=0,
            weak_count=0,
            unmergeable_count=0,
            duration_ms=int((datetime.now() - started).total_seconds() * 1000),
            status="empty",
            message="指定年份下没有可抽表产物（请先跑 /tables/extract）",
        )

    groups = group_across_years(all_tables)
    reports: List[GroupReport] = []
    skill_tasks: List[SkillTaskSpec] = []
    strong = weak = unmergeable = 0

    for g in groups:
        v = assess_group(g)
        if v.status == "strong":
            try:
                mr = merge_strong_group(g, v, company, settings)
            except Exception as exc:  # noqa: BLE001
                _log.error("strong 合并失败: %s (%s)", g.group_key, exc)
                reports.append(
                    GroupReport(
                        group_key=g.group_key,
                        source_md_stem=g.source_md_stem,
                        sanitized_title=g.sanitized_title,
                        status="weak",
                        years=v.years,
                        column_similarity=v.column_similarity,
                        row_jaccard=v.row_jaccard,
                        pending_skill=True,
                        reason=f"强路径合并失败: {exc}，降级到 skill",
                    )
                )
                skill_tasks.append(build_skill_task(g, base))
                weak += 1
                continue
            strong += 1
            reports.append(
                GroupReport(
                    group_key=g.group_key,
                    source_md_stem=g.source_md_stem,
                    sanitized_title=g.sanitized_title,
                    status="strong",
                    years=v.years,
                    column_similarity=v.column_similarity,
                    row_jaccard=v.row_jaccard,
                    long_csv=_to_rel(mr.long_csv, base),
                    wide_csv=_to_rel(mr.wide_csv, base),
                    reason=v.reason,
                )
            )
        elif v.status == "weak":
            weak += 1
            reports.append(
                GroupReport(
                    group_key=g.group_key,
                    source_md_stem=g.source_md_stem,
                    sanitized_title=g.sanitized_title,
                    status="weak",
                    years=v.years,
                    column_similarity=v.column_similarity,
                    row_jaccard=v.row_jaccard,
                    pending_skill=True,
                    reason=v.reason,
                )
            )
            skill_tasks.append(build_skill_task(g, base))
        else:
            unmergeable += 1
            reports.append(
                GroupReport(
                    group_key=g.group_key,
                    source_md_stem=g.source_md_stem,
                    sanitized_title=g.sanitized_title,
                    status="unmergeable",
                    years=v.years,
                    column_similarity=v.column_similarity,
                    row_jaccard=v.row_jaccard,
                    reason=v.reason,
                )
            )

    duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    _log.info(
        "跨年合并完成: company=%s years=%s total_csvs=%d total_groups=%d "
        "strong=%d weak=%d unmergeable=%d duration_ms=%d",
        company,
        years,
        len(all_tables),
        len(groups),
        strong,
        weak,
        unmergeable,
        duration_ms,
    )
    return DispatchResult(
        company=company,
        years=years,
        total_csvs=len(all_tables),
        total_groups=len(groups),
        strong_count=strong,
        weak_count=weak,
        unmergeable_count=unmergeable,
        groups=reports,
        skill_tasks=skill_tasks,
        duration_ms=duration_ms,
        status="done",
    )
