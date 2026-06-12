"""公司搜索 service：读 mapping.json 查 stock_code + 构造巨潮 PDF 直链。

数据源策略（单用户本地版）：
1. 优先查 mapping.json（手工维护的 A 股代码表）
2. 后续可扩展为在线 API（suggest3.sinajs.cn 等），但本阶段不引入外部依赖

巨潮 PDF 直链规律：
- 详情页：http://www.cninfo.com.cn/new/disclosure/detail?stockCode=300750&announcementId=...
- PDF 直链：http://static.cninfo.com.cn/finalpage/{yyyy-mm-dd}/{announcementId}.PDF
- 公告检索：http://www.cninfo.com.cn/new/hisAnnouncement/query
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import get_settings


@dataclass
class CompanySearchResult:
    name: str
    stock_code: Optional[str]
    pdf_url_template: Optional[str]  # 形如 "http://static.cninfo.com.cn/finalpage/{date}/{announcementId}.PDF"


def _load_mapping() -> dict[str, str]:
    """读 mapping.json，文件不存在则返回空 dict。"""
    p: Path = get_settings().MAPPING_PATH
    if not p.exists():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
        # 忽略 _comment 字段
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except (json.JSONDecodeError, OSError):
        return {}


def lookup_stock_code(company_name: str) -> Optional[str]:
    """查 stock_code。"""
    mapping = _load_mapping()
    return mapping.get(company_name)


def build_search_result(company_name: str) -> CompanySearchResult:
    """构造搜索结果（用于 company 创建/详情展示）。"""
    code = lookup_stock_code(company_name)
    pdf_tpl = None
    if code:
        # 巨潮直链（占位，announcementId 与日期需调用查询接口；本阶段留模板）
        pdf_tpl = f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}"
    return CompanySearchResult(name=company_name, stock_code=code, pdf_url_template=pdf_tpl)


def build_cninfo_announcement_query_url(stock_code: str) -> str:
    """巨潮「历史公告查询」接口 GET URL（stock orgId 需要 stockCode 解析 orgId_gssz0XXXX）。"""
    # 实际 stock orgId 需要先 POST /new/hisAnnouncement/query 查 orgId；这里给出搜索入口
    return f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={stock_code}&orgId=gssz0{stock_code}"
