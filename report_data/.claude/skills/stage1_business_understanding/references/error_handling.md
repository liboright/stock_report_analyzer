# 错误处理

skill 运行时各类异常的处理规则。

## 章节读取

| 条件 | 动作 |
|---|---|
| Read 成功 + `len(content) >= 200` | 调 `report_writer` 写两份文档 |
| Read 成功 + `len(content) < 200` | 跳过，记 `[skip-short]`，继续 |
| Read 失败（文件不存在/编码错误）| 跳过该章节，记 warning，继续下一章 |
| 章节含「详见本报告第 X 页」类引用 | 同步 Read `by_section/行业分析/` 下对应章节作补料 |

## SubAgent 失败

- 重试 1 次
- 仍失败 → 跳过该文档，在 session summary 标注「产物缺失」
- **不允许部分完成**：双文档保持同步，要么都成功要么都跳过

## 模板找不到

- fallback 到 5 节骨架（公司基本定位 / 产品 / 业绩 / 风险 / 行业）
- 在文档头部标注「fallback 模式」

## 财务数据缺失

- 进入第三阶段补全
- 调 `report_writer` SubAgent 回填（prompt 里明确说明「缺失 X、Y、Z 数据，请补全后返回完整 2 个代码块」）
- 仍缺失 → 在最终 result 里标注「数据缺失项」

## 路径错误

| 症状 | 原因 | 修复 |
|---|---|---|
| `No such file or directory` | cwd 不对 | `cd REPORT_DATA_PATH` 或用绝对路径 `D:/Quant/report_data/...` |
| 写文件无反应或权限错误 | M3 sandbox 拦截 `D:/...` 路径 | 改用 `/d/...` 前缀（Unix 风格）|
| bash 输出乱码 | Windows 编码问题 | 改用 `Glob` 工具代替 `ls` |

## 数据缺失值处理

年报未披露某指标时：

- 模板中：单元格写「未披露」，**不要留空也不要外推**
- 叙述中：说明「年报未披露该数据」
- 不允许根据其他年份外推
- 不允许用网络搜索补充
