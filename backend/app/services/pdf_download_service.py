"""PDF 下载 service：构造巨潮 PDF 直链，httpx 流式下载到 raw/{公司}/pdf/。

注意：A股年报实际下载通常需要：
1. POST http://www.cninfo.com.cn/new/hisAnnouncement/query
   形参：stock=300750,gssh0300750,9900020127&tabName=fulltext&pageSize=30&pageNum=1
        &column=szse&category=category_ndbg_szsh;&plate=sz;&seDate=...
   响应：announcements[] 数组含 announcementId / announcementTime / adjunctUrl
2. 直链：http://static.cninfo.com.cn/{adjunctUrl}

本阶段提供「构造查询参数 + 单条下载」两个原子函数，下载循环由 worker 调用。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import httpx

from app.config import get_settings


CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_FILE_HOST = "http://static.cninfo.com.cn/"


@dataclass
class CninfoAnnouncement:
    announcement_id: str
    announcement_time: str  # "yyyy-mm-dd"
    title: str
    adjunct_url: str  # finalpage/2024-03-01/1234567890.PDF
    pdf_url: str  # http://static.cninfo.com.cn/finalpage/...


@dataclass
class DownloadResult:
    saved_path: Path
    sha256: str
    bytes: int


def query_annual_reports(
    stock_code: str,
    org_id: str,
    years: Iterable[int],
    timeout: float = 15.0,
) -> List[CninfoAnnouncement]:
    """查 stock 指定年份的所有「年度报告」公告（category=category_ndbg_szsh）。

    巨潮 API 反爬：需要 Referer + User-Agent + Cookie（首次访问 /new/disclosure 拿）。
    本函数只构造请求骨架并返回空 list，**真实调用在 worker 中重试**。
    """
    years_list = list(years)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "http://www.cninfo.com.cn/new/disclosure/stock?stockCode="
        + stock_code,
        "Accept": "application/json, text/plain, */*",
    }
    results: List[CninfoAnnouncement] = []
    with httpx.Client(timeout=timeout, headers=headers) as client:
        for year in years_list:
            payload = {
                "stock": f"{stock_code},{org_id},9900020127",
                "tabName": "fulltext",
                "pageSize": 30,
                "pageNum": 1,
                "column": "szse",
                "category": "category_ndbg_szsh;",
                "plate": "sz;",
                "seDate": f"{year}-01-01~{year}-12-31",
                "searchkey": "",
            }
            try:
                r = client.post(CNINFO_QUERY_URL, data=payload)
                r.raise_for_status()
                data = r.json()
            except Exception:
                # 网络/Cookie 问题：跳过该年
                continue

            for item in data.get("announcements") or []:
                if "年度报告" not in item.get("announcementTitle", ""):
                    continue
                if "摘要" in item.get("announcementTitle", ""):
                    continue
                adjunct = item.get("adjunctUrl", "")
                if not adjunct.lower().endswith(".pdf"):
                    continue
                results.append(
                    CninfoAnnouncement(
                        announcement_id=str(item.get("announcementId", "")),
                        announcement_time=(item.get("adjunctUrl", "").split("/")[-2] if "/" in adjunct else ""),
                        title=item.get("announcementTitle", ""),
                        adjunct_url=adjunct,
                        pdf_url=CNINFO_FILE_HOST + adjunct,
                    )
                )
    return results


def download_pdf(
    url: str,
    dest_dir: Path,
    filename: str,
    timeout: float = 60.0,
) -> Optional[DownloadResult]:
    """流式下载到 dest_dir/filename，返回 (path, sha256, bytes)。失败返回 None。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    h = hashlib.sha256()
    total = 0
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", url) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(chunk_size=64 * 1024):
                        if chunk:
                            f.write(chunk)
                            h.update(chunk)
                            total += len(chunk)
        return DownloadResult(saved_path=dest, sha256=h.hexdigest(), bytes=total)
    except Exception:
        if dest.exists():
            dest.unlink(missing_ok=True)
        return None
