"""PDF 解析 service：包装 MinerUOnlineParser / BatchMinerUClient。

- 真实模式（PARSE_MODE=real）：调 MinerU 在线 API（需 MINERU_API_KEY，联网，慢）
- Mock 模式（PARSE_MODE=mock）：读预制 fixture markdown（离线、即时、用于测试/开发）

下游使用：parse_pipeline.py 在 BackgroundTask 中调 `parse_pdfs_to_md_batch([(pdf, md, data_id), ...])`。
单文件入口 `parse_pdf_to_md(pdf, md_path, use_mock)` 保留以兼容老 caller，内部委托到 batch。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import get_settings


def parse_pdf_to_md(
    pdf_path: Path,
    output_md_path: Path,
    use_mock: bool = False,
) -> str:
    """把 PDF 解析为 Markdown，存到 output_md_path（单文件便捷入口）。

    内部委托到 `parse_pdfs_to_md_batch([(pdf, md, "single")], use_mock=use_mock)`。

    Returns: 写入的 markdown 文本（同时也写到 output_md_path）。
    Raises: FileNotFoundError if PDF 不存在；RuntimeError on minerU failure。
    """
    result = parse_pdfs_to_md_batch(
        [(Path(pdf_path), Path(output_md_path), "single")],
        use_mock=use_mock,
    )
    return result["single"]


def parse_pdfs_to_md_batch(
    items: List[Tuple[Path, Path, str]],
    *,
    use_mock: bool = False,
) -> Dict[str, str]:
    """批量解析 N 份 PDF，**单次 MinerU 批**（一家公司多年 2N 份一次提交）。

    Args:
        items: [(pdf_path, output_md_path, data_id), ...]
            - pdf_path: 待解析 PDF
            - output_md_path: 落盘 markdown 的目标路径（同名 full.md 也写到同目录）
            - data_id: MinerU 任务回查键；返回 dict 中以 data_id 索引 markdown
        use_mock: True 时每份都走 _mock_md_content 单独写盘（不调 MinerU），
                 便于单测 / 离线开发。False 时走 BatchMinerUClient，单次 API + 单次轮询。

    Returns:
        {data_id: markdown_content}，长度 == len(items)

    Raises:
        FileNotFoundError 任一 PDF 不存在
        RuntimeError 真实模式未配 MINERU_API_KEY
        PDFParserError MinerU API 失败
    """
    if not items:
        return {}
    # 预检
    for pdf, _md, _did in items:
        if not Path(pdf).exists():
            raise FileNotFoundError(f"PDF 不存在: {pdf}")
    for _pdf, md, _did in items:
        Path(md).parent.mkdir(parents=True, exist_ok=True)

    if use_mock:
        out: Dict[str, str] = {}
        for _pdf, md, did in items:
            content = _mock_md_content(_pdf)
            Path(md).write_text(content, encoding="utf-8")
            out[did] = content
        return out

    # 真实 MinerU（单次批）
    settings = get_settings()
    if not settings.MINERU_API_KEY or settings.MINERU_API_KEY == "placeholder-replace-me":
        raise RuntimeError(
            "未配置 MINERU_API_KEY。请在 .env 设置后重试，或在测试时设 use_mock=True。"
        )

    from app.services.mineru_parser import BatchMinerUClient

    client = BatchMinerUClient(
        api_key=settings.MINERU_API_KEY,
        api_base=settings.MINERU_API_BASE,
    )
    # data_id 直接用 items 里的；上传顺序按 items 顺序
    md_by_data_id = client.submit_and_wait(
        [(Path(p), did) for p, _md, did in items],
    )

    # 落盘：每个文件独立写 output_md_path + full.md
    for _pdf, md_out, did in items:
        content = md_by_data_id[did]
        full_path = Path(md_out).parent / "full.md"
        # full.md 共享同目录会被覆盖——按"每份都写自己的 full.md"语义，
        # 这里以最后一份为准（与原 parse_pdf_to_md 行为一致：每次都覆盖）
        full_path.write_text(content, encoding="utf-8")
        Path(md_out).write_text(content, encoding="utf-8")

    return md_by_data_id


def _mock_md_content(pdf_path: Path) -> str:
    """为测试/离线场景返回固定结构 markdown。

    结构尽量贴近真实年报的章节切分：含 # 第X节 和 ## H2 二级标题（用于测 split_by_sections
    和 split_section3）。
    """
    return """# 第一节 重要提示、目录和释义

本报告为测试 mock 内容。

# 第二节 公司简介和主要财务指标

| 项目 | 2023 | 2022 |
| --- | --- | --- |
| 营业收入 | 1000 | 800 |
| 净利润 | 100 | 80 |

# 第三节 管理层讨论与分析

## 一、报告期内公司所处行业情况
公司处于快速发展行业，受益于政策与需求双轮驱动。

## 二、报告期内公司从事的主要业务
公司主营产品包括 A、B、C 三大系列。

## 三、报告期内公司从事的业务情况
公司围绕主营业务持续经营。

## （一）主营业务分析
2023 年公司实现营业收入 1000 万元，同比增长 25%。

## 四、核心竞争力分析
公司具有技术、规模、客户三大优势。

## 五、报告期内接待调研情况
报告期内共接待机构调研 50 场次。

# 第四节 公司治理

公司严格按照《公司法》《证券法》等法律法规运作。

# 第五节 环境和社会责任

公司持续推进绿色低碳发展。

# 第六节 重要事项

无重大未披露事项。

# 第七节 股份变动及股东情况

报告期末普通股股东总数 100,000 户。

# 第八节 优先股相关情况

不适用。

# 第九节 债券相关情况

不适用。

# 第十节 财务报告

详见财务报表附注。
"""
