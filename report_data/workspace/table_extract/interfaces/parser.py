"""
表格解析器接口定义
HTML 表格解析，含完整 rowspan/colspan 处理
"""

from typing import List, Optional
from .models import ParsedTable, TableCell


class TableParser:
    """
    HTML 表格解析器

    正确处理 rowspan 和 colspan，维护单元格网格，
    使跨行/跨列的单元格内容能正确复制到所有对应位置。

    使用方法：
        parser = TableParser()
        parsed = parser.parse(html_string)
        for row in parsed.data_grid:
            print(row)
    """

    def parse(self, html: str) -> ParsedTable:
        """
        解析 HTML 表格为二维网格

        处理逻辑：
        1. 遍历 table > tr > td/th 构建单元格列表
        2. 建立 row_count x col_count 的网格
        3. 对于 rowspan 单元格，将内容复制到后续行的相同列位置
        4. 对于 colspan 单元格，展开为多个列
        5. 返回包含 data_grid 的 ParsedTable

        Args:
            html: HTML 表格字符串（如 "<table>...</table>"）

        Returns:
            ParsedTable: 包含以下属性
                - headers: 多级表头行列表
                - data_grid: 二维数据网格（None 表示被合并单元格覆盖）
                - row_count: 行数
                - col_count: 列数
                - raw_html: 原始 HTML

        Raises:
            TableParseError: 解析失败时
        """
        ...

    def parse_multiple(self, html_content: str) -> List[ParsedTable]:
        """
        从 HTML 内容中提取所有表格

        Args:
            html_content: 包含多个表格的 HTML 字符串

        Returns:
            List[ParsedTable]: 解析后的表格列表

        示例：
            html = "<table>...</table> text <table>...</table>"
            tables = parser.parse_multiple(html)
            for t in tables:
                print(t.row_count, t.col_count)
        """
        ...

    def _build_grid(self, rows: List[List[TableCell]]) -> List[List[Optional[str]]]:
        """
        构建二维数据网格，处理 rowspan 复制

        内部方法，外部不应直接调用。

        算法：
        1. 计算网格维度（最大行数、最大列数）
        2. 创建 row_count x col_count 的空网格
        3. 填充单元格，遇到 rowspan 将内容复制到后续行
        4. 使用 filled 矩阵追踪已填充位置，避免重复填充

        Args:
            rows: 单元格矩阵，每行是一个 TableCell 列表

        Returns:
            List[List[Optional[str]]]: 二维数据网格
        """
        ...


def parse_html_table(html: str) -> ParsedTable:
    """
    快捷函数：解析单个 HTML 表格

    Args:
        html: HTML 表格字符串

    Returns:
        ParsedTable: 解析后的表格
    """
    ...


def extract_tables_from_html(html_content: str) -> List[ParsedTable]:
    """
    快捷函数：从 HTML 内容中提取所有表格

    Args:
        html_content: HTML 内容

    Returns:
        List[ParsedTable]: 表格列表
    """
    ...