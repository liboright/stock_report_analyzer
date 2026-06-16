"""一次性脚本：归一化 annual_report / report_run 表中所有路径字段的 \ → /。"""
import os
os.environ["DB_PATH"] = "D:/quant/report_data/.claude_state/state.db"
from app.config import get_settings
get_settings.cache_clear()
s = get_settings()
from app.db import session as db_session
from app.models import AnnualReport, ReportRun

with db_session.SessionLocal() as sess:
    ars = sess.query(AnnualReport).all()
    for ar in ars:
        for col in ["pdf_path", "finance_pdf_path", "other_pdf_path",
                    "business_md_path", "finance_md_path", "md_path",
                    "tables_dir_path"]:
            val = getattr(ar, col, None)
            if val and "\\" in val:
                new_val = val.replace("\\", "/")
                print(f"annual_report id={ar.id} {col}: {val!r} -> {new_val!r}")
                setattr(ar, col, new_val)

    runs = sess.query(ReportRun).all()
    for r in runs:
        if r.final_path and "\\" in r.final_path:
            new_val = r.final_path.replace("\\", "/")
            print(f"report_run id={r.id} final_path: {r.final_path!r} -> {new_val!r}")
            r.final_path = new_val
        if r.error and "\\" in r.error:
            # error 字段是文案，不动（仅路径字段动）
            pass

    sess.commit()
    print("done")
