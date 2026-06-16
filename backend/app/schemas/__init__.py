"""Pydantic v2 schemas (request/response)。"""
from app.schemas.company import (
    CompanyCreate,
    CompanyRead,
    CompanyDetail,
)
from app.schemas.annual_report import (
    AnnualReportRead,
    AnnualReportUploadResponse,
)
from app.schemas.report import (
    ReportGenerateRequest,
    ReportRunRead,
    ReportRunDetail,
    ReportContent,
)
from app.schemas.task import (
    TaskStatus,
    TaskEventRead,
)
from app.schemas.file_tree import (
    ChapterFile,
    FileTreeResponse,
    ResearchFile,
    Section3File,
    SubsectionFile,
    TableCsvFile,
)
from app.schemas.table_merge import (
    GroupSummary,
    TablesMergeRequest,
    TablesMergeResponse,
)

__all__ = [
    "CompanyCreate",
    "CompanyRead",
    "CompanyDetail",
    "AnnualReportRead",
    "AnnualReportUploadResponse",
    "ReportGenerateRequest",
    "ReportRunRead",
    "ReportRunDetail",
    "ReportContent",
    "TaskStatus",
    "TaskEventRead",
    "SubsectionFile",
    "ChapterFile",
    "Section3File",
    "ResearchFile",
    "TableCsvFile",
    "FileTreeResponse",
    "TablesMergeRequest",
    "TablesMergeResponse",
    "GroupSummary",
]
