# 路径规范

## 沙箱硬规则

- claude 进程 cwd 是 `REPORT_DATA_PATH`（如 `D:/Quant/report_data/`）
- 所有数据在 `{公司}/` 子目录下，**没有顶层 `md/` 目录**
- 写文件必须用 `/d/...` 前缀（Unix 风格）避免 sandbox 拦截
- `D:/...` 或 `D:\\...` 会被 M3 sandbox 拦截
- 重命名/读取/查找可用任意风格

## 路径示例

| 路径 | 状态 | 说明 |
|---|---|---|
| `md/clean/贵州茅台2023年年报/管理层讨论/` | ❌ | 顶层没有 `md/` 目录 |
| `贵州茅台/md/clean/贵州茅台2023年年报/管理层讨论/` | ✅ | 正确（在公司子目录下）|
| `D:/Quant/report_data/贵州茅台/md/clean/...` | ✅ | 正确（绝对路径）|
| `D:/Quant/report_data/贵州茅台/md/clean/...` | ❌ 写文件时 | sandbox 拦截 |
| `/d/Quant/report_data/贵州茅台/md/clean/...` | ✅ 写文件 | Unix 风格前缀 |

## 落盘流程

1. 先 heredoc 写到 `/d/Quant/report_data/{公司}/md/research_file/{公司}_业务概况_{年份}.md`
2. 再用 `mv` 改名到 `/d/Quant/report_data/{公司}/md/research_file/{公司}_业务概况.md`
3. 行业分析同理

## 故障排查

- `ls 贵州茅台/` 报 "No such file or directory" → cwd 不对，需用绝对路径 `D:/Quant/report_data/...`
- 写文件无反应或权限错误 → 改用 `/d/...` 前缀
