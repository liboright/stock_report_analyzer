"""MinerU 在线 API 解析器（项目内嵌版）。

从 `D:/quant/report_gen/report_generator/parser/` 复制并改造：
- 移除对 `report_generator` 父包的依赖
- `MinerUAPIClient` 改为构造注入 api_key / api_base（从 app.config 读，不再硬编码）
- `__init__.py` 只 re-export 在线 API 链路实际用到的符号
- 2026-06 新增 `BatchMinerUClient`：单次提交 N 份 PDF 的批量客户端
  （`parse_split_pipeline` 走此入口）

在线 API 调用链：
    MinerUOnlineParser / BatchMinerUClient
        └── MinerUAPIClient / BatchMinerUClient.submit_and_wait
        └── MinerUProcessor   (HeadingLevelConverter 标题归一 + 清理 zip 噪声文件)
"""
from .exceptions import PDFParserError
from .heading_level_converter import HeadingLevelConverter
from .mineru_api_client import (
    BatchMinerUClient,
    MinerUAPIClient,
    convert_mineru,
    convert_mineru_batch,
)
from .mineru_online_parser import MinerUOnlineParser, parse_mineru_online
from .mineru_processor import MinerUProcessor, process_mineru_md

__all__ = [
    "PDFParserError",
    "HeadingLevelConverter",
    "MinerUAPIClient",
    "convert_mineru",
    "BatchMinerUClient",
    "convert_mineru_batch",
    "MinerUOnlineParser",
    "parse_mineru_online",
    "MinerUProcessor",
    "process_mineru_md",
]
