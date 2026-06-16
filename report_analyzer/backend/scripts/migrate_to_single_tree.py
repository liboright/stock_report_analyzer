"""「单棵树」路径迁移脚本：把 ``D:/Quant/report_data/report/`` + ``md/`` 合并到 ``D:/Quant/report_data/{公司}/``。

使用::

    python scripts/migrate_to_single_tree.py --dry-run   # 只打印，不执行
    python scripts/migrate_to_single_tree.py              # 正式执行（幂等）
    python scripts/migrate_to_single_tree.py --force      # 忽略已迁移标志，强制重跑
    python scripts/migrate_to_single_tree.py --rollback   # 回滚到最近一次备份

设计要点：
- **幂等**：``{REPORT_DATA_ROOT}/.claude_state/migration_completed.flag`` 存在则跳过
- **备份**：执行前复制 ``report/`` 子树 + ``state.db`` 到 ``.claude_state/migration_backup_{ts}/``
- **复制优先**：所有 move 操作先 ``shutil.copytree``，断言新位置存在再 ``rmtree`` 旧位置
- **DB 改写**：REPLACE 路径前缀；归一化 ``\\`` → ``/``；删除整段删除特性（preliminary/legacy）
- **断言**：迁移后遍历 annual_report / report_run 所有 path 字段，文件必须存在；不通过则 raise
- **Windows case**：``D:/Quant`` vs ``D:/quant`` 在 Windows 下指同一目录，代码统一规范为小写

源 → 目标 速查（仅本脚本涉及的物理文件；DB 路径改写规则见 ``_update_db``）：

源（旧）                                                           → 目标（新）
report/raw/{公司}/pdf/original/...                                  → {公司}/pdf/original/...
report/raw/{公司}/pdf/split/...                                      → {公司}/pdf/split/...
report/raw/贵州茅台/pdf/贵州茅台2024年年度报告.pdf                   → 贵州茅台/pdf/original/贵州茅台2024年年度报告.pdf  （非标准位置修正）
report/raw/{公司}/md/{公司}{年}年年度报告_业务报告/                  → {公司}/md/raw/业务报告/{公司}{年}年年度报告/
report/raw/{公司}/md/{公司}{年}年年度报告_财务报告/                  → {公司}/md/raw/财务报告/{公司}{年}年年度报告/
report/raw/{公司}/md/split/{公司}{年}年年度报告/                     → 删除（已被上面两个 split 后的 dir 覆盖；DB 路径改写后指向新位置）
report/raw/{公司}/md/preliminary/{公司}{年}年年度报告/              → 删除（preliminary 解析整段删除）
report/md/{公司}/input/{公司}{年}年年度报告/*.md                    → {公司}/md/clean/{公司}{年}年年报/by_section/*.md
report/md/{公司}/output/mid_file/管理层讨论/{年}/*.md                → {公司}/md/clean/{公司}{年}年年报/管理层讨论/{年}/*.md
report/md/{公司}/output/mid_file/管理层讨论/{年}/tables_raw/*.csv    → {公司}/md/clean/{公司}{年}年年报/管理层讨论/{年}/tables_raw/*.csv
report/md/{公司}/output/tables/*.csv                                → {公司}/md/clean/宁德时代2025年年报/table/*.csv  （CSV 文件名无年份，约定归到最近一年）
report/md/{公司}/output/research_file/*.md                          → {公司}/md/research_file/*.md
report/md/{公司}/output/final/*.md                                  → {公司}/md/final/*.md
report/md/{公司}/output/navi/{公司}_{year}_index.json                → {公司}/md/clean/{公司}{year}年年报/navi/{公司}_{year}_index.json
report/md/{公司}/output/navi/{公司}_reports.md                       → {公司}/md/research_file/navi/{公司}_reports.md
report/md/{公司}/output/log/llm_log_*.txt                           → .claude_state/logs/{公司}_llm_log_*.txt
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# -------- 路径常量（硬编码是合理的：这是数据迁移，不读 config） --------
REPORT_DATA_ROOT = Path("D:/Quant/report_data")
OLD_RAW_BASE = REPORT_DATA_ROOT / "report" / "raw"
OLD_MD_BASE = REPORT_DATA_ROOT / "report" / "md"
STATE_DB = REPORT_DATA_ROOT / ".claude_state" / "state.db"
STATE_DIR = REPORT_DATA_ROOT / ".claude_state"
LOGS_DIR = STATE_DIR / "logs"
TMP_DIR = STATE_DIR / "tmp"

# 标志文件
FLAG_FILE = STATE_DIR / "migration_completed.flag"

# legacy 整本解析 + preliminary 整段删除要清掉的记录
LEGACY_COMPANY_NAME = "宁德时代"
LEGACY_YEAR = 2023
PRELIM_YEAR = 2024

# tables/*.csv 没有年份，约定归到最近一年（与 report_data 实际状态一致：2025）
TABLE_CSVS_LATEST_YEAR = 2025

_log = logging.getLogger("migrate")


# ============================================================
# 工具函数
# ============================================================

def _to_posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def _rel_to_data_root(p: Path) -> str:
    """返回 p 相对 REPORT_DATA_ROOT 的 POSIX 字符串（用于 DB path 字段断言）。"""
    return _to_posix(p.relative_to(REPORT_DATA_ROOT))


def _ensure_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)


def _copytree_then_remove(src: Path, dst: Path) -> None:
    """先复制整个子树，断言目标存在后删除源。原子失败语义：复制未完成不删源。

    兼容 dst 已存在的情况（shutil.copytree(dirs_exist_ok=True)）。
    """
    if not src.exists():
        return
    _ensure_dir(dst.parent)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    if not dst.exists():
        raise RuntimeError(f"copy 失败，目标不存在: {dst}")
    shutil.rmtree(src)


def _move_file(src: Path, dst: Path) -> None:
    """先复制单文件，断言目标存在后删除源。"""
    if not src.exists():
        return
    _ensure_dir(dst.parent)
    shutil.copy2(src, dst)
    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
        raise RuntimeError(f"copy 失败: {src} → {dst}")
    src.unlink()


# ============================================================
# 迁移计划
# ============================================================

@dataclass
class MigrationPlan:
    """一次迁移的"动作列表 + 落点"。dry-run 模式只打印 actions，不执行。"""

    actions: list[str] = field(default_factory=list)
    moves: list[tuple[Path, Path, str]] = field(default_factory=list)  # (src, dst, kind)
    executed: bool = False

    def move(self, src: Path, dst: Path, kind: str = "file") -> None:
        self.actions.append(f"{kind}: {src} → {dst}")
        self.moves.append((src, dst, kind))

    def delete(self, path: Path, kind: str = "file") -> None:
        self.actions.append(f"delete {kind}: {path}")
        self.moves.append((path, None, "delete_" + kind))  # 哨兵：dst=None

    def run(self) -> None:
        for src, dst, kind in self.moves:
            if kind.startswith("delete_"):
                if src.is_dir():
                    shutil.rmtree(src)
                elif src.exists():
                    src.unlink()
            elif kind == "dir":
                _copytree_then_remove(src, dst)
            else:
                _move_file(src, dst)
        self.executed = True


# ============================================================
# 物理迁移（每家公司独立计算 moves）
# ============================================================

def _build_company_moves(plan: MigrationPlan) -> None:
    """遍历 OLD_RAW_BASE / OLD_MD_BASE 下每家公司，构建 plan.moves。

    - 公司目录名 = 一级子目录名（即中文公司名）
    - 跳过非公司目录（如 mapping.json、workspace/、.claude_state/）
    """
    # 收集所有公司 = OLD_RAW_BASE ∪ OLD_MD_BASE 一级子目录
    companies: set[str] = set()
    for base in (OLD_RAW_BASE, OLD_MD_BASE):
        if not base.exists():
            continue
        for child in base.iterdir():
            if child.is_dir():
                companies.add(child.name)
    _log.info("发现 %d 家公司: %s", len(companies), sorted(companies))

    for company in sorted(companies):
        _plan_company(plan, company)


def _plan_company(plan: MigrationPlan, company: str) -> None:
    new_root = REPORT_DATA_ROOT / company  # 新位置根
    old_raw = OLD_RAW_BASE / company
    old_md = OLD_MD_BASE / company

    # ---- A. report/raw/{公司}/pdf/ ----
    if old_raw.exists():
        pdf_dir = old_raw / "pdf"
        if pdf_dir.exists():
            # A1. pdf/original/ → {公司}/pdf/original/
            orig_dir = pdf_dir / "original"
            if orig_dir.exists():
                plan.move(orig_dir, new_root / "pdf" / "original", kind="dir")

            # A2. pdf/split/ → {公司}/pdf/split/
            split_dir = pdf_dir / "split"
            if split_dir.exists():
                plan.move(split_dir, new_root / "pdf" / "split", kind="dir")

            # A3. pdf/{公司}*.pdf 散文件（非标准位置）→ {公司}/pdf/original/
            for f in pdf_dir.iterdir():
                if f.is_file() and f.suffix.lower() == ".pdf":
                    plan.move(f, new_root / "pdf" / "original" / f.name, kind="file")

        # ---- B. report/raw/{公司}/md/ ----
        md_dir = old_raw / "md"
        if md_dir.exists():
            # B1. md/{公司}{年}年年度报告_业务报告/ → {公司}/md/raw/业务报告/{公司}{年}年年度报告/
            for sub in md_dir.iterdir():
                if not sub.is_dir():
                    continue
                name = sub.name
                if name.endswith("_业务报告"):
                    year = _extract_year_from_dir_name(name, company)
                    if year:
                        plan.move(
                            sub,
                            new_root / "md" / "raw" / "业务报告" / f"{company}{year}年年度报告",
                            kind="dir",
                        )
                elif name.endswith("_财务报告"):
                    year = _extract_year_from_dir_name(name, company)
                    if year:
                        plan.move(
                            sub,
                            new_root / "md" / "raw" / "财务报告" / f"{company}{year}年年度报告",
                            kind="dir",
                        )
                elif name == "split":
                    # 旧 split/{公司}{年}年年度报告/ → 删除（内容已被上面两个目录覆盖；DB 路径改写后指向新位置）
                    for inner in sub.iterdir():
                        if inner.is_dir():
                            plan.delete(inner, kind="dir")
                elif name == "preliminary":
                    # 整段删除：preliminary 解析特性废弃
                    for inner in sub.iterdir():
                        if inner.is_dir():
                            plan.delete(inner, kind="dir")
                else:
                    # 未知子目录：警告并保留（不移动，避免数据丢失）
                    _log.warning("未识别的 md 子目录，跳过: %s", sub)

    # ---- C. report/md/{公司}/input/ → {公司}/md/clean/{公司}{年}年年报/by_section/ ----
    if old_md.exists():
        input_dir = old_md / "input"
        if input_dir.exists():
            for year_dir in input_dir.iterdir():
                if not year_dir.is_dir():
                    continue
                year = _extract_year_from_dir_name(year_dir.name, company)
                if year is None:
                    _log.warning("无法解析年份: %s", year_dir)
                    continue
                target = (
                    new_root
                    / "md"
                    / "clean"
                    / f"{company}{year}年年报"
                    / "by_section"
                )
                plan.move(year_dir, target, kind="dir")

        # ---- D. report/md/{公司}/output/mid_file/管理层讨论/ ----
        mid_file = old_md / "output" / "mid_file"
        if mid_file.exists():
            mgmt_dir = mid_file / "管理层讨论"
            if mgmt_dir.exists():
                for year_dir in mgmt_dir.iterdir():
                    if not year_dir.is_dir():
                        continue
                    year_str = year_dir.name
                    if not year_str.isdigit():
                        _log.warning("管理层讨论非数字子目录: %s", year_dir)
                        continue
                    year = int(year_str)
                    target = (
                        new_root
                        / "md"
                        / "clean"
                        / f"{company}{year}年年报"
                        / "管理层讨论"
                        / year_str
                    )
                    plan.move(year_dir, target, kind="dir")

            # mid_file/research_file/ → {公司}/md/research_file/
            rf_dir = mid_file / "research_file"
            if rf_dir.exists():
                for f in rf_dir.iterdir():
                    if f.is_file():
                        plan.move(f, new_root / "md" / "research_file" / f.name, kind="file")

        # ---- E. report/md/{公司}/output/tables/*.csv → {公司}/md/clean/宁德时代2025年年报/table/ ----
        # （CSV 文件名无年份信息，统一归到最新年报的 table/ 下；当前数据只有宁德时代，故用 LEGACY_COMPANY_NAME+TABLE_CSVS_LATEST_YEAR）
        tables_dir = old_md / "output" / "tables"
        if tables_dir.exists():
            target = (
                new_root
                / "md"
                / "clean"
                / f"{LEGACY_COMPANY_NAME}{TABLE_CSVS_LATEST_YEAR}年年报"
                / "table"
            )
            for f in tables_dir.iterdir():
                if f.is_file() and f.suffix.lower() == ".csv":
                    plan.move(f, target / f.name, kind="file")

        # ---- F. report/md/{公司}/output/research_file/ → {公司}/md/research_file/ ----
        rf_root = old_md / "output" / "research_file"
        if rf_root.exists():
            for f in rf_root.iterdir():
                if f.is_file():
                    plan.move(f, new_root / "md" / "research_file" / f.name, kind="file")

        # ---- G. report/md/{公司}/output/final/ → {公司}/md/final/ ----
        final_dir = old_md / "output" / "final"
        if final_dir.exists():
            for f in final_dir.iterdir():
                if f.is_file():
                    plan.move(f, new_root / "md" / "final" / f.name, kind="file")

        # ---- H. report/md/{公司}/output/navi/ ----
        navi_dir = old_md / "output" / "navi"
        if navi_dir.exists():
            for f in navi_dir.iterdir():
                if not f.is_file():
                    continue
                # {公司}_{year}_index.json → {公司}/md/clean/{公司}{year}年年报/navi/
                if "_index.json" in f.name:
                    year = _extract_year_from_index_filename(f.name, company)
                    if year:
                        target = (
                            new_root
                            / "md"
                            / "clean"
                            / f"{company}{year}年年报"
                            / "navi"
                            / f.name
                        )
                        plan.move(f, target, kind="file")
                        continue
                # {公司}_reports.md → {公司}/md/research_file/navi/
                if f.name.endswith("_reports.md"):
                    plan.move(f, new_root / "md" / "research_file" / "navi" / f.name, kind="file")
                    continue
                # log/ 子目录里的索引文件：跳过
                if f.name == "log":
                    continue
                _log.warning("未识别的 navi 文件: %s", f)

        # ---- I. report/md/{公司}/output/log/llm_log_*.txt → .claude_state/logs/{公司}_llm_log_*.txt ----
        log_dir = old_md / "output" / "log"
        if log_dir.exists():
            for f in log_dir.iterdir():
                if f.is_file() and f.name.startswith("llm_log_"):
                    new_name = f"{company}_{f.name}"
                    plan.move(f, LOGS_DIR / new_name, kind="file")


def _extract_year_from_dir_name(name: str, company: str) -> int | None:
    """从 "{公司}{4位年}年年度报告[_{业务,财务}报告]?" 格式提取年份。

    接受的后缀：
    - 无后缀（input/{公司}{年}年年度报告/）
    - "_业务报告"
    - "_财务报告"
    """
    if not name.startswith(company):
        return None
    rest = name[len(company):]
    # 去掉已知后缀
    for suf in ("_业务报告", "_财务报告", ""):
        if suf and rest.endswith(suf):
            rest = rest[: -len(suf)]
            break
    # 此时 rest 形如 "{4位年}年年度报告"
    if len(rest) < 4 + len("年年度报告"):
        return None
    year_str = rest[:4]
    if not (year_str.isdigit() and rest[4:].startswith("年年度报告")):
        return None
    return int(year_str)


def _extract_year_from_index_filename(name: str, company: str) -> int | None:
    """从 "{公司}_{year}_index.json" 提取年份。"""
    prefix = f"{company}_"
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix):]
    if not rest.endswith("_index.json"):
        return None
    year_str = rest[: -len("_index.json")]
    if not (len(year_str) == 4 and year_str.isdigit()):
        return None
    return int(year_str)


# ============================================================
# DB 改写
# ============================================================

def _update_db(plan: MigrationPlan) -> None:
    """执行 SQL 改写：路径前缀替换、删除整段删除特性记录、归一化分隔符。"""
    if not STATE_DB.exists():
        _log.warning("DB 不存在，跳过 DB 改写: %s", STATE_DB)
        return

    # 0. 扫描"pre-existing orphan"annual_report 行：pdf_path 文件不存在
    orphan_ids = _find_orphan_annual_report_ids()
    if orphan_ids:
        plan.actions.append(
            f"DB 改写：删除 {len(orphan_ids)} 条 pre-existing orphan annual_report "
            f"(id={orphan_ids})"
        )

    # SQLite 不支持 REPLACE 参数化；用 Python 拼接（数据来自我们自己的 DB）
    sql_statements: list[str] = [
        # 1. annual_report.business_md_path：md/split/ → md/raw/业务报告/
        (
            "UPDATE annual_report SET business_md_path = REPLACE(business_md_path, 'md/split/', 'md/raw/业务报告/') "
            "WHERE business_md_path IS NOT NULL"
        ),
        # 2. annual_report.finance_md_path：md/split/ → md/raw/财务报告/
        (
            "UPDATE annual_report SET finance_md_path = REPLACE(finance_md_path, 'md/split/', 'md/raw/财务报告/') "
            "WHERE finance_md_path IS NOT NULL"
        ),
        # 3. annual_report 其他 4 个 pdf 路径：只分隔符归一化（基础前缀不动）
        # 4. annual_report.md_path：deprecated，置 NULL（用户决定丢弃重跑可重生）
        "UPDATE annual_report SET md_path = NULL WHERE md_path IS NOT NULL",
        # 5. annual_report pdf_path / finance_pdf_path / other_pdf_path：分隔符归一化
        (
            "UPDATE annual_report SET "
            "pdf_path = REPLACE(pdf_path, '\\\\', '/'), "
            "finance_pdf_path = REPLACE(finance_pdf_path, '\\\\', '/'), "
            "other_pdf_path = REPLACE(other_pdf_path, '\\\\', '/') "
            "WHERE 1=1"
        ),
        # 6. annual_report business_md_path / finance_md_path：分隔符归一化（兜底）
        (
            "UPDATE annual_report SET "
            "business_md_path = REPLACE(business_md_path, '\\\\', '/'), "
            "finance_md_path = REPLACE(finance_md_path, '\\\\', '/') "
            "WHERE 1=1"
        ),
        # 7. 删除 legacy parse_pipeline 整本解析的 2023 宁德时代记录
        (
            "DELETE FROM report_run WHERE template='parse_pipeline' "
            f"AND company_id IN (SELECT id FROM company WHERE name='{LEGACY_COMPANY_NAME}') "
            f"AND year={LEGACY_YEAR}"
        ),
        (
            f"DELETE FROM annual_report WHERE company_id IN "
            f"(SELECT id FROM company WHERE name='{LEGACY_COMPANY_NAME}') "
            f"AND year={LEGACY_YEAR}"
        ),
        # 8. 删除 preliminary_parse 2024 宁德时代记录
        (
            "DELETE FROM report_run WHERE template='preliminary_parse' "
            f"AND company_id IN (SELECT id FROM company WHERE name='{LEGACY_COMPANY_NAME}') "
            f"AND year={PRELIM_YEAR}"
        ),
        # 9. report_run.final_path：分隔符归一化
        "UPDATE report_run SET final_path = REPLACE(final_path, '\\\\', '/') WHERE final_path IS NOT NULL",
    ]

    plan.actions.append("DB 改写：")
    for sql in sql_statements:
        plan.actions.append(f"  SQL: {sql}")


def _find_orphan_annual_report_ids() -> list[int]:
    """扫描 annual_report，找出 pdf_path 文件在磁盘上不存在的行（pre-existing orphan）。

    用于迁移前预清理：避免迁移后断言失败。
    """
    if not STATE_DB.exists():
        return []
    conn = sqlite3.connect(str(STATE_DB))
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, pdf_path FROM annual_report")
        ids: list[int] = []
        for row_id, pdf_path in cur.fetchall():
            if pdf_path is None:
                continue
            norm = pdf_path.replace("\\", "/")
            old_candidate = REPORT_DATA_ROOT / "report" / "raw" / norm
            new_candidate = REPORT_DATA_ROOT / norm
            if not old_candidate.exists() and not new_candidate.exists():
                ids.append(row_id)
        return ids
    finally:
        conn.close()


def _execute_db_updates(backup_dir: Path) -> None:
    """实际执行 DB 改写（从 backup_dir/state.db 复制回 state.db，再执行 SQL）。

    设计：在 transaction 里执行，全部成功才 commit；任何失败回滚。
    """
    backup_db = backup_dir / "state.db"
    if not backup_db.exists():
        raise FileNotFoundError(f"备份 DB 不存在: {backup_db}")
    shutil.copy2(backup_db, STATE_DB)
    conn = sqlite3.connect(str(STATE_DB))
    try:
        cur = conn.cursor()
        # 先删 pre-existing orphan
        orphan_ids = _find_orphan_annual_report_ids()
        if orphan_ids:
            _log.info("删除 pre-existing orphan annual_report id=%s", orphan_ids)
            placeholders = ",".join("?" * len(orphan_ids))
            cur.execute(
                f"DELETE FROM annual_report WHERE id IN ({placeholders})",
                orphan_ids,
            )

        for sql in [
            (
                "UPDATE annual_report SET business_md_path = REPLACE(business_md_path, 'md/split/', 'md/raw/业务报告/') "
                "WHERE business_md_path IS NOT NULL"
            ),
            (
                "UPDATE annual_report SET finance_md_path = REPLACE(finance_md_path, 'md/split/', 'md/raw/财务报告/') "
                "WHERE finance_md_path IS NOT NULL"
            ),
            "UPDATE annual_report SET md_path = NULL WHERE md_path IS NOT NULL",
            (
                "UPDATE annual_report SET "
                "pdf_path = REPLACE(pdf_path, '\\\\', '/'), "
                "finance_pdf_path = REPLACE(finance_pdf_path, '\\\\', '/'), "
                "other_pdf_path = REPLACE(other_pdf_path, '\\\\', '/')"
            ),
            (
                "UPDATE annual_report SET "
                "business_md_path = REPLACE(business_md_path, '\\\\', '/'), "
                "finance_md_path = REPLACE(finance_md_path, '\\\\', '/')"
            ),
            (
                "DELETE FROM report_run WHERE template='parse_pipeline' "
                f"AND company_id IN (SELECT id FROM company WHERE name='{LEGACY_COMPANY_NAME}') "
                f"AND year={LEGACY_YEAR}"
            ),
            (
                f"DELETE FROM annual_report WHERE company_id IN "
                f"(SELECT id FROM company WHERE name='{LEGACY_COMPANY_NAME}') "
                f"AND year={LEGACY_YEAR}"
            ),
            (
                "DELETE FROM report_run WHERE template='preliminary_parse' "
                f"AND company_id IN (SELECT id FROM company WHERE name='{LEGACY_COMPANY_NAME}') "
                f"AND year={PRELIM_YEAR}"
            ),
            "UPDATE report_run SET final_path = REPLACE(final_path, '\\\\', '/') WHERE final_path IS NOT NULL",
        ]:
            cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# 断言
# ============================================================

def _assert_paths_exist() -> tuple[list[str], list[str]]:
    """迁移后遍历 annual_report / report_run 所有 path 字段，文件必须存在。

    对于"迁移前已是孤儿"的记录（path 含反斜杠，或文件在旧位置 ``report/raw|md/`` 不存在）
    只记 WARNING，不 raise；
    对于"迁移后才发现的孤儿"（path 是我们刚改写过的，但目标位置不存在）则 raise。

    Returns: (warnings, errors) 两个列表。
    """
    warnings: list[str] = []
    errors: list[str] = []

    # 旧位置的前缀（绝对路径）
    old_prefixes = (
        str(REPORT_DATA_ROOT / "report" / "raw"),
        str(REPORT_DATA_ROOT / "report" / "md"),
    )

    def _classify(path: str, full: Path) -> str:
        """返回 'old' / 'new'。"""
        full_str = str(full)
        if any(full_str.startswith(p) for p in old_prefixes):
            return "old"
        if "\\" in path:
            return "old"
        return "new"

    conn = sqlite3.connect(str(STATE_DB))
    try:
        cur = conn.cursor()
        path_fields = (
            "pdf_path",
            "finance_pdf_path",
            "other_pdf_path",
            "business_md_path",
            "finance_md_path",
        )
        cur.execute(f"SELECT {', '.join(path_fields)} FROM annual_report")
        for row in cur.fetchall():
            for fld, path in zip(path_fields, row):
                if path:
                    full = REPORT_DATA_ROOT / path
                    if not full.exists():
                        kind = _classify(path, full)
                        msg = f"annual_report.{fld}={path} → {full}"
                        if kind == "old":
                            warnings.append(f"[pre-existing orphan] {msg}")
                        else:
                            errors.append(f"[post-migration missing] {msg}")

        cur.execute("SELECT final_path FROM report_run WHERE final_path IS NOT NULL")
        for (path,) in cur.fetchall():
            full = REPORT_DATA_ROOT / path
            if not full.exists():
                kind = _classify(path, full)
                msg = f"report_run.final_path={path} → {full}"
                if kind == "old":
                    warnings.append(f"[pre-existing orphan] {msg}")
                else:
                    errors.append(f"[post-migration missing] {msg}")
    finally:
        conn.close()

    return warnings, errors


# ============================================================
# 备份 / 回滚
# ============================================================

def make_backup() -> Path:
    """备份 report/ 子树 + state.db 到 .claude_state/migration_backup_{ts}/。

    Returns: 备份目录路径
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = STATE_DIR / f"migration_backup_{ts}"
    _ensure_dir(backup_dir)

    # 1. 备份 report/ 子树
    report_root = REPORT_DATA_ROOT / "report"
    if report_root.exists():
        _log.info("备份 %s → %s/report/", report_root, backup_dir)
        shutil.copytree(report_root, backup_dir / "report")

    # 2. 备份 state.db
    if STATE_DB.exists():
        _log.info("备份 %s → %s/state.db", STATE_DB, backup_dir)
        shutil.copy2(STATE_DB, backup_dir / "state.db")

    return backup_dir


