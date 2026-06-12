"""
将年报第三节"管理层讨论与分析"按二级标题拆分为独立文件

用法:
    python split_section3.py <公司名> <年份>
示例:
    python split_section3.py 宁德时代 2023
"""
import sys
from pathlib import Path

# 添加 annual_report_reader 到路径
TOOLS_DIR = Path(__file__).parent.parent / "deep-research-report" / "shared" / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from annual_report_reader.utils import REPORT_BASE_PATH, parse_markdown_headings, extract_section_number


def split_section3(company: str, year: str) -> None:
    """拆分第三节为独立文件"""
    # 输入路径：阶段 2.3 章节切分产物（按 docs/artifacts.md 规范）
    # 优先 {公司}{年}年年报（零填充编号），fallback {公司}{年}年年度报告
    base_dir = REPORT_BASE_PATH / company / "md" / "clean" / f"{company}{year}年年报" / "by_section"
    input_file = base_dir / "03_第三节 管理层讨论与分析.md"
    if not input_file.exists():
        # 兼容旧名 {公司}{年}年年度报告（无 zero-pad）
        legacy_dir = REPORT_BASE_PATH / company / "md" / "clean" / f"{company}{year}年年度报告" / "by_section"
        input_file = legacy_dir / "03_第三节 管理层讨论与分析.md"
    if not input_file.exists():
        # 兼容更旧的 input/ 目录
        legacy_dir = REPORT_BASE_PATH / company / "input" / f"{company}{year}年年度报告"
        input_file = legacy_dir / "03_第三节 管理层讨论与分析.md"

    if not input_file.exists():
        print(f"文件不存在: {input_file}")
        return

    content = input_file.read_text(encoding="utf-8")
    headings = parse_markdown_headings(content)

    # 过滤 level==2 的二级标题
    h2_headings = [h for h in headings if h["level"] == 2]

    if not h2_headings:
        print("未找到二级标题")
        return

    # 输出目录：阶段 2.4（按 docs/artifacts.md 规范，不带 year 子目录）
    output_dir = REPORT_BASE_PATH / company / "md" / "clean" / f"{company}{year}年年报" / "管理层讨论"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 按行号切分内容
    lines = content.split('\n')

    for idx, h2 in enumerate(h2_headings):
        title = h2["title"]
        line_start = h2["line_start"]

        # 确定结束行（下一个同级或更高级标题的前一行）
        line_end = len(lines)
        for next_h in h2_headings[idx + 1:]:
            line_end = next_h["line_start"] - 1
            break

        # 提取序号
        seq_num = extract_section_number(title)
        if seq_num is not None:
            file_prefix = f"{seq_num:02d}_"
        else:
            file_prefix = f"{idx + 1:02d}_"

        # 清理文件名中的非法字符
        safe_title = "".join(c if c not in r'\/:*?"<>|' else "_" for c in title)
        output_file = output_dir / f"{file_prefix}{safe_title}.md"

        # 截取内容（行号从1开始，转为0基索引）
        section_lines = lines[line_start - 1:line_end]
        section_content = '\n'.join(section_lines)

        # 写入文件（带元信息头）
        header = f"""---
company: {company}
year: {year}
section: 第三节 管理层讨论与分析
original_title: {title}
---

"""
        output_file.write_text(header + section_content, encoding="utf-8")
        print(f"生成: {output_file.name}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python split_section3.py <公司名> <年份>")
        sys.exit(1)

    split_section3(sys.argv[1], sys.argv[2])