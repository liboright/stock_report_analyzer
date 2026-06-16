"""年报 PDF 自动下载（深交所 / 上交所）。

设计要点：
- 严格按 `.claude/skills/annual-report-search/references/{szse,sse}-flow.md` 的 LLM 流程
  翻成 Playwright Python 代码（headless）。
- 关键差异：
  * SZSE（000/001/002/003/300）：无 WAF，表格行 `<a>` 的 `attachpath` 属性即 PDF 直链
    `https://disc.static.szse.cn{attachpath}`，直接 curl 即可
  * SSE（600/601/603/605/688）：有 acw_sc__v2 JS 挑战，必须先让浏览器 navigate 到
    PDF URL 让 WAF 解决并写入 cookie，再带 cookie curl
- 文件命名：`{company_name}{year}年年度报告.pdf`，与 `pdf_upload_service.py` 已登记
  的 raw 目录一致
- 入口：`download_one_year(stock_code, year, dest_dir, on_progress=...)`，
  按 stock_code 前缀自动分发到 SZSE / SSE
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

_log = logging.getLogger(__name__)


# ============= 公共常量 =============

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SZSE_PREFIXES = ("000", "001", "002", "003", "300")
SSE_PREFIXES = ("600", "601", "603", "605", "688")

# 深交所 disc.static.szse.cn 是静态 CDN，无 WAF
SZSE_PDF_HOST = "https://disc.static.szse.cn"
# 上交所 static.sse.com.cn 是 PDF 静态资源域名，www 会 301 过去
SSE_PDF_HOST = "https://static.sse.com.cn"

ProgressCb = Optional[Callable[[str], None]]


# ============= 数据结构 =============


@dataclass
class DownloadOutcome:
    year: int
    pdf_path: Path
    sha256: str
    file_size: int


# ============= 交易所识别 =============


def detect_exchange(stock_code: str) -> Literal["szse", "sse"]:
    """按 6 位代码前缀分交易所。其它前缀抛 ValueError。"""
    if not stock_code or len(stock_code) != 6 or not stock_code.isdigit():
        raise ValueError(f"非法股票代码: {stock_code!r}")
    if stock_code.startswith(SZSE_PREFIXES):
        return "szse"
    if stock_code.startswith(SSE_PREFIXES):
        return "sse"
    raise ValueError(f"未知交易所前缀: {stock_code!r}")


# ============= curl 工具 =============


def _curl_download(url: str, dest: Path, cookie: str = "", referer: str = "") -> Path:
    """调本机 curl 流式下载。SSE 必传 cookie + Referer；SZSE 可不传。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "curl",
        "-L",
        "--compressed",
        "-A",
        UA,
        "-s",
        "-o",
        str(dest),
    ]
    if referer:
        cmd += ["-e", referer]
    if cookie:
        cmd += ["-b", cookie]
    cmd.append(url)
    _log.info("curl %s -> %s", url, dest)
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    size = dest.stat().st_size if dest.exists() else 0
    if proc.returncode != 0 or size < 100_000:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"curl 失败 (rc={proc.returncode}, size={size}, stderr={proc.stderr[:200]!r})"
        )
    return dest


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ============= Playwright 上下文管理 =============


def _find_chromium_executable() -> Optional[str]:
    """扫描 ms-playwright 安装目录下的 chrome.exe，匹配当前 playwright python 库版本。
    库默认找 chromium-XXXX 但实际可能装了其他版本。"""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not base.exists():
        return None
    # 优先 headless_shell（更快）；其次完整 chromium
    for sub in sorted(base.glob("chromium_headless_shell-*"), reverse=True):
        cand = sub / "chrome-headless-shell-win64" / "chrome-headless-shell.exe"
        if cand.exists():
            return str(cand)
    for sub in sorted(base.glob("chromium-*"), reverse=True):
        cand = sub / "chrome-win64" / "chrome.exe"
        if cand.exists():
            return str(cand)
    return None


async def _open_browser() -> tuple["async_playwright", Browser, BrowserContext, Page]:
    p = await async_playwright().start()
    exe = _find_chromium_executable()
    launch_kwargs = {"headless": True}
    if exe:
        launch_kwargs["executable_path"] = exe
    browser = await p.chromium.launch(**launch_kwargs)
    context = await browser.new_context(user_agent=UA, locale="zh-CN")
    page = await context.new_page()
    return p, browser, context, page


