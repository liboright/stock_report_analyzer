# SubAgent 产物契约

`report_writer` SubAgent 的两种写作模式、产物格式与落盘机制。

## 目录

- [两种写作模式](#两种写作模式)
- [产物契约](#产物契约)
- [落盘机制（方案 B）](#落盘机制方案-b)
- [SubAgent prompt 必带指令](#subagent-prompt-必带指令)
- [增量写作原则](#增量写作原则)

## 两种写作模式

| 模式 | 触发条件 | `existing_content_path` |
|------|---------|------------------------|
| 完整写作 | 首个章节 | None |
| 增量补充 | 后续章节 | 已有文档路径 |

完整写作：用 `assets/templates/business_profile.md` 作骨架，从空开始。
增量补充：保持已有内容不变，只补充缺失数据。

## 产物契约

SubAgent result 文本必须包含**恰好 2 个 markdown 代码块**：

| 产物 | 代码块首行 | 目标文件 |
|---|---|---|
| 业务概况 | `# {公司}公司业务概况` 或包含「业务概况」 | `{公司}_业务概况.md` |
| 行业分析 | `# {公司} 行业分析` 或包含「行业分析」 | `{公司}_行业分析.md` |

runner 按代码块首行标题语义分发到目标文件。

## 落盘机制（方案 B）

**职责划分**：

| 角色 | 职责 |
|---|---|
| SubAgent | 只产内容，**不写文件** |
| main agent | 汇总多次调用结果，提取最终 2 个代码块 |
| claude_skill_runner.py | 解析代码块，Python `Path.write_text` 落盘 |

**为什么 SubAgent 不写文件**：
- Write/Edit/heredoc 在 M3 sandbox 长流程里不可靠
- 写文件只发生一次（runner 内部 Python），不走 sandbox，可靠性 100%

**路径**：runner 自动计算 `D:/quant/report_data/{公司}/md/research_file/...`，agent 不用关心。

## SubAgent prompt 必带指令

拼接 prompt 时务必带上：

```
- 禁止调 Write / Edit / Bash 写文件
- 在 result 文本里用 markdown 代码块输出文档最终内容
- 代码块标题：
  - 业务概况：# {公司}公司业务概况
  - 行业分析：# {公司} 行业分析
- 2 个代码块都要返回完整内容（不要省略）
```

## 增量写作原则

- 已有内容中的数据和表述保持不变
- 仅从当前章节中提取本轮缺失的数据/信息
- 不要重新生成或改写已有段落
- 若需补充多年数据，分多次迭代，每次只增加一个年份