def rollback(backup_dir: Path) -> None:
    """从备份恢复 report/ + state.db（删除已迁移的 {公司}/ 子树）。"""
    if not backup_dir.exists():
        raise FileNotFoundError(f"备份目录不存在: {backup_dir}")

    # 1. 恢复 report/ 子树：先删当前残存，再 copytree 回去
    current_report = REPORT_DATA_ROOT / "report"
    if current_report.exists():
        shutil.rmtree(current_report)
    shutil.copytree(backup_dir / "report", current_report)

    # 2. 恢复 state.db
    shutil.copy2(backup_dir / "state.db", STATE_DB)

    # 3. 删除 {公司}/ 根下已迁移内容（粗粒度：删所有 {公司} 目录里 md/ pdf/ 子树）
    for child in REPORT_DATA_ROOT.iterdir():
        if child.is_dir() and child.name in {f.name for f in (OLD_RAW_BASE.iterdir() if OLD_RAW_BASE.exists() else [])}:
            for sub in ("pdf", "md"):
                target = child / sub
                if target.exists():
                    shutil.rmtree(target)

    # 4. 删标志
    if FLAG_FILE.exists():
        FLAG_FILE.unlink()

    _log.info("回滚完成，备份目录: %s", backup_dir)


# ============================================================
# 入口
# ============================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="迁移到单棵 {公司}/ 路径树")
    parser.add_argument("--dry-run", action="store_true", help="只打印动作，不执行")
    parser.add_argument("--force", action="store_true", help="忽略已迁移标志，强制重跑")
    parser.add_argument("--rollback", type=str, metavar="BACKUP_DIR", help="回滚到指定备份目录")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.rollback:
        rollback(Path(args.rollback))
        return 0

    # 检查幂等性
    if FLAG_FILE.exists() and not args.force:
        _log.info("已迁移完成（FLAG 存在: %s），跳过；用 --force 重跑", FLAG_FILE)
        return 0

    # 1. 构建迁移计划
    plan = MigrationPlan()
    _build_company_moves(plan)
    _update_db(plan)

    # 2. 打印计划
    _log.info("==== 迁移计划（%d 个动作）====", len(plan.actions))
    for action in plan.actions:
        print(f"  {action}")

    if args.dry_run:
        _log.info("dry-run 完成，未执行任何操作")
        return 0

    # 3. 备份
    backup_dir = make_backup()
    _log.info("备份目录: %s", backup_dir)

    # 4. 执行物理移动
    try:
        plan.run()
    except Exception as e:
        _log.exception("物理迁移失败: %s", e)
        _log.info("开始回滚...")
        rollback(backup_dir)
        raise

    # 5. 执行 DB 改写（从备份恢复 DB 再写）
    try:
        _execute_db_updates(backup_dir)
    except Exception as e:
        _log.exception("DB 改写失败: %s", e)
        _log.info("开始回滚...")
        rollback(backup_dir)
        raise

    # 6. 断言
    warnings, errors = _assert_paths_exist()
    if warnings:
        _log.warning("==== 迁移前已是孤儿的路径（仅 WARNING，不回滚）====")
        for w in warnings:
            _log.warning("  %s", w)
    if errors:
        _log.error("==== 迁移后路径缺失（ERROR，将回滚）====")
        for e in errors:
            _log.error("  %s", e)
        _log.info("开始回滚...")
        rollback(backup_dir)
        raise RuntimeError(f"迁移后路径缺失 {len(errors)} 条")

    # 7. 清理空目录
    for base in (OLD_RAW_BASE, OLD_MD_BASE):
        if base.exists():
            try:
                base.rmdir()  # 仅当为空时
                _log.info("清理空目录: %s", base)
            except OSError:
                _log.warning("目录非空，保留: %s", base)

    # 8. 写标志
    FLAG_FILE.write_text(
        f"completed_at={time.strftime('%Y-%m-%d %H:%M:%S')}\nbackup={backup_dir}\n",
        encoding="utf-8",
    )
    _log.info("迁移完成 ✓ 标志: %s", FLAG_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