# ============= SZSE 流程 =============


async def download_szse(
    stock_code: str,
    year: int,
    dest_dir: Path,
    company_name: str,
    *,
    on_progress: ProgressCb = None,
) -> DownloadOutcome:
    """深交所年报下载：填代码 → typeahead → 选公告类别=年度报告 → 查询 → 取 attachpath → curl。"""
    if on_progress:
        on_progress(f"[SZSE] 打开深交所公告页…")
    p, browser, context, page = await _open_browser()
    try:
        await page.goto("https://www.szse.cn/disclosure/listed/fixed/index.html", wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)

        # 1) 填代码（用 native setter 触发 typeahead）
        if on_progress:
            on_progress(f"[SZSE] 输入股票代码 {stock_code}…")
        typed = await page.evaluate(
            """(code) => {
                const inp = document.getElementById('input_code');
                if (!inp) return { ok: false, err: 'no #input_code' };
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, code);
                inp.dispatchEvent(new Event('input', { bubbles: true }));
                return { ok: true };
            }""",
            stock_code,
        )
        if not typed.get("ok"):
            raise RuntimeError(f"填代码失败: {typed}")
        await page.wait_for_timeout(1500)

        # 2) 点 typeahead 建议
        clicked_sug = await page.evaluate(
            """() => {
                const sug = document.querySelector('#c-typeahead-menu-1 li.active a')
                    || document.querySelector('#c-typeahead-menu-1 li a');
                if (sug) { sug.click(); return true; }
                return false;
            }"""
        )
        if not clicked_sug:
            raise RuntimeError("未找到 typeahead 建议项")

        # 3) 点「公告类别」下拉按钮
        cat_opened = await page.evaluate(
            """() => {
                const btn = Array.from(document.querySelectorAll('a.c-selectex-btn'))
                    .find(a => a.textContent.includes('公告类别'));
                if (!btn) return false;
                btn.click();
                return true;
            }"""
        )
        if not cat_opened:
            raise RuntimeError("未找到「公告类别」下拉按钮")
        await page.wait_for_timeout(500)

        # 4) 点选「年度报告」
        picked = await page.evaluate(
            """() => {
                const items = Array.from(document.querySelectorAll('ul.dropdrow-list li a'));
                const target = items.find(a => a.textContent.trim() === '年度报告');
                if (target) { target.click(); return true; }
                return false;
            }"""
        )
        if not picked:
            raise RuntimeError("未找到「年度报告」菜单项")

        # 5) 点「查询」
        if on_progress:
            on_progress(f"[SZSE] 提交查询…")
        queried = await page.evaluate(
            """() => {
                const btn = Array.from(document.querySelectorAll('button, a.btn'))
                    .find(b => b.textContent.trim() === '查询');
                if (btn) { btn.click(); return true; }
                return false;
            }"""
        )
        if not queried:
            raise RuntimeError("未找到「查询」按钮")
        await page.wait_for_timeout(2500)

        # 6) 提取表格行 attachpath
        attachpath = await page.evaluate(
            """(yearStr) => {
                const rows = Array.from(document.querySelectorAll('tr'));
                const want = `${yearStr}年年度报告`;
                // 多份相关公告时优先原始版（首个），避开摘要/英文/修订
                for (const row of rows) {
                    const link = row.querySelector('a.annon-title-link');
                    if (!link) continue;
                    const title = link.dataset.title || link.textContent || '';
                    if (!title.includes(want)) continue;
                    if (/摘要|英文|修订|更正/.test(title)) continue;
                    return link.getAttribute('attachpath');
                }
                return null;
            }""",
            str(year),
        )
        if not attachpath:
            raise RuntimeError(f"未找到 {year} 年年度报告行（可能尚未发布）")
        if on_progress:
            on_progress(f"[SZSE] 找到 PDF：{attachpath}")

        # 7) curl 下载（无 WAF）
        url = SZSE_PDF_HOST + attachpath
        dest = dest_dir / f"{company_name}{year}年年度报告.pdf"
        _curl_download(url, dest)
    finally:
        await context.close()
        await browser.close()
        await p.stop()

    sha = _sha256_of(dest)
    return DownloadOutcome(year=year, pdf_path=dest, sha256=sha, file_size=dest.stat().st_size)


# ============= SSE 流程 =============


