# 路径约定 (Path Conventions)

本 skill 所有路径**相对于 cwd `REPORT_DATA_PATH`**（即 `D:\quant\stock_report_analyzer\report_data`）。

## 输入路径

| 文件 | 路径 | 来源 | 必填 |
|------|------|------|------|
| 参考资料 | `{公司}/md/research_file/参考资料/{公司}_三年综合数据.md` | stage0 | ✅ |
| 业务概况 | `{公司}/md/research_file/{公司}_业务概况.md` | stage1 | ✅ |
| 行业分析 | `{公司}/md/research_file/{公司}_行业分析.md` | stage1 | ✅ |
| 最近一期年报原文 | `{公司}/md/clean/{公司}{最近年}年年报/管理层讨论/*.md` | stage0 输入 | 推荐 |

## 输出路径

| 文件 | 路径 |
|------|------|
| 公司分析报告 | `{公司}/md/research_file/{公司}_公司报告.md` |

## 日志路径

| 文件 | 路径 |
|------|------|
| LLM 调用日志 | `{公司}/output/log/llm_log_{timestamp}.txt` |

## 命名规则

| 元素 | 规则 |
|------|------|
| `{公司}` | 与目录名一致（如「宁德时代」「贵州茅台」） |
| `{最近年}` | 从 stage1 业务概况标题中提取的最大年份（如 2025） |
| `{YYYY-MM-DD}` | 报告落盘当天的日期 |

## 路径硬规则

1. **写文件必须用 `/d/...` 前缀**：避免 M3 sandbox 拦截绝对路径写入
2. **不使用反斜杠 `\`**：Windows 路径用 `/` 或 `\\` 都行，但用 `/d/...` 最稳
3. **不依赖环境变量**：路径直接拼接 cwd + 相对路径

## 路径示例

```
D:\quant\stock_report_analyzer\report_data\
├── 宁德时代\
│   ├── md\
│   │   ├── clean\
│   │   │   └── 宁德时代2025年年度报告\
│   │   │       └── 管理层讨论\*.md
│   │   └── research_file\
│   │       ├── 参考资料\
│   │       │   └── 宁德时代_三年综合数据.md
│   │       ├── 宁德时代_业务概况.md
│   │       ├── 宁德时代_行业分析.md
│   │       └── 宁德时代_公司报告.md        ← 本 skill 产物
│   └── output\
│       └── log\
│           └── llm_log_*.txt
```

## 与 stage1 的路径对比

| skill | 产物 | 路径 |
|-------|------|------|
| stage0 | 三年综合数据 | `{公司}/md/research_file/参考资料/{公司}_三年综合数据.md` |
| stage1 | 业务概况 | `{公司}/md/research_file/{公司}_业务概况.md` |
| stage1 | 行业分析 | `{公司}/md/research_file/{公司}_行业分析.md` |
| **stage2** | **公司报告** | **`{公司}/md/research_file/{公司}_公司报告.md`** |