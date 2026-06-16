"""
核心类接口定义
TableExtractor 主流程类
"""

from typing import Dict, List, Optional
from .models import TableType, MergedData, TableInfo


class TableExtractor:
    """
    表格提取主流程类

    协调 parser、classifier、merger 完成：
    1. 扫描 md 文件中的 HTML 表格
    2. 解析表格（含 rowspan 处理）
    3. 分类为 8 类
    4. 合并多年数据
    5. 输出 CSV
    """

    def __init__(self, company: str, base_dir: str) -> None:
        """
        初始化表格提取器

        Args:
            company: 公司名（如 "宁德时代"）
            base_dir: 基础目录（如 "D:/quant/report_database"）
        """
        ...

    def extract(self) -> Dict[TableType, MergedData]:
        """
        执行完整提取流程

        处理步骤：
        1. 扫描目录：md/{公司}/output/mid_file/管理层讨论/{年份}/*.md
        2. 解析表格：调用 TableParser 解析 HTML 表格
        3. 表格分类：调用 TableClassifier 分类为 8 类
        4. 数据合并：调用 TableMerger 合并多年数据
        5. 输出 CSV：保存到 output/tables/{类型}.csv

        Returns:
            Dict[TableType, MergedData]: 类型到合并数据的映射
        """
        ...

    def extract_from_md(self, md_path: str) -> List[TableInfo]:
        """
        从单个 md 文件提取所有表格

        Args:
            md_path: md 文件路径

        Returns:
            List[TableInfo]: 提取的表格列表
        """
        ...

    def scan_md_files(self, company_path: str) -> Dict[str, List[str]]:
        """
        扫描公司所有年份的 md 文件

        Args:
            company_path: 公司目录路径

        Returns:
            Dict[str, List[str]]: {"年份": [文件路径列表], ...}
        """
        ...

    def save_to_csv(self, merged: MergedData, output_path: str) -> None:
        """
        保存为 CSV 文件（支持双行表头）

        Args:
            merged: 合并后的数据
            output_path: 输出文件路径
        """
        ...

    def get_table_count(self) -> int:
        """获取已提取的表格总数"""
        ...

    def get_classification_stats(self) -> Dict[TableType, int]:
        """获取分类统计"""
        ...