async def _sse_navigate_with_retry(page: Page, url: str, *, retries: int = 3) -> None:
    """navigate to SSE page, retry on WAF challenge page (#inputCode 缺失即视为被拦)。"""
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            last_err = e
            _log.warning("SSE goto attempt %d failed: %s", attempt + 1, e)
            await page.wait_for_timeout(2000)
            continue
        # 等待 #inputCode 出现（WAF 挑战页可能无该元素）
        for _ in range(20):  # 最多 10s
            if await page.locator("#inputCode").count() > 0:
                return
            await page.wait_for_timeout(500)
        # inputCode 仍未出现：可能是 WAF 挑战页，刷新重试
        _log.warning("SSE page missing #inputCode (attempt %d), retrying", attempt + 1)
        await page.wait_for_timeout(2000)
    raise RuntimeError(
        f"SSE 页面连续 {retries} 次未加载 #inputCode（疑似 WAF 拦截）: {last_err}"
    )


async def _sse_search_page_url(
    page: Page, stock_code: str, year: int
) -> Optional[str]:
    """SSE 搜年报，返回首个匹配的标题链接 URL（点进去是 PDF）。"""
    await _sse_navigate_with_retry(page, "https://www.sse.com.cn/disclosure/listedinfo/regular/")

    # 1) 填代码（#inputCode 是真正的输入框）
    code_input = page.locator('#inputCode')
    await code_input.fill(stock_code)
    await page.wait_for_timeout(500)

    # 2) 报告类型 = 年报：用 Playwright 原生 select_option 触发 SSE 表单 auto-submit。
    # SSE 监听 <select> 的 change 事件，选完 YEARLY 就自动 XHR 拉数据：
    #   - 不需要点搜索按钮（这个表单根本没有"查询"按钮，那个 🔍 是股票代码旁的图标）
    #   - 不需要设日期范围（留空 = 不限日期，返回该股票全部历史年报）
    #   - JS evaluate 设 sel.value + selectpicker('refresh') 不行，picker 内部缓存认为
    #     "未选中"，查询接口退化到返回所有 SSE 公司年报
    selects = page.locator('select')
    select_count = await selects.count()
    selected = False
    for i in range(select_count):
        opts = await selects.nth(i).evaluate(
            "el => Array.from(el.options).map(o => o.value)"
        )
        if 'YEARLY' in opts:
            await selects.nth(i).select_option(value='YEARLY')
            selected = True
            break
    if not selected:
        raise RuntimeError("未找到「报告类型」下拉（缺 YEARLY 选项）")

    # 3) 等表格稳定（auto-submit 后约 1-2s 出结果）
    await page.wait_for_timeout(2500)
    
    # 4) 遍历结果行找年报链接（必须匹配 stock_code 前缀，避免错拿别家公司）
    href = await page.evaluate(
        """(args) => {
            const { yearStr, stockCode } = args;
            const want = `${yearStr}年年度报告`;
            // 工具：href 是不是属于指定 stock_code
            // 上交所文件名格式：600519_20250403_QVTI.pdf（股票代码在文件名最前面）
            const isMine = (h) => {
                if (!h) return false;
                // 取路径最后一段（即文件名），再前 6 位
                const fname = h.split('/').pop() || '';
                const m = fname.match(/^(\d{6})[_.]/);
                return m && m[1] === stockCode;
            };
            const cleanTitle = (t) => t.replace(/摘要|英文|修订|更正|公告/g, '').trim();
            // 优先：href 含 stockCode 且 text 含 want
            const anchors = Array.from(document.querySelectorAll('a'));
            for (const a of anchors) {
                const t = cleanTitle((a.textContent || ''));
                if (!t.includes(want)) continue;
                const h = a.getAttribute('href') || '';
                if (isMine(h)) return h;
            }
            // 次优：href 含 stockCode（不要求 text 完全匹配）
            for (const a of anchors) {
                const h = a.getAttribute('href') || '';
                if (!isMine(h)) continue;
                return h;
            }
            // 退路：data-url 行
            const rows = Array.from(document.querySelectorAll('[data-url]'));
            for (const r of rows) {
                const t = cleanTitle((r.textContent || ''));
                if (!t.includes(want)) continue;
                const h = r.getAttribute('data-url') || '';
                if (isMine(h)) return h;
            }
            return null;
        }""",
        {"yearStr": str(year), "stockCode": stock_code},
    )
    return href


# ============= 会话复用 =============


