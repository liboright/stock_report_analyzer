"""
报告写作智能体 - 专业的上市公司研究报告写作智能体
支持从头写作和增量写作两种模式

这是一个 Claude Code SubAgent，直接执行 LLM 调用并写入文件
"""

import os
import sys
from pathlib import Path
from typing import Optional

# 添加父目录到 path 以便导入 llm_logger
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_template(template_path: str) -> str:
    """加载模版文件内容"""
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


def load_reference_content(reference_content_path: str) -> str:
    """加载参考内容文件"""
    with open(reference_content_path, 'r', encoding='utf-8') as f:
        return f.read()


def load_existing_content(existing_content_path: str) -> str:
    """加载已有内容文件"""
    with open(existing_content_path, 'r', encoding='utf-8') as f:
        return f.read()


def save_output(content: str, output_path: str) -> None:
    """保存输出内容"""
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)


def parse_template_structure(template_content: str) -> dict:
    """解析模版结构，提取章节和小节信息"""
    lines = template_content.split('\n')
    chapters = []
    current_chapter = None
    current_section = None
    section_content = []

    for line in lines:
        if line.startswith('## ') and not line.startswith('### '):
            if current_chapter:
                chapters.append(current_chapter)
            current_chapter = {
                'name': line.replace('## ', '').strip(),
                'sections': []
            }
            current_section = None
        elif line.startswith('### '):
            if current_section:
                current_chapter['sections'].append({
                    'name': line.replace('### ', '').strip(),
                    'content': '\n'.join(section_content)
                })
            current_section = {
                'name': line.replace('### ', '').strip(),
                'content': []
            }
            section_content = []
        elif current_section is not None:
            section_content.append(line)
        elif current_chapter is not None:
            pass

    if current_chapter:
        if current_section:
            current_chapter['sections'].append({
                'name': current_section['name'],
                'content': '\n'.join(section_content)
            })
        chapters.append(current_chapter)

    return {'chapters': chapters}


def _format_template_structure(structure: dict) -> str:
    """格式化模版结构用于提示词"""
    lines = []
    for chapter in structure.get('chapters', []):
        lines.append(f"- {chapter['name']}")
        for section in chapter.get('sections', []):
            lines.append(f"  - {section['name']}")
    return '\n'.join(lines)


def generate_initial_prompt(template_content: str, reference_content: str, reference_source: str = None) -> str:
    """生成从头写作的提示词"""
    template_structure = parse_template_structure(template_content)
    source_label = reference_source if reference_source else "该参考内容"

    prompt = f"""请根据以下模版格式和参考内容，生成完整的年度报告研究章节。

【参考内容来自】：{source_label}

模版结构：
{_format_template_structure(template_structure)}

参考内容：
{reference_content}

【写作要求】：
1. 按照模版结构生成完整内容，覆盖所有章节和小节
2. 所有数据必须来自参考内容（{source_label}）
3. 数据需标注来源（如"数据来源：{source_label}"）
4. 分析要有深度和逻辑性，优先使用具体数字而非模糊表述
5. 保持Markdown格式规范

请输出完整内容：
"""

    return prompt


def generate_enrichment_prompt(
    template_content: str,
    existing_content: str,
    reference_content: str,
    reference_source: str = None
) -> str:
    """生成增量写作的提示词"""
    if reference_source is None:
        reference_source = "该参考内容"

    prompt = f"""【增量写作任务】请从新参考内容中提取数据，补充到已有内容的正确位置。

【新参考内容来自】：{reference_source}

【已有内容】（保持原样，不要修改已有数据和表述）：
{existing_content}

【新参考内容】（来自{reference_source}，从中提取补充数据）：
{reference_content}

【写作原则】：
1. **不改已有**：已有内容中的所有数据、表述、段落结构保持原样，一字不变
2. **仅补充缺失**：从新参考内容中识别已有内容缺失的数据（如某些年份的市场份额、历史对比数据等）
3. **位置正确**：将补充数据放入已有内容对应的正确位置（表格单元格、年份小节等）
4. **标注来源**：所有新增数据必须标注来源为 {reference_source}
5. **不重新生成**：不重新生成或改写已有段落，不输出完整文档，只输出需要补充的内容片段

【具体操作】：
- 对比已有内容和新参考内容，找出缺失的年份数据
- 将缺失数据补充到对应位置（如财务表格的缺失单元格、年份对比的缺失数据点）
- 若某些章节在已有内容中已有对应数据，检查是否需要补充更早年份的数据

请按上述原则输出补充内容（仅输出需要补充的部分，不要输出完整文档）：
"""

    return prompt


def write_report(
    template_path: str,
    reference_content_path: str,
    output_path: str,
    existing_content_path: str = None,
    reference_source: str = None
) -> dict:
    """
    主函数：执行报告写作

    Args:
        template_path: 模版文件路径
        reference_content_path: 参考内容文件路径
        output_path: 输出文件路径
        existing_content_path: 已有内容文件路径（可选，用于增量写作）
        reference_source: 参考内容来源描述（如"2024年年报"）

    Returns:
        dict: 包含 result 和 prompt，用于日志记录
    """
    template_content = load_template(template_path)
    reference_content = load_reference_content(reference_content_path)

    if existing_content_path:
        existing_content = load_existing_content(existing_content_path)
        prompt = generate_enrichment_prompt(
            template_content,
            existing_content,
            reference_content,
            reference_source
        )
        mode = "增量写作"
    else:
        prompt = generate_initial_prompt(template_content, reference_content, reference_source)
        mode = "从头写作"

    return {
        "prompt": prompt,
        "mode": mode,
        "output_path": output_path,
        "reference_source": reference_source
    }


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("用法: python report_writer.py <template_path> <reference_content_path> <output_path> [existing_content_path] [reference_source]")
        sys.exit(1)

    template_path = sys.argv[1]
    reference_content_path = sys.argv[2]
    output_path = sys.argv[3]
    existing_content_path = sys.argv[4] if len(sys.argv) > 4 else None
    reference_source = sys.argv[5] if len(sys.argv) > 5 else None

    result = write_report(
        template_path,
        reference_content_path,
        output_path,
        existing_content_path,
        reference_source
    )

    print(f"模式: {result['mode']}")
    print(f"输出: {result['output_path']}")
    print(f"来源: {result['reference_source']}")
    print("\n生成的提示词：")
    print(result['prompt'])