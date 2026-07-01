# 工作流程 (Workflow)

本 skill 的 8 阶段闭环流程详细说明，包括每个阶段的输入/输出/调用方式，以及 4 个 prompt 模板（write / mainline / rewrite / review）。

## 目录

- [总览](#总览)
- [阶段 1: 依赖检查](#阶段-1-依赖检查)
- [阶段 2: 读取 3 个输入文件](#阶段-2-读取-3-个输入文件)
- [阶段 3: 识别最近一期年报](#阶段-3-识别最近一期年报)
- [阶段 4: 生成 v1 初稿](#阶段-4-生成-v1-初稿report_writer--write-mode)
- [阶段 5: 提炼叙事主线](#阶段-5-提炼叙事主线report_writer--mainline-mode)
- [阶段 6: 基于主线重写 v2](#阶段-6-基于主线重写-v2report_writer--rewrite-mode)
- [阶段 7: 审查 v2](#阶段-7-审查-v2report_reviewer)
- [阶段 8: 润色循环](#阶段-8-润色循环-max_polish_rounds-轮)
- [阶段 9: 落盘 + 验证](#阶段-9-落盘--验证)
- [阶段 10: Session 汇总](#阶段-10-session-汇总)

## 总览

```
[1 依赖检查] → [2 读 3 份输入] → [3 识别最近一期年报]
                                            ↓
                       [4 写 v1 初稿: report_writer write]
                                            ↓
                       [5 提炼叙事主线: report_writer mainline]
                                            ↓
                       [6 基于主线重写 v2: report_writer rewrite]
                                            ↓
                       [7 审查 v2: report_reviewer]
                                            ↓
                                       pass?
                                       /    \
                                     yes    no
                                      ↓      ↓
                            [9 落盘最终版]   [8 润色循环 (≤max_polish_rounds 轮)]
                                            ↓
                                       [9 落盘最终版]
                                            ↓
                                       [10 Session 汇总]
```

## 阶段 1: 依赖检查

见 SKILL.md「强制依赖检查」代码块。

## 阶段 2: 读取 3 个输入文件

```python
ref_content    = Read(f"{公司}/md/research_file/参考资料/{公司}_三年综合数据.md")
business_md    = Read(f"{公司}/md/research_file/{公司}_业务概况.md")
industry_md    = Read(f"{公司}/md/research_file/{公司}_行业分析.md")
log_step(step_name="Read - 3 个前置产物",
         data=f"参考资料 {len(ref_content)} 字符, 业务概况 {len(business_md)} 字符, 行业分析 {len(industry_md)} 字符")
```

## 阶段 3: 识别最近一期年报

```python
from pathlib import Path
import re
years_in_doc = re.findall(r"20\d{2}", business_md[:500])
latest_year = max(int(y) for y in years_in_doc)  # 如 2025

latest_dir = f"{公司}/md/clean/{公司}{latest_year}年年报/管理层讨论/"
if Path(latest_dir).exists():
    log_step(step_name="定位最近一期年报", data=latest_dir)
    latest_files = sorted(Path(latest_dir).glob("*.md"))
    # 选 2-3 个最关键章节读入 prompt
else:
    log_step(step_name="定位最近一期年报", data=f"未找到 {latest_dir}，第七节将降级")
```

**为什么读最近一期原文？** stage1 业务概况是「三年整合」叙述，**不突出最近一年的事件细节**。深度解读需要从最近一年年报里捞：当年新签合同、新建产能、新发布产品、董事长致辞等。

## 阶段 4: 生成 v1 初稿（report_writer · write mode）

**目标**：覆盖完整优先 — 9 节齐全 + 数据齐全。**不强调措辞**（交给后续主线 + 重写）。

**调用前**：`logger.log_start(agent_name="report_writer", task_description="写 v1 初稿", metadata={"company": company, "mode": "write", "round": 1})`

**调用后**：`logger.log_end(call_id, result_summary=f"v1 {字节数} 字节")`

**Prompt 模板**：

```
你是上市公司研究分析师。基于以下 4 份材料，写一份公司分析报告初稿。

# 公司
{company}

# 输入材料（按优先级）
1. 【数据底稿】三年综合数据（{字节数} 字符）：{ref_content}
2. 【业务叙述】业务概况（{字节数} 字符）：{business_md}
3. 【行业叙述】行业分析（{字节数} 字符）：{industry_md}
4. 【最近一期年报·关键章节】：
   - {latest_file_1}: {content_1}
   - {latest_file_2}: {content_2}

# MODE
write  (初稿，优先覆盖与数据完整)

# 任务
按照模板 `assets/templates/report.md` 生成《{公司} 公司分析报告·初稿》。
模板包含 9 节，每节有明确的写作要求与字数区间。

# 写作要求（v1 阶段）
1. **数据驱动**：所有数字必须来自输入材料，禁止编造；缺失写「未披露」
2. **覆盖优先**：9 节全部写到，模板字段全部填上
3. **最近一期原文驱动第七节**：必须从最近一年年报里捞出新事件
4. **不输出投资建议**：不给目标价/评级/买卖建议

# 落盘
- 用 Write 工具写入：{公司}/md/research_file/_drafts/{公司}_v1.md
- UTF-8，markdown，9 节齐全
- 完成后输出"完成 v1"两字
```

## 阶段 5: 提炼叙事主线（report_writer · mainline mode）

**目标**：从 v1 + 3 份原始材料中提炼出"读者读完后应该带走的故事"。主线 = 贯穿 9 节的论点 + 横向贯穿主题。

**调用前**：`logger.log_start(agent_name="report_writer", task_description="提炼叙事主线", metadata={"company": company, "mode": "mainline"})`

**调用后**：`logger.log_end(call_id)` → mainline 内容由 main agent 落盘到 `参考资料/{公司}_报告主线.md`。

**Prompt 模板**：

```
你是上市公司研究分析师。阅读 v1 初稿 + 3 份原始材料，提炼本报告的**叙事主线**。

# 公司
{company}

# 输入材料
1. v1 初稿：{v1_content}
2. 数据底稿：{ref_content}
3. 业务叙述：{business_md}
4. 行业叙述：{industry_md}

# MODE
mainline  (不重写报告，只产出主线文档)

# 主线文档结构
按下面格式输出 markdown 文本（**不要落盘**，main agent 会用 Write 工具落盘）：

# {公司} 公司分析报告 · 叙事主线

## 一句话定位
<150 字以内：行业地位 + 主营业务 + 核心数字>

## 核心故事（2-3 段，每段 100-200 字）
<读者读完后应该带走的故事，包含 3 个层次：
 - 商业本质（这家公司靠什么赚钱）
 - 当前位置（在行业中处于什么位置，最近一年发生的关键变化）
 - 未来方向（增长驱动 + 主要风险）>

## 9 节主线论点
### 一、执行摘要
- 论点 1
- 论点 2
- 论点 3
### 二、公司画像
- ...
### 三、主要业务
- 板块 1 的核心论点
- 板块 2 的核心论点
- ...
### 四、新业务
- ...
### 五、盈利模式
- ...
### 六、发展现状与趋势
- ...
### 七、最近一期财务深度解读
- ...
### 八、风险提示
- ...
### 九、未来展望
- ...

## 横向贯穿主题（3-5 个）
<需要在多节中保持一致的论点/数据/表述，例如：
 - 主题 1：全球化战略（业务第三节提 / 财务第五节提 / 趋势第六节提 → 全文口径一致）
 - 主题 2：储能业务作为第二曲线（新业务第四节提 / 财务第五节提 / 展望第九节提 → 口径一致）
 - ...

## 关键数据点（全文反复引用的 5-8 个）
- 营收 / 净利润 / 增速
- 核心赛道市占率
- 关键业务板块占比
- 现金流/盈利质量指标
（这些数据在 9 节中应保持一致表述，不要在不同节给出矛盾数字）

## 风格基调
<一段话描述本报告的整体语气，例如：
 "客观数据驱动，强调行业地位与战略转型；不出现情感性形容词；专业术语保留英文原文。">

# 输出
返回上述格式的完整 markdown 文本，**不要落盘**。完成后输出"主线已就绪"两字。
```

## 阶段 6: 基于主线重写 v2（report_writer · rewrite mode）

**目标**：v2 是**真正的对外版本**。以主线为骨架重新组织 9 节，强制去重、措辞润色、贯穿横向主题。

**调用前**：`logger.log_start(agent_name="report_writer", task_description="重写 v2", metadata={"company": company, "mode": "rewrite", "round": 2})`

**Prompt 模板**：

```
你是上市公司研究分析师。基于 v1 初稿 + 叙事主线 + 3 份原始材料，**重写**为对外版本 v2。

# 公司
{company}

# 输入材料
1. 【叙事主线】(主骨架): {mainline_content}
2. 【v1 初稿】(参考但不直接复制): {v1_content}
3. 【数据底稿】: {ref_content}
4. 【业务叙述】: {business_md}
5. 【行业叙述】: {industry_md}
6. 【最近一期年报·关键章节】: {latest_chapters}

# MODE
rewrite  (基于主线重写，强调贯穿 / 去重 / 措辞)

# 任务
按主线骨架 + 模板 `assets/templates/report.md` 重新写《{公司} 公司分析报告·v2》。

# 写作要求（v2 阶段，区别于 v1）
1. **主线贯穿**：9 节都按叙事主线骨架来写，不要各写各的
2. **强制去重**：
   - 同一数字/事实在多节出现时，只在主线规定的章节展开，其他章节引用而不重复
   - 第三/第七节如果都讲市占率，明确分工（第三节=板块地位，第七节=最近一年变化）
3. **横向主题一致**：主线列出的"横向贯穿主题"在多节中保持口径一致
4. **数据一致**：主线列出的"关键数据点"在 9 节中数值/表述完全一致，不允许矛盾
5. **措辞润色**：
   - 避免直接复制 stage1 业务概况原文
   - 避免连续 3 个以上短句堆叠
   - 避免「公司...公司...公司」开头
   - 段落长度 100-300 字
6. **数据驱动**：所有数字必须来自输入材料
7. **不输出投资建议**

# 落盘
- 用 Write 工具写入：{公司}/md/research_file/_drafts/{公司}_v2.md
- UTF-8，markdown，9 节齐全
- 完成后输出"完成 v2"两字
```

## 阶段 7: 审查 v2（report_reviewer）

**目标**：用结构化审查量化 v2 的问题，输出 JSON 报告。Pass 标准：`critical_issues` 为空 且 评分 ≥ 7。

**调用前**：`logger.log_start(agent_name="report_reviewer", task_description=f"审查 v{round}", metadata={"company": company, "round": round})`

**调用后**：main agent 解析 JSON 落盘到 `_drafts/{公司}_review_v{N}.json`，判断 `pass` 与 `critical_issues`。

**Prompt 模板**（传给 `general-purpose` SubAgent）：

```
你是公司分析报告的审查员。审查 v2 报告是否达到对外发布标准。

# 公司
{company}

# 输入材料
1. 【v2 报告】(待审查): {v2_content}
2. 【数据底稿】(事实基准): {ref_content}
3. 【业务叙述】(事实基准): {business_md}
4. 【行业叙述】(事实基准): {industry_md}
5. 【叙事主线】(设计意图): {mainline_content}
6. 【报告模板】(结构基准): {template_summary}

# 任务
按以下 4 个维度审查 v2 报告，输出 JSON。

## 审查维度
1. **信息重复**：同一事实在多节反复出现（>2 次）？
2. **数据缺失**：模板要求的字段是否齐全？事实是否能在 3 份原始材料中找到？
3. **语言质量**：是否生硬、堆砌、缺乏过渡？
4. **整体逻辑**：9 节是否围绕主线展开？横向主题是否一致？是否有逻辑断裂？

## 输出格式
**只输出 JSON，不要解释，不要 markdown 代码块包裹**：

{
  "score": <1-10 整数>,
  "pass": <bool, pass 标准: critical_issues 为空且 score >= 7>,
  "summary": "<整体评价 1-2 句>",
  "critical_issues": [
    {
      "type": "信息重复" | "数据缺失" | "逻辑断裂" | "事实错误" | "章节缺失",
      "location": "<节名 / 段号>",
      "description": "<具体问题>",
      "suggestion": "<修复建议>"
    }
  ],
  "minor_issues": [
    {
      "type": "语言生硬" | "措辞" | "表格" | "结构",
      "location": "<节名 / 段号>",
      "description": "<具体问题>",
      "suggestion": "<修复建议>"
    }
  ],
  "section_feedback": {
    "执行摘要": "<一句话评价 + 改进方向>",
    "公司画像": "...",
    "主要业务": "...",
    "新业务": "...",
    "盈利模式": "...",
    "发展现状与趋势": "...",
    "最近一期财务深度解读": "...",
    "风险提示": "...",
    "未来展望": "..."
  }
}

# 落盘
**不要落盘**（不要调用 Write），把 JSON 字符串直接返回 main agent。
完成后输出"审查完毕"两字。
```

## 阶段 8: 润色循环（≤ max_polish_rounds 轮）

**触发条件**：审查未通过 且 当前轮数 < `max_polish_rounds`（默认 2）。

**每轮流程**：

```
输入:
  - 上一版报告 v_n
  - 上一版审查报告 review_v_n
  - 3 份原始材料 (ref / business / industry)
  - 叙事主线 mainline
处理:
  1. 调 report_writer (rewrite mode) → v_{n+1}
  2. 调 report_reviewer → review_v_{n+1}
  3. 判断 pass: 是 → 退出循环；否 → 继续
退出条件:
  - 审查通过 → v_{n+1} 为最终版
  - 达到 max_polish_rounds 仍未通过 → v_{n+1} 为最终版 + session summary 标注「审查未完全通过，残留 N 个 critical issue」
```

**Prompt 模板**（rewrite mode，针对润色）：

```
你是上市公司研究分析师。审查报告指出了 v{n} 的若干问题，请基于反馈**重写**为 v{n+1}。

# 公司
{company}

# 输入材料
1. 【v{n} 当前版本】(要改进的): {v_n_content}
2. 【v{n} 审查报告】(要解决的问题): {review_v_n_json}
3. 【叙事主线】(贯穿依据): {mainline_content}
4. 【数据底稿】(事实基准): {ref_content}
5. 【业务叙述】(事实基准): {business_md}
6. 【行业叙述】(事实基准): {industry_md}
7. 【最近一期年报·关键章节】(事实基准): {latest_chapters}

# MODE
rewrite  (基于审查报告润色，强调修复 critical_issues)

# 任务
按 v{n} 审查报告逐条修复后，重写为《{公司} 公司分析报告·v{n+1}》。

# 写作要求
1. **必修 critical_issues**：
   - 对审查报告中每条 critical_issue，在 v{n+1} 中明确处理
   - 处理方式: 删除/重写/调整位置，并在文末加注释「[本节已处理: critical_issue N]」便于回溯
2. **可选修 minor_issues**：在不影响篇幅的前提下尽量修
3. **保持主线**：不要为了修问题而偏离叙事主线
4. **不引入新事实**：所有数据必须能在 3 份原始材料中找到
5. **数据一致**：与 v{n} 的数据必须一致（不允许偷偷改数字）
6. **章节齐全**：9 节不能因为润色而缺失
7. **不输出投资建议**

# 落盘
- 用 Write 工具写入：{公司}/md/research_file/_drafts/{公司}_v{n+1}.md
- UTF-8，markdown，9 节齐全
- 完成后输出"完成 v{n+1}"两字
```

**循环控制伪代码**：

```python
max_rounds = 2  # 默认 2，可由用户覆盖
current_round = 2  # v2 是当前轮
current_version = v2_content
current_review = review_v1_json

while not current_review["pass"] and current_round < 2 + max_rounds:
    next_round = current_round + 1
    # 调 report_writer rewrite → v_{next_round}
    v_next = call_report_writer_rewrite(
        current_version=current_version,
        review=current_review,
        mainline=mainline_content,
        ref=ref_content,
        business=business_md,
        industry=industry_md,
        latest=latest_chapters,
        n=current_round,
    )
    # 调 report_reviewer
    review_next = call_report_reviewer(
        v_content=v_next,
        ref=ref_content,
        business=business_md,
        industry=industry_md,
        mainline=mainline_content,
        round=next_round,
    )
    current_version = v_next
    current_review = review_next
    current_round = next_round

# 退出: 通过 / 达到 max_rounds
final_version = current_version
final_round = current_round
passed = current_review["pass"]
```

**成本估算**：每轮 1 report_writer + 1 report_reviewer ≈ 2 LLM 调用。总调用次数 = 1 (v1) + 1 (mainline) + 1 (v2) + 2 × max_rounds。默认 max_rounds=2 时 = 7 次 LLM 调用。

## 阶段 9: 落盘 + 验证

```python
from pathlib import Path

# 落盘最终版
final_path = f"{公司}/md/research_file/{公司}_公司报告.md"
final_version = current_version  # v_{final_round}
Write(final_path, final_version)

# 验证
if not Path(final_path).exists():
    raise RuntimeError(f"报告文件未生成: {final_path}")

content = Read(final_path)
required_sections = [
    "执行摘要", "公司画像", "主要业务", "新业务",
    "盈利模式", "发展现状与趋势", "最近一期财务深度解读",
    "风险提示", "未来展望",
]
missing_sections = [s for s in required_sections if s not in content]
if missing_sections:
    raise RuntimeError(f"报告缺失必备章节：{missing_sections}")

log_step(step_name="验证 - 报告产物",
         data=f"最终版本: v{final_round}, 字节数: {len(content)}, 9 节齐全, 审查{'通过' if passed else '未通过'}")
```

## 阶段 10: Session 汇总

`log_session_summary` 记录：
- 完成公司名
- 最终报告路径 + 字节数 + 最终版本号（v2/v3/v4）
- 审查结果（通过/未通过 + 最终评分 + 残留 critical issue 数）
- 润色轮数（实际跑了多少轮）
- 最近一期年份
- 输入材料字符数
- 调用的 SubAgent 次数（write / mainline / rewrite / review 分别多少）
