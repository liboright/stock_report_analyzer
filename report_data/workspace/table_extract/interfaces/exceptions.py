"""
异常类型定义
表格提取模块使用的所有异常类型
"""


class TableExtractError(Exception):
    """表格提取基础异常"""
    pass


class TableParseError(TableExtractError):
    """
    表格解析异常

    当 HTML 表格解析失败时抛出，如：
    - HTML 格式错误
    - 缺少必要的标签
    - 单元格嵌套错误
    """
    pass


class ColumnAlignError(TableExtractError):
    """
    列对齐异常

    当多年份表格列数不一致且无法自动对齐时抛出
    """
    pass


class ClassificationError(TableExtractError):
    """
    表格分类异常

    当表格无法被分类到任何已知类型时抛出
    """
    pass


class MergeError(TableExtractError):
    """
    数据合并异常

    当合并多年份数据失败时抛出，如：
    - 项目名称不一致
    - 数据类型冲突
    - 列结构不兼容
    """
    pass


class FileNotFoundTableError(TableExtractError):
    """
    文件不存在异常

    当指定的 md 文件或目录不存在时抛出
    """
    pass


class InvalidTableStructureError(TableExtractError):
    """
    表格结构无效异常

    当表格结构不符合预期（如无数据行、列数异常）时抛出
    """
    pass