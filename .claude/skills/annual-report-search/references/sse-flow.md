# 上交所年报下载流程

适用代码前缀：`600` / `601` / `603` / `605` / `688`（含科创板 688）。

## 入口

https://www.sse.com.cn/disclosure/listedinfo/regular/

## 步骤

### 1. 打开定期报告页

```
mcp__plugin_playwright_playwright__browser_navigate
  url: https://www.sse.com.cn/disclosure/listedinfo/regular/
```

### 2. 获取页面结构

```
mcp__plugin_playwright_playwright__browser_snapshot
```

定位：
- 「证券代码」输入框（`#inputCode`）
- 「报告类型」下拉（Bootstrap selectpicker，`value="YEARLY"` 代表「年报」）
- 右侧公告列表区

**重要**：这个表单**没有「查询」按钮**（股票代码旁的 🔍 是图标，不是搜索按钮），**也没有日期范围必填**。SSE 监听 `<select>` 的 change 事件，**选完 YEARLY 自动 XHR 拉数据**（不指定日期 = 不限日期，返回该股票全部历史年报）。

### 3. 填左侧搜索表单（最少步骤：只需 2 步）

#### 3.1 输入证券代码

```
mcp__plugin_playwright_playwright__browser_type
  element: 证券代码输入框
  target: <ref>
  text: "600519"
```

#### 3.2 选择「报告类型 = 年报」

```
mcp__plugin_playwright_playwright__browser_select_option
  element: 报告类型下拉
  target: <ref>
  values: ["YEARLY"]   # value 是 "YEARLY"，不是 "年报" 两个字
```

> ⚠️ **必须用 Playwright 原生 `browser_select_option`**，**不要用** `browser_evaluate` 设 `sel.value` + dispatchEvent。Bootstrap selectpicker 内部缓存了选中状态，JS 直接设 value 后 picker 仍认为"未选中"，XHR 拉的是**所有 SSE 公司年报**（不是这只股票的），匹配会错位。

**选完 select 后立即自动 XHR**，约 1-2s 出结果。

### 4. 读取结果列表

```
mcp__plugin_playwright_playwright__browser_wait_for
  time: 2
```

```
mcp__plugin_playwright_playwright__browser_snapshot
```

右侧列表按公告时间倒序展示。每行通常含：标题 / 公告日期 / 公告类型。

### 5. 定位所有目标年报（多年批量）

**重要**：SSE 选完 YEARLY 后**一次性** XHR 拉出**该公司所有历史年报**（不只你请求的年份）。所以可以在同一个浏览器会话里**依次处理多年的年报**，cookie 只需取一次。

对每个 `year in years`，在当前列表里找标题形如 `XXX {year}年年度报告` 的行。**过滤规则**：
- ✅ 保留：`XXX 2025年年度报告` / `XXX2025年年度报告` / `XXX 2025 年年度报告`
- ❌ 跳过：标题含「摘要」「(修订)」「(更正)」「(更新)」「英文」/ `Annual Report`
- 若同一年多条：取**公告日期最早**的那条 = 原始版本

记录每条目标行的：
- `target_year`（如 2025）
- 标题行的 element ref（用于 `browser_click`）
- 列表行 `/url` 字段（用于拼 PDF 直链，常用）

把多年目标在内存里组成一个 `tasks = [(year1, ref1, url1), (year2, ref2, url2), ...]`，**一次性**进入步骤 6 循环处理。

> **重点**：SSE 列表不像 SZSE 有"下载"按钮，而是**点标题进入详情页**再下载。

### 6. 批量下载循环（cookie 复用一次）

**⚠️ 重要：SSE 有 JS WAF 反爬（acw_sc__v2 cookie）**

直接 `curl` PDF URL 会拿到 7KB 的反爬 HTML 页面（gzip 压缩），不是真实 PDF。**必须先让浏览器执行 JS 拿到 WAF cookie**，再带 cookie 调 curl。

**第 1 步：取一次 cookie**（年份循环开始前做一次）

在当前列表页直接 evaluate 拿 cookie（点击年份链接前，浏览器已经在 SSE 域跑过 JS，cookie 应已就位）：

```
mcp__plugin_playwright_playwright__browser_evaluate
  function: () => document.cookie
```

把 `document.cookie` 字符串存到变量 `COOKIE`（**整个 session 内复用**，acw_sc__v2 大约 30 分钟有效）。

**第 2 步：for year, ref, url in tasks 循环**

对每个目标年，复用同一份 `COOKIE`：

