# 深交所年报下载流程

适用代码前缀：`000` / `001` / `002` / `003` / `300`（含创业板 300）。

## 入口

https://www.szse.cn/disclosure/listed/fixed/index.html

## 关键差异（SZSE vs SSE）

| 维度 | SZSE | SSE |
|------|------|-----|
| 公告类别 | **自定义下拉**（非 native `<select>`） | native `<select>` |
| 代码输入 | text + typeahead（输入后选推荐） | 纯 text |
| PDF URL 在 | `<a>` 的 **`attachpath` 属性** | `<a>` 的 `href`（在 detail 页里） |
| WAF | **无**（disc.static.szse.cn 是静态 CDN） | 有 acw_sc__v2 JS 挑战 |
| curl 方式 | 直接 `curl -A "<UA>"` 即可 | 必须先拿 cookie 再带 cookie curl |

**好消息：SZSE 下载比 SSE 简单** —— 不用反爬绕过，curl 直接拿真实 PDF。

## 步骤

### 1. 打开公告搜索页

```
mcp__plugin_playwright_playwright__browser_navigate
  url: https://www.szse.cn/disclosure/listed/fixed/index.html
```

### 2. 填代码（用 native setter + 触发 typeahead）

代码 input 是 `#input_code`，但**直接设 value 会被自定义组件清空**。必须用 native setter：

```
mcp__plugin_playwright_playwright__browser_evaluate
  function: () => {
    const inp = document.getElementById('input_code');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(inp, '002594');
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    return new Promise(r => setTimeout(() => {
      // 验证 typeahead 出现
      const menu = document.getElementById('c-typeahead-menu-1');
      r({ 
        ok: !!menu && menu.querySelectorAll('li').length > 0,
        items: menu ? Array.from(menu.querySelectorAll('li a')).map(a => a.textContent.trim()) : []
      });
    }, 1500));
  }
```

返回 `{ok: true, items: ["002594  比亚迪"]}` 表示 typeahead 出现。

> 注意：不能用 `browser_type` —— 它不触发 typeahead 的 AJAX 推荐请求。

### 3. 选「公告类别 = 年度报告」

`公告类别` 是自定义下拉按钮 `a.c-selectex-btn`：

```
mcp__plugin_playwright_playwright__browser_evaluate
  function: () => {
    // 1. 点击 typeahead 建议（确认公司）
    const sug = document.querySelector('#c-typeahead-menu-1 li.active a');
    if (sug) sug.click();
    
    // 2. 点击公告类别下拉
    const catBtn = Array.from(document.querySelectorAll('a.c-selectex-btn'))
      .find(a => a.textContent.includes('公告类别'));
    if (!catBtn) return { error: 'no cat button' };
    catBtn.click();
    
    // 3. 点选「年度报告」（菜单项是 a 标签）
    return new Promise(r => setTimeout(() => {
      const items = Array.from(document.querySelectorAll('ul.dropdrow-list li a'));
      const target = items.find(a => a.textContent.trim() === '年度报告');
      if (!target) return r({ error: 'no 年度报告 item', items: items.map(a => a.textContent.trim()) });
      target.click();
      r({ ok: true, catBtnText: catBtn.textContent.trim() });
    }, 300));
  }
```

### 4. 点击「查询」按钮

查询按钮是 `button` "查询"：

```
mcp__plugin_playwright_playwright__browser_evaluate
  function: () => {
    const btn = Array.from(document.querySelectorAll('button, a.btn'))
      .find(b => b.textContent.trim() === '查询');
    if (btn) { btn.click(); return { clicked: '查询' }; }
    return { error: 'no 查询 button' };
  }
```

### 5. 等待结果加载

```
mcp__plugin_playwright_playwright__browser_wait_for
  time: 2
```

### 6. 提取所有年报行的 attachpath

**关键**：SZSE 表格里每行 title 单元格是 `<a class="annon-title-link" attachid="..." attachformat="pdf" attachpath="/disc/disk03/finalpage/.../xxx.PDF" href="...">`

`attachpath` 属性就是 PDF 直链（相对路径）。**不要点 href，那是详情页**。

```
mcp__plugin_playwright_playwright__browser_evaluate
  function: () => {
    const rows = Array.from(document.querySelectorAll('tr'));
    const main = {};  // year -> {title, attachpath}
    for (const row of rows) {
      const txt = row.textContent;
      if (!/\d{4}年年度报告/.test(txt)) continue;
      const link = row.querySelector('a.annon-title-link');
      if (!link) continue;
      const title = link.dataset.title || '';
      if (/摘要|英文|修订|更正/.test(title)) continue;
      const m = title.match(/(\d{4})年年度报告/);
      if (!m) continue;
      const year = m[1];
      if (main[year]) continue;  // 第一个就是原始版
      main[year] = { 
        title, 
        attachpath: link.getAttribute('attachpath'),
        attachid: link.getAttribute('attachid')
      };
    }
    return main;
  }
```

返回 `{2025: {attachpath: "/disc/disk03/..."}, 2024: {...}, 2023: {...}}`。

### 7. curl 下载（无 WAF，直接搞定）

完整 PDF URL：`https://disc.static.szse.cn{attachpath}`

```bash
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
for year in 2025 2024 2023; do
  case $year in
    2025) path="/disc/disk03/finalpage/2026-03-28/f5cdbdbf-b138-4e83-8c6d-5a7eecb9e670.PDF";;
    2024) path="/disc/disk03/finalpage/2025-03-24/fe401102-25a6-40ea-8cb2-6c6eab1bd599.PDF";;
    2023) path="/disc/disk03/finalpage/2024-03-26/6f1e78f2-fbdf-4553-8867-3930dc09a4f3.PDF";;
  esac
  curl -L --compressed -A "$UA" -s \
    -o "D:/quant/report_data/{company_name}/pdf/original/{company_name}${year}年年度报告.pdf" \
    "https://disc.static.szse.cn${path}"
done
```

### 8. 验证

```bash
ls -la "D:/quant/report_data/{company_name}/pdf/original/"*.pdf
file *.pdf  # 应输出 "PDF document, version 1.x, NNN page(s)"
```

PDF 应 ≥ 1 MB（比亚迪/宁德时代级别年报通常 5-10 MB）。

## 异常情况

| 现象 | 处理 |
|------|------|
| typeahead 不出现 | snapshot 截 input 区域，看 `c-typeahead-menu-1` 是否被改 id；可能等 2s |
| 「公告类别」下拉点了没反应 | snapshot 找所有 `a.c-selectex-btn`，确认 class 仍是 `c-selectex-btn` |
| 「年度报告」菜单项找不到 | snapshot 截 `ul.dropdrow-list`，看实际 options；可能 SZSE 改文案 |
| 表格加载但无 002594 比亚迪 | 检查 `input_code` 的 typeahead 是否成功确认（点击 li.active.a 后表格应自动刷新） |
| curl 拿 0 字节 / 403 | 加 `-A` UA；或换 `https://www.szse.cn` 前缀（disc.static 是 CDN，主域也可能直接 serve） |

## 速度优化

- SZSE 一次浏览器会话可查多家公司，**只填代码不同**（typeahead 速度快）
- 表格加载后用 `browser_evaluate` 一次提取 3 年的 attachpath，再串行 curl（curl 极快）
- curl 后无需 close 浏览器，直接 `browser_navigate` 到新 URL 即可继续

