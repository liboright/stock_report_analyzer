"""对已切分的年报 PDF（财务报告 / 业务报告）做 MinerU 解析。

输入：通过 `POST /split-pdf` 已切分到
    `RAW_BASE_PATH/{公司}/pdf/split/{公司}{年份}年年度报告_{财务报告,业务报告}.pdf`

输出 markdown 写到
    `RAW_BASE_PATH/{公司}/md/{公司}{年份}年年度报告_{财务报告,业务报告}/`
        ├── {公司}{年份}年年度报告_{财务报告,业务报告}.md
        └── full.md

> 财务/业务两份必须放在不同子目录：MinerU 解析器内部会写 `full.md`，
> 同一目录会被互相覆盖。

用法：
    python scripts/parse_split_pdfs.py                       # 默认 宁德时代 + 真实 MinerU
    python scripts/parse_split_pdfs.py --company 宁德时代
    python scripts/parse_split_pdfs.py --use-mock           # 跳过 MinerU，写 mock MD
    python scripts/parse_split_pdfs.py --parts 财务报告     # 只跑财务报告部分
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# 把 backend/ 加进 sys.path，才能 `import app.*`
# 同时把 CWD 切到 backend/ —— Settings 读 `.env` 是相对 CWD 解析的
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
import os  # noqa: E402

os.chdir(str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.models import AnnualReport, Company  # noqa: E402

_log = logging.getLogger("parse_split_pdfs")

# 切分产物的两个"角色"：财务报告 / 业务报告
PARTS: dict[str, str] = {
    "财务报告": "finance_pdf_path",
    "业务报告": "other_pdf_path",
}


def _parse_one(
    pdf_path: Path,
    output_dir: Path,
    output_md: Path,
    api_key: str,
    api_base: str,
    use_mock: bool,
) -> Path:
    """对单个切分 PDF 调 MinerU，写到 output_dir/output_md。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    if use_mock:
        # mock 模式：写一个最简 markdown（不调 API）
        md = f"# {pdf_path.stem}\n\nmock 解析（{pdf_path.name}）。\n"
        output_md.write_text(md, encoding="utf-8")
        (output_dir / "full.md").write_text(md, encoding="utf-8")
        return output_md

    from app.services.mineru_parser import MinerUOnlineParser

    parser = MinerUOnlineParser(
        str(pdf_path),
        api_key=api_key,
        api_base=api_base,
    )
    md = parser.parse(output_dir=str(output_dir))
    # MinerUOnlineParser 不写文件，由我们落盘
    (output_dir / "full.md").write_text(md, encoding="utf-8")
    output_md.write_text(md, encoding="utf-8")
    return output_md


def _md_dir_for(company: str, year: int, part_name: str) -> Path:
    """财务/业务两份各自一个子目录（避免 full.md 冲突）。"""
    return get_settings().RAW_BASE_PATH / company / "md" / f"{company}{year}年年度报告_{part_name}"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    ap = argparse.ArgumentParser(description="对已切分 PDF 跑 MinerU 解析")
    ap.add_argument("--company", default="宁德时代", help="公司名（默认 宁德时代）")
    ap.add_argument(
        "--parts",
        nargs="+",
        default=list(PARTS.keys()),
        choices=list(PARTS.keys()),
        help="要解析的子部分（默认两份都跑）",
    )
    ap.add_argument(
        "--use-mock",
        action="store_true",
        help="跳过真实 MinerU，写 mock MD（开发/测试用）",
    )
    args = ap.parse_args()

    settings = get_settings()
    api_key = settings.MINERU_API_KEY
    api_base = settings.MINERU_API_BASE
    if not args.use_mock and (not api_key or api_key == "placeholder-replace-me"):
        _log.error("未配置 MINERU_API_KEY（在 .env），或加 --use-mock")
        return 2

    # 1) 查公司
    db = db_session.SessionLocal()
    try:
        company: Company | None = (
            db.query(Company).filter(Company.name == args.company).first()
        )
        if not company:
            _log.error("公司不存在: %s", args.company)
            return 1
        # 2) 查所有 split_status=done 的年报
        reports: list[AnnualReport] = (
            db.query(AnnualReport)
            .filter(
                AnnualReport.company_id == company.id,
                AnnualReport.split_status == "done",
            )
            .order_by(AnnualReport.year.desc())
            .all()
        )
    finally:
        db.close()

    if not reports:
        _log.warning("公司 %s 没有已切分的年报（split_status=done 为空）", args.company)
        return 0

    _log.info("公司 %s 共 %d 份已切分年报", args.company, len(reports))
    for ar in reports:
        _log.info("  - %d 年 (id=%d)", ar.year, ar.id)

    overall_ok = 0
    overall_fail = 0
    t0 = time.time()
    for ar in reports:
        _log.info("=" * 60)
        _log.info(">> %d 年年报 (id=%d) 开始", ar.year, ar.id)
        for part_name in args.parts:
            attr = PARTS[part_name]
            rel_pdf = getattr(ar, attr)
            if not rel_pdf:
                _log.warning("  [%s] DB 字段 %s 为空，跳过", part_name, attr)
                continue
            pdf_abs = settings.RAW_BASE_PATH / rel_pdf
            if not pdf_abs.exists():
                _log.error("  [%s] PDF 不存在: %s", part_name, pdf_abs)
                overall_fail += 1
                continue

            out_dir = _md_dir_for(args.company, ar.year, part_name)
            out_md = out_dir / f"{args.company}{ar.year}年年度报告_{part_name}.md"
            t1 = time.time()
            _log.info(
                "  [%s] PDF=%s  size=%.1fMB  ->  %s",
                part_name,
                pdf_abs.name,
                pdf_abs.stat().st_size / 1024 / 1024,
                out_md.relative_to(settings.RAW_BASE_PATH),
            )
            try:
                _parse_one(
                    pdf_abs,
                    out_dir,
                    out_md,
                    api_key=api_key,
                    api_base=api_base,
                    use_mock=args.use_mock,
                )
            except Exception as e:  # noqa: BLE001
                _log.exception("  [%s] 解析失败: %s", part_name, e)
                overall_fail += 1
                continue
            _log.info(
                "  [%s] 完成 (%ds, %.1fKB)",
                part_name,
                int(time.time() - t1),
                out_md.stat().st_size / 1024,
            )
            overall_ok += 1
    _log.info("=" * 60)
    _log.info(
        "全部完成: ok=%d fail=%d  (%.1fs)",
        overall_ok,
        overall_fail,
        time.time() - t0,
    )
    return 0 if overall_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