class _SseSession:
    r"""SSE 浏览器会话：worker 内多 year 复用，WAF cookie 一次拿多次用。"""
    def __init__(self):
        self.p = None
        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self):
        self.p, self.browser, self.context, self.page = await _open_browser()
        return self

    async def __aexit__(self, *exc):
        try:
            await self.context.close()
        except Exception:
            pass
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.p.stop()
        except Exception:
            pass

    async def fetch_pdf(self, stock_code: str, year: int) -> str:
        """搜年报 → 拿 WAF cookie → 返回完整 PDF URL。"""
        href = await _sse_search_page_url(self.page, stock_code, year)
        if not href:
            raise RuntimeError(f"未找到 {year} 年年度报告链接（可能尚未发布）")
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return SSE_PDF_HOST + href
        return "https://www.sse.com.cn/" + href.lstrip("/")

    async def warm_waf(self, pdf_url: str) -> str:
        """navigate PDF URL 拿 acw_sc__v2 cookie，返回 cookie 串。"""
        try:
            await self.page.goto(pdf_url, wait_until="commit", timeout=60000)
        except Exception as e:
            _log.warning("SSE PDF navigate warn: %s", e)
        cookie_str = ""
        for _ in range(20):
            await self.page.wait_for_timeout(500)
            cookies = await self.context.cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            if "acw_sc__v2=" in cookie_str:
                return cookie_str
        raise RuntimeError("SSE WAF cookie (acw_sc__v2) 未在 10s 内出现")


# ============= SSE 多 year 批量下载（复用浏览器）=============


async def download_sse_years(
    stock_code: str,
    years: list[int],
    dest_dir: Path,
    company_name: str,
    *,
    on_progress: ProgressCb = None,
) -> list[DownloadOutcome]:
    """一次浏览器会话，串行下多年。WAF cookie 第一次拿后可继续用（同会话内 ~30min 有效）。"""
    results: list[DownloadOutcome] = []
    async with _SseSession() as s:
        for year in years:
            if on_progress:
                on_progress(f"[SSE] 搜索 {company_name} {year} 年报…")
            pdf_url = await s.fetch_pdf(stock_code, year)
            if on_progress:
                on_progress(f"[SSE] {year} PDF：{pdf_url}")
            cookie_str = await s.warm_waf(pdf_url)
            if on_progress:
                on_progress(f"[SSE] WAF cookie 已就绪（{len(cookie_str)} 字节）")

            dest = dest_dir / f"{company_name}{year}年年度报告.pdf"
            _curl_download(pdf_url, dest, cookie=cookie_str, referer="https://www.sse.com.cn/")
            sha = _sha256_of(dest)
            results.append(
                DownloadOutcome(year=year, pdf_path=dest, sha256=sha, file_size=dest.stat().st_size)
            )
    return results


# ============= 入口 =============


async def download_one_year(
    stock_code: str,
    year: int,
    dest_dir: Path,
    company_name: str,
    *,
    on_progress: ProgressCb = None,
) -> DownloadOutcome:
    """按 stock_code 前缀自动分发到 SZSE / SSE。SSE 也走会话复用。"""
    ex = detect_exchange(stock_code)
    if on_progress:
        on_progress(f"识别交易所：{ex.upper()}（{stock_code}）")
    if ex == "szse":
        return await download_szse(stock_code, year, dest_dir, company_name, on_progress=on_progress)
    # SSE 单 year：复用 session
    outcomes = await download_sse_years(
        stock_code, [year], dest_dir, company_name, on_progress=on_progress
    )
    return outcomes[0]


# 同步包装（给 BackgroundTask 调用，worker 默认是同步）
def download_one_year_sync(
    stock_code: str,
    year: int,
    dest_dir: Path,
    company_name: str,
    *,
    on_progress: ProgressCb = None,
) -> DownloadOutcome:
    """Blocking 版本：新建事件循环跑 download_one_year。"""
    return asyncio.run(
        download_one_year(stock_code, year, dest_dir, company_name, on_progress=on_progress)
    )


def download_sse_years_sync(
    stock_code: str,
    years: list[int],
    dest_dir: Path,
    company_name: str,
    *,
    on_progress: ProgressCb = None,
) -> list[DownloadOutcome]:
    """Blocking 版本：复用同一浏览器下载 SSE 多年。"""
    return asyncio.run(
        download_sse_years(stock_code, years, dest_dir, company_name, on_progress=on_progress)
    )