1.  **点开详情页**（拿 PDF 直链 + 触发 WAF cookie refresh）

    ```
    mcp__plugin_playwright_playwright__browser_click
      element: {year} 年年报标题链接
      target: <ref>
    ```

    浏览器跳到详情页 / 或直接跳 `static.sse.com.cn/...pdf`。等 1-2s 加载：

    ```
    mcp__plugin_playwright_playwright__browser_wait_for
      time: 2
    ```

2.  **拿 PDF 真实 URL**（任选其一）

    **方式 1**（推荐）：如果浏览器直接跳到 PDF 预览页，用 `browser_evaluate` 取 `window.location.href`：

    ```
    mcp__plugin_playwright_playwright__browser_evaluate
      function: () => window.location.href
    ```

    **方式 2**：如果跳到详情页，snapshot 找"查看 PDF" 链接 / `<a href="...pdf">` / `<embed src="...pdf">`，用 `browser_evaluate` 抓：

    ```
    mcp__plugin_playwright_playwright__browser_evaluate
      function: () => {
        const a = document.querySelector('a[href$=".pdf"]');
        if (a) return a.href;
        const e = document.querySelector('embed[src$=".pdf"], object[data$=".pdf"]');
        if (e) return e.src || e.data;
        return null;
      }
    ```

    **方式 3**：表格行链接就在 snapshot 里（实际最常见，URL 在 `/url:` 字段）。不点链接也能用 `https://static.sse.com.cn` 前缀 + `/url` 拼成 PDF 直链。

3.  **curl 带 cookie + UA 下载**（**复用步骤 1 取的 COOKIE**）

    ```bash
    UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    curl -L --compressed -A "$UA" -b "$COOKIE" \
         -o "<output_dir>/{company_name}{year}年年度报告.pdf" \
         "https://static.sse.com.cn/<拼接的 PDF 路径>"
    ```

    关键参数：
    - `-A` UA 必填，否则被 WAF 拦截
    - `-b` cookie 必填，缺了拿 7KB HTML
    - `--compressed` 自动解压响应
    - PDF URL 域名是 **`static.sse.com.cn`**（不是 `www.sse.com.cn`），www 会 301/302 到 static

4.  **返回列表页**（处理下一年）

    ```
    mcp__plugin_playwright_playwright__browser_navigate_back
    ```

    等列表重新渲染：

    ```
    mcp__plugin_playwright_playwright__browser_wait_for
      time: 1
    ```

    → 回到 `for` 循环顶部，处理下一个 `(year, ref, url)`。

**第 3 步：循环结束后** 关闭浏览器或直接进 `### 10. 验证`

### 10. 验证

```bash
ls -la "<output_dir>/{company_name}{year}年年度报告.pdf"
```

文件应 ≥ 1 MB（科创板技术报告附图多，可能 5-30 MB）。

## 异常情况

| 现象 | 处理 |
|------|------|
| 报告类型下拉没"YEARLY" | snapshot 截全下拉，常用 value：`9`（上交所内部编码）或 `YEARLY` |
| 列表为空 / 列表是其他公司的年报 | 说明 selectpicker 没正确触发（value 改了但 picker 缓存没更新）。**改用 Playwright 原生 `browser_select_option`** 而不是 `browser_evaluate` 设 value |
| 列表只有"摘要" | 选错了公告类型（可能选成了"摘要"），重新 `browser_select_option("YEARLY")` |
| 详情页 5 秒空白 | `browser_navigate` 刷新，仍失败则跳过 |
| 详情页只有"在线阅读"，无"下载" | 在 PDF 阅读器工具栏找下载图标（snapshot 看 toolbar 区域） |
| 链接是相对路径 `/disclosure/...` | browser_evaluate 时拼 `window.location.origin + href` |

## 速度优化

- **多年批量（核心优化）**：SSE 选完 YEARLY 后**一次性** XHR 拉出该公司所有历史年报。在**同一个浏览器会话**里依次点开多年的年报：
  1. `browser_evaluate` 取 `document.cookie`（只需 1 次，存为 `COOKIE` 变量）
  2. 对每年：点开详情 → 拿 PDF URL → `curl -A -b "$COOKIE"` 下载 → `browser_navigate_back` 返回列表 → 处理下一年
  3. **不要**每下完一年就关浏览器、重新 `browser_navigate` 入口页、重新填代码、重新 `browser_select_option("YEARLY")`。3 份年报共享 1 次 cookie、1 次 XHR，**节省 1-2 分钟**
- 每次 select change 后等 `browser_wait_for time=2` 让列表稳定
- 返回列表后等 `browser_wait_for time=1` 让列表重新渲染
- 沪深两市切换时调 `browser_navigate` 到新页面（不能跨域复用 session）
