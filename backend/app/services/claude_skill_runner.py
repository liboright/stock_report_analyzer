"""Claude Code skill runner：通过 subprocess 调 ``claude`` CLI 跑指定 skill。

设计：
- 白名单在 ``SUPPORTED_SKILLS``：stage1_business_understanding + stage2_table_merge
- 用 ``claude -p "/<skill> <args>" --output-format stream-json --verbose --bare``
  non-interactive 模式，按行解析 NDJSON（agent 思考 / 工具调用 / 工具结果 / 最终输出）
- 异步流式读 stdout（Popen），每条事件通过 ``progress_bus.publish`` 实时推 SSE
- ANTHROPIC_API_KEY 从 settings 注入 subprocess env
- 跑完后验证产物文件存在，路径返回

stage1 沿用 ``run_skill`` 接口签名（接收 years 列表，单产物）。
stage2 新增 ``run_skill_for_table_merge``（多参数 + long+wide 双产物）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence

from app.config import get_settings
from app.workers import progress_bus


# 当前支持的 skill 白名单
SUPPORTED_SKILLS = {
    "stage1_business_understanding": "Stage 1: 业务了解",
    "stage2_table_merge": "Stage 2: 跨年表格合并（弱组语义对齐）",
}


@dataclass
class SkillRunResult:
    skill: str
    company: str
    year: Optional[int]  # 向后兼容：取 years[0]；years 为空时为 None
    output_path: Path
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    # 多产物场景（如 stage2 long+wide）；单产物时为空列表
    output_paths: List[Path] = field(default_factory=list)
    # 实际传给 skill 的年份列表（多年份支持后新加）
    years: List[int] = field(default_factory=list)


class ClaudeSkillError(RuntimeError):
    """skill 执行失败（subprocess 错误、超时、产物缺失）"""


# ============================================================
# 内部工具
# ============================================================


def _resolve_claude_path() -> str:
    """解析 claude CLI 路径。Windows 下 .cmd 后缀要保留。"""
    claude_path = shutil.which("claude")
    if not claude_path:
        raise ClaudeSkillError("未找到 claude CLI（请先 `npm install -g @anthropic-ai/claude-code`）")
    return claude_path


def _build_subprocess_env() -> dict[str, str]:
    """从 settings 注入 ANTHROPIC_API_KEY；保留当前 PATH/系统变量。"""
    s = get_settings()
    env = os.environ.copy()
    if s.ANTHROPIC_API_KEY and s.ANTHROPIC_API_KEY != "sk-ant-placeholder-replace-me":
        env["ANTHROPIC_API_KEY"] = s.ANTHROPIC_API_KEY
    if s.ANTHROPIC_MODEL:
        env["ANTHROPIC_MODEL"] = s.ANTHROPIC_MODEL
    return env


def _attach_add_dirs(cmd: list[str], add_dirs: Sequence[Path]) -> list[str]:
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    return cmd


# ============================================================
# stage1 旧入口（保持兼容）
# ============================================================


def _build_command(
    skill: str,
    company: str,
    years: Sequence[int],
    add_dirs: Sequence[Path],
) -> list[str]:
    """构造 ``claude -p "/<skill> <company> <years_csv>" --bare`` 命令。

    Prompt 格式：
        /stage1_business_understanding 宁德时代 2023,2024,2025
    年份用逗号分隔（让 skill 一次拿到所有可用年份）。
    """
    if skill not in SUPPORTED_SKILLS:
        raise ClaudeSkillError(f"不支持的 skill: {skill}（仅支持 {list(SUPPORTED_SKILLS.keys())}）")
    claude_path = _resolve_claude_path()

    parts = [f"/{skill}", company]
    if years:
        parts.append(",".join(str(y) for y in years))
    prompt = " ".join(parts)

    # --output-format stream-json --verbose：按行输出 NDJSON 事件，方便前端分类展示
    # --bare：禁用 progress spinner、欢迎横幅，让 stdout 干净
    cmd: list[str] = [
        claude_path,
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--bare",
        # -p 非交互模式默认会卡权限弹窗；bypassPermissions 让 Write/Edit/Bash
        # 写文件不再被拦（cwd + --add-dir 仍在物理边界内，详见 PRD 决策记录）
        # 注意：--permission-mode bypassPermissions 仅绕 UI 层；MiniMax-M3 sandbox
        # 还会拦截 shell redirect（>`printf`>）、`Write` 工具不存在。必须额外加
        # --dangerously-skip-permissions（CLI 级 flags）才能真正让 heredoc 落盘。
        # 该 flag 推荐用于无外网的沙箱环境（我们的所有数据都在本地，符合）。
        "--dangerously-skip-permissions",
        "--permission-mode", "bypassPermissions",
    ]
    return _attach_add_dirs(cmd, add_dirs)


def _expected_output(skill: str, company: str, settings) -> List[Path]:
    """计算 skill 产物路径列表。

    stage1（双产物）:
        md/research_file/{公司}_业务概况.md
        md/research_file/{公司}_行业分析.md
    命名严格遵守 docs/artifacts.md §1 规范（含公司前缀）。
    """
    if skill == "stage1_business_understanding":
        out_dir = settings.REPORT_DATA_PATH / company / "md" / "research_file"
        return [
            out_dir / f"{company}_业务概况.md",
            out_dir / f"{company}_行业分析.md",
        ]
    raise ClaudeSkillError(f"未知 skill: {skill}")


# ============================================================
# 产物兜底解析（2026-06-16 方案 B：subprocess 产物缺失时从 stdout 自动落盘）
# ============================================================


# 匹配 ```markdown ... ``` 或 ```md ... ``` 代码块（DOTALL 让 . 跨行）
_MARKDOWN_BLOCK_RE = re.compile(r"```(?:markdown|md)?\s*\n(.*?)\n```", re.DOTALL)


def _extract_markdown_blocks(text: str) -> List[tuple[str, str]]:
    """从 text 提取所有 markdown 代码块。

    Returns:
        [(title, body), ...] — title 取代码块首行（去 # / 空格）。
    """
    if not text:
        return []
    blocks: List[tuple[str, str]] = []
    for m in _MARKDOWN_BLOCK_RE.finditer(text):
        body = m.group(1)
        first_line = body.strip().split("\n", 1)[0].strip()
        title = first_line.lstrip("#").strip()
        blocks.append((title, body))
    return blocks


def _dispatch_markdown_blocks(
    blocks: List[tuple[str, str]],
    expected_paths: Sequence[Path],
) -> List[Path]:
    """把代码块按标题语义分发到 expected_paths，返回落盘成功的路径列表。

    匹配规则：
    - 文件名含「业务概况」→ 找 title 含「业务概况」的代码块
    - 文件名含「行业分析」→ 找 title 含「行业分析」的代码块
    """
    title_keys = {
        "业务概况": ["业务概况", "主营业务"],
        "行业分析": ["行业分析", "行业情况"],
    }
    written: List[Path] = []
    used_blocks: set[int] = set()

    for path in expected_paths:
        # 找到文件对应的 key
        matched_key = None
        for key in title_keys:
            if key in path.name:
                matched_key = key
                break
        if matched_key is None:
            continue

        keywords = title_keys[matched_key]
        # 找未使用的代码块
        for idx, (title, body) in enumerate(blocks):
            if idx in used_blocks:
                continue
            if any(kw in title for kw in keywords):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body.rstrip() + "\n", encoding="utf-8")
                written.append(path)
                used_blocks.add(idx)
                break

    return written


# ============================================================
# 流式输出解析（stream-json → SSE 事件）
# ============================================================


# 工具参数摘要最大长度
_TOOL_ARG_SUMMARY_MAX = 240
# 工具结果摘要最大长度
_TOOL_RESULT_SUMMARY_MAX = 320
# thinking 文本最大长度（防止一坨长思考刷屏）
_THINKING_MAX = 800
# assistant text 摘要最大长度
_TEXT_SNIPPET_MAX = 240


def _truncate(s: str, n: int) -> str:
    if s is None:
        return ""
    s = str(s)
    if len(s) <= n:
        return s
    return s[:n] + f"…(+{len(s) - n} chars)"


def _summarize_tool_input(tool_name: str, inp: Any) -> str:
    """把工具参数压成一行可读摘要。"""
    if not isinstance(inp, dict):
        return _truncate(repr(inp), _TOOL_ARG_SUMMARY_MAX)
    if tool_name in ("Read", "ReadFile", "read_file"):
        f = inp.get("file_path") or inp.get("path") or inp.get("filePath")
        return f"file={f}" if f else ""
    if tool_name in ("Glob", "SearchFiles", "search_files"):
        pat = inp.get("pattern") or inp.get("glob")
        return f"pattern={pat}" if pat else ""
    if tool_name in ("Grep", "grep"):
        pat = inp.get("pattern")
        path = inp.get("path") or inp.get("cwd")
        return f"pattern={pat} path={path}" if pat else ""
    if tool_name in ("Write", "WriteFile", "write_file", "CreateFile"):
        f = inp.get("file_path") or inp.get("path") or inp.get("filePath")
        return f"file={f}" if f else ""
    if tool_name in ("Edit", "EditFile", "edit_file"):
        f = inp.get("file_path") or inp.get("path") or inp.get("filePath")
        return f"file={f}" if f else ""
    if tool_name in ("Bash", "bash", "run_command"):
        cmd = inp.get("command") or inp.get("cmd")
        return f"cmd={_truncate(cmd, 200)}" if cmd else ""
    if tool_name in ("Agent", "delegate_agent"):
        desc = inp.get("description") or inp.get("prompt")
        return f"description={_truncate(desc, 200)}" if desc else ""
    # fallback：取前 2 个键值
    keys = list(inp.keys())[:3]
    return ", ".join(f"{k}={_truncate(repr(inp.get(k)), 80)}" for k in keys)


def _summarize_tool_result(content: Any) -> str:
    """把 tool_result 内容压成一行摘要。"""
    if isinstance(content, str):
        return _truncate(content, _TOOL_RESULT_SUMMARY_MAX)
    if isinstance(content, list):
        # tool_result 列表里每项可能是 {"type":"text","text":"..."} 或带 image
        out_parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("type")
                if t == "text":
                    out_parts.append(_truncate(item.get("text", ""), _TOOL_RESULT_SUMMARY_MAX))
                elif t == "image":
                    out_parts.append(f"[image {item.get('source', {}).get('media_type', '?')}]")
                else:
                    out_parts.append(f"[{t}]")
            else:
                out_parts.append(_truncate(repr(item), 60))
        return " | ".join(out_parts)[:_TOOL_RESULT_SUMMARY_MAX]
    return _truncate(repr(content), _TOOL_RESULT_SUMMARY_MAX)


def _publish_event(
    run_id: int,
    *,
    phase: str,
    message: str,
    level: str = "info",
    stage: Optional[int] = None,
    payload: Optional[dict] = None,
) -> None:
    """推一条 task_event，失败不抛（流式不能被 SSE 错误打断主流程）。"""
    try:
        progress_bus.publish(
            run_id=run_id,
            message=message,
            level=level,
            stage=stage,
            payload={"phase": phase, **(payload or {})},
        )
    except Exception:  # noqa: BLE001
        pass


def _process_stream_event(run_id: int, line: str) -> None:
    """单行 NDJSON → 推 SSE 事件。

    claude CLI --output-format stream-json --verbose 输出（节选）：
      {"type":"system","subtype":"init",...}
      {"type":"user",...}
      {"type":"assistant","message":{"content":[
          {"type":"thinking","thinking":"..."},
          {"type":"text","text":"..."},
          {"type":"tool_use","name":"Read","input":{...}}
      ]}}
      {"type":"user","message":{"content":[
          {"type":"tool_result","tool_use_id":"...","content":[...]}
      ]}}
      {"type":"result","subtype":"success","result":"...","duration_ms":...}
    """
    line = line.strip()
    if not line:
        return
    if not (line.startswith("{") and line.endswith("}")):
        # 偶尔 claude CLI 会在 JSON 之间插入空行/纯文本 banner，过滤掉
        return
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return

    et = ev.get("type")
    if et == "system":
        sub = ev.get("subtype", "init")
        _publish_event(
            run_id,
            phase="system",
            message=f"[cli] system/{sub}",
            payload={"subtype": sub, "model": ev.get("model")},
        )
        return

    if et == "assistant":
        msg = ev.get("message") or {}
        contents = msg.get("content") or []
        if not isinstance(contents, list):
            return
        for block in contents:
            btype = block.get("type")
            if btype == "thinking":
                thinking = block.get("thinking", "")
                _publish_event(
                    run_id,
                    phase="thinking",
                    message=f"[think] {_truncate(thinking, _THINKING_MAX)}",
                    payload={"kind": "thinking", "full": thinking},
                )
            elif btype == "text":
                text = block.get("text", "")
                _publish_event(
                    run_id,
                    phase="text",
                    message=f"[text] {_truncate(text, _TEXT_SNIPPET_MAX)}",
                    payload={"kind": "text", "full": text},
                )
            elif btype == "tool_use":
                tool_name = block.get("name", "?")
                tool_input = block.get("input", {})
                tool_id = block.get("id", "")
                summary = _summarize_tool_input(tool_name, tool_input)
                _publish_event(
                    run_id,
                    phase="tool_use",
                    message=f"[tool] {tool_name}({summary})",
                    payload={
                        "kind": "tool_use",
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "input": tool_input,
                    },
                )
            # 其他 block type (e.g. "redacted_thinking") 暂时忽略
        return

    if et == "user":
        # user 类型通常承载 tool_result（tool 调用方把工具结果发回给 agent）
        msg = ev.get("message") or {}
        contents = msg.get("content") or []
        if not isinstance(contents, list):
            return
        for block in contents:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id", "")
                is_err = bool(block.get("is_error"))
                content = block.get("content", "")
                summary = _summarize_tool_result(content)
                _publish_event(
                    run_id,
                    phase="tool_result",
                    message=f"[result{' err' if is_err else ''}] {summary}",
                    level=("error" if is_err else "info"),
                    payload={
                        "kind": "tool_result",
                        "tool_use_id": tool_id,
                        "is_error": is_err,
                        "content": content,
                    },
                )
        return

    if et == "result":
        sub = ev.get("subtype", "")
        # success 退出码 0 不等于任务完成：agent 可能因权限被拦而把内容贴 stdout 兜底
        # （参见贵州茅台 stage1 失败案例：result=success 但产物未落盘）
        is_err = sub in ("error", "error_max_turns", "error_during_execution") or (
            sub == "success" and "blocked" in (ev.get("result", "") or "")
        )
        result_text = ev.get("result", "")
        _publish_event(
            run_id,
            phase="result",
            message=f"[cli] result/{sub}{(': ' + _truncate(result_text, 200)) if result_text else ''}",
            level=("error" if is_err else "info"),
            payload={
                "kind": "result",
                "subtype": sub,
                "is_error": is_err,
                "result": result_text,
                "duration_ms": ev.get("duration_ms"),
                "total_cost_usd": ev.get("total_cost_usd"),
            },
        )
        return

    # 未知 type 留个 breadcrumb（只在 payload 完整塞进，方便排查）
    _publish_event(
        run_id,
        phase="raw",
        message=f"[cli] unknown event type={et}",
        payload={"raw_type": et, "raw_keys": list(ev.keys())[:10]},
    )


def run_skill(
    skill: str,
    company: str,
    years: Optional[Sequence[int]] = None,
    year: Optional[int] = None,
    timeout_seconds: int = 1800,
    run_id: Optional[int] = None,
) -> SkillRunResult:
    """同步跑 claude skill（单产物 stage1），流式推 SSE 事件。

    Args:
        skill: 白名单内的 skill 名
        company: 公司名
        years: 多年份列表（优先使用；推荐）
        year: 单年份，向后兼容；与 years 同时给时取 years
        timeout_seconds: subprocess 超时（默认 30 分钟，多年生成给足时间）
        run_id: SSE 推事件用的 run_id（必传，否则不推 SSE）

    Returns:
        SkillRunResult，years 字段反映实际传入的年份。

    Raises:
        ClaudeSkillError: CLI 不存在、subprocess 非 0 退出、超时、产物缺失。
    """
    s = get_settings()
    add_dirs = [s.REPORT_DATA_PATH, s.DEEP_RESEARCH_PATH]

    # 合并 years / year（years 优先）
    if years is None and year is not None:
        years = [year]
    years_list: List[int] = list(years) if years else []
    legacy_year: Optional[int] = years_list[0] if years_list else year

    cmd = _build_command(skill, company, years_list, add_dirs)
    env = _build_subprocess_env()

    # 流式推 SSE
    effective_run_id = run_id if run_id is not None else 0
    if run_id is not None:
        _publish_event(
            run_id,
            phase="start",
            message=(
                f"[start] claude skill={skill} company={company} years={years_list or '(无)'} "
                f"timeout={timeout_seconds}s"
            ),
            stage=1,
            payload={"skill": skill, "company": company, "years": years_list},
        )

    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(s.REPORT_DATA_PATH),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 合并到 stdout，按行解析
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as e:
        raise ClaudeSkillError(
            f"找不到 claude CLI（{e}）。请确认已安装并加入 PATH"
        ) from e

    tail: list[str] = []
    # 方案 B（2026-06-16）：缓存最新 result 事件文本，用于产物缺失兜底解析。
    latest_result_text: str = ""
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            tail.append(line.rstrip())
            if len(tail) > 5000:
                tail.pop(0)
            # 缓存最新 result 事件。NDJSON 每行一条 JSON，但 json.dumps 默认带空格分隔，
            # 不能用 '"type":"result"' 严格匹配；统一 try json.loads 看 type 字段。
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    ev = json.loads(stripped)
                    if ev.get("type") == "result":
                        latest_result_text = ev.get("result", "") or latest_result_text
                except Exception:  # noqa: BLE001
                    pass
            # 解析 stream-json 事件
            _process_stream_event(effective_run_id, line)
    except Exception as e:  # noqa: BLE001
        if run_id is not None:
            _publish_event(
                run_id,
                phase="stderr",
                message=f"[read stdout 异常] {e}",
                level="warning",
            )

    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        if run_id is not None:
            _publish_event(
                run_id,
                phase="timeout",
                message=f"[timeout] claude skill 超过 {timeout_seconds}s 被强杀",
                level="error",
            )
        raise ClaudeSkillError(
            f"claude skill {skill} 超时（>{timeout_seconds}s）"
        )

    elapsed = time.time() - start
    output_paths = _expected_output(skill, company, s)
    output_path = output_paths[0]  # 主产物（向后兼容：SkillRunResult.output_path）

    if proc.returncode != 0:
        if run_id is not None:
            _publish_event(
                run_id,
                phase="exit_error",
                message=(
                    f"[exit] returncode={proc.returncode} tail="
                    f"{'\\n'.join(tail[-3:])[:300]}"
                ),
                level="error",
                payload={"returncode": proc.returncode, "tail": tail[-20:]},
            )
        raise ClaudeSkillError(
            f"claude skill {skill} 退出码 {proc.returncode}。tail: {'\\n'.join(tail[-5:])[:500]}"
        )

    missing = [p for p in output_paths if not p.exists()]
    if missing:
        # 方案 B（2026-06-16）：产物缺失兜底 — 从 stdout / result.result 提取 markdown 代码块
        # 自动落盘。覆盖长流程 SubAgent 自己写文件被 M3 sandbox 拦截的场景。
        blocks: List[tuple[str, str]] = []
        if latest_result_text:
            blocks = _extract_markdown_blocks(latest_result_text)
        if not blocks:
            # 兜底 2：从 tail 倒序找最后一条 result 事件（兼容 latest_result_text 未命中的边界场景）
            for line in reversed(tail):
                stripped = line.strip()
                if not (stripped.startswith("{") and stripped.endswith("}")):
                    continue
                try:
                    ev = json.loads(stripped)
                    if ev.get("type") == "result":
                        blocks = _extract_markdown_blocks(ev.get("result", ""))
                        if blocks:
                            break
                except Exception:  # noqa: BLE001
                    continue

        if blocks:
            written = _dispatch_markdown_blocks(blocks, output_paths)
            if written:
                if run_id is not None:
                    _publish_event(
                        run_id,
                        phase="output_recovered",
                        message=(
                            f"[output_recovered] 从 stdout 兜底落盘 {len(written)} 个产物: "
                            + ", ".join(p.name for p in written)
                        ),
                        level="warning",
                        payload={
                            "recovered_paths": [str(p) for p in written],
                            "blocks_found": len(blocks),
                        },
                    )
                # 重新计算 missing
                missing = [p for p in output_paths if not p.exists()]

    if missing:
        if run_id is not None:
            _publish_event(
                run_id,
                phase="output_missing",
                message=(
                    f"[output_missing] 期望产物缺失 ({len(missing)}/{len(output_paths)}): "
                    + ", ".join(p.name for p in missing)
                ),
                level="error",
            )
        raise ClaudeSkillError(
            f"claude skill {skill} 跑完但产物缺失: "
            + ", ".join(str(p) for p in missing)
        )

    if run_id is not None:
        _publish_event(
            run_id,
            phase="done",
            message=f"[done] 产物落盘 {output_path.name}（{elapsed:.1f}s）",
            stage=1,
            payload={"output": str(output_path), "elapsed": elapsed},
        )

    return SkillRunResult(
        skill=skill,
        company=company,
        year=legacy_year,
        output_path=output_path,
        returncode=proc.returncode,
        stdout="\n".join(tail[-100:]),
        stderr="",
        elapsed_seconds=elapsed,
        output_paths=output_paths,
        years=years_list,
    )


# ============================================================
# stage2 新入口：跨年表格合并（弱组兜底）
# ============================================================


# stage2 产物文件名前缀（与 table_merge_service.merge_strong_group 对齐）
_TABLE_MERGE_ILLEGAL = re.compile(r'[\\/:*?"<>|\s]+')


def _sanitize_for_filename(s: str) -> str:
    """源 md stem / sanitized_title → 安全 filename stem。"""
    s = _TABLE_MERGE_ILLEGAL.sub("_", s or "").strip("_")
    return s or "未命名"


def _expected_table_merge_outputs(
    company: str, group_key: str, settings
) -> tuple[Path, Path]:
    """计算 stage2 产物的 long + wide 路径。

    与 table_merge_service.merge_strong_group 的命名规则保持一致：
    {stem}_{title}_long.csv / _wide.csv
    """
    if "|" in group_key:
        stem, title = group_key.split("|", 1)
    else:
        stem, title = "", group_key
    safe_stem = _sanitize_for_filename(stem)
    safe_title = _sanitize_for_filename(title)
    out_dir = settings.REPORT_DATA_PATH / company / "md" / "research_file" / "table"
    long_path = out_dir / f"{safe_stem}_{safe_title}_long.csv"
    wide_path = out_dir / f"{safe_stem}_{safe_title}_wide.csv"
    return long_path, wide_path


def _build_command_table_merge(
    skill: str,
    company: str,
    group_key: str,
    years: Sequence[int],
    csv_paths: Sequence[str],
    add_dirs: Sequence[Path],
) -> list[str]:
    """构造 stage2 的 claude CLI 命令。

    Prompt 格式（4 段，whitespace 分隔；csv_paths 内部用 | 分隔避免空格混淆）：
        /stage2_table_merge <company> <group_key> <years_csv> <path1>|<path2>|...
    """
    if skill not in SUPPORTED_SKILLS:
        raise ClaudeSkillError(f"不支持的 skill: {skill}（仅支持 {list(SUPPORTED_SKILLS.keys())}）")
    claude_path = _resolve_claude_path()

    if not years:
        raise ClaudeSkillError("stage2_table_merge 至少需要 1 个年份")
    if not csv_paths:
        raise ClaudeSkillError("stage2_table_merge 至少需要 1 个 CSV 路径")

    years_str = ",".join(str(y) for y in years)
    csvs_str = "|".join(csv_paths)
    prompt = f"/{skill} {company} {group_key} {years_str} {csvs_str}"

    cmd: list[str] = [
        claude_path,
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--bare",
        # -p 非交互模式默认会卡权限弹窗；bypassPermissions 让 Write/Edit/Bash
        # 写文件不再被拦（cwd + --add-dir 仍在物理边界内，详见 PRD 决策记录）
        # 注意：--permission-mode bypassPermissions 仅绕 UI 层；MiniMax-M3 sandbox
        # 还会拦截 shell redirect。必须额外加 --dangerously-skip-permissions
        # 才能真正让 heredoc 落盘（推荐用于无外网沙箱环境）。
        "--dangerously-skip-permissions",
        "--permission-mode", "bypassPermissions",
    ]
    return _attach_add_dirs(cmd, add_dirs)


def run_skill_for_table_merge(
    skill: str,
    company: str,
    group_key: str,
    years: Sequence[int],
    csv_paths: Sequence[str],
    timeout_seconds: int = 600,
    run_id: Optional[int] = None,
) -> SkillRunResult:
    """同步跑 stage2_table_merge skill，对单一弱组做语义对齐 → long + wide CSV。

    Args:
        skill: 白名单内的 skill 名（'stage2_table_merge'）
        company: 公司名（如 '贵州茅台'）
        group_key: 分组键（格式 `{source_md_stem}|{sanitized_title}`）
        years: 该组涉及的年份列表
        csv_paths: N 个 CSV 相对 REPORT_DATA_PATH 的 POSIX 路径（与 years 一一对应）
        timeout_seconds: subprocess 超时（默认 10 分钟）
        run_id: SSE 推事件用的 run_id

    Returns:
        SkillRunResult，output_path = long_csv，output_paths = [long_csv, wide_csv]

    Raises:
        ClaudeSkillError: 参数不合法、CLI 不存在、subprocess 非 0、超时、产物缺失。
    """
    s = get_settings()
    add_dirs = [s.REPORT_DATA_PATH, s.DEEP_RESEARCH_PATH]
    cmd = _build_command_table_merge(skill, company, group_key, years, csv_paths, add_dirs)
    env = _build_subprocess_env()

    long_path, wide_path = _expected_table_merge_outputs(company, group_key, s)
    effective_run_id = run_id if run_id is not None else 0

    if run_id is not None:
        _publish_event(
            run_id,
            phase="start",
            message=(
                f"[start] stage2 group={group_key} years={list(years)} "
                f"timeout={timeout_seconds}s"
            ),
            stage=3,
            payload={"group_key": group_key, "years": list(years)},
        )

    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(s.REPORT_DATA_PATH),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as e:
        raise ClaudeSkillError(
            f"找不到 claude CLI（{e}）。请确认已安装并加入 PATH"
        ) from e

    tail: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            tail.append(line.rstrip())
            if len(tail) > 500:
                tail.pop(0)
            _process_stream_event(effective_run_id, line)
    except Exception as e:  # noqa: BLE001
        if run_id is not None:
            _publish_event(
                run_id,
                phase="stderr",
                message=f"[read stdout 异常] {e}",
                level="warning",
            )

    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        if run_id is not None:
            _publish_event(
                run_id,
                phase="timeout",
                message=f"[timeout] stage2 超过 {timeout_seconds}s 被强杀",
                level="error",
            )
        raise ClaudeSkillError(
            f"claude skill {skill} 超时（>{timeout_seconds}s）"
        )

    elapsed = time.time() - start

    if proc.returncode != 0:
        if run_id is not None:
            _publish_event(
                run_id,
                phase="exit_error",
                message=(
                    f"[exit] returncode={proc.returncode} tail="
                    f"{'\\n'.join(tail[-3:])[:300]}"
                ),
                level="error",
            )
        raise ClaudeSkillError(
            f"claude skill {skill} 退出码 {proc.returncode}。tail: {'\\n'.join(tail[-5:])[:500]}"
        )

    if not long_path.exists() or not wide_path.exists():
        if run_id is not None:
            _publish_event(
                run_id,
                phase="output_missing",
                message=(
                    f"[output_missing] long={long_path.exists()} wide={wide_path.exists()}"
                ),
                level="error",
            )
        raise ClaudeSkillError(
            f"claude skill {skill} 跑完但产物缺失: long={long_path.exists()}, wide={wide_path.exists()}"
        )

    if run_id is not None:
        _publish_event(
            run_id,
            phase="done",
            message=f"[done] stage2 落盘 {long_path.name}, {wide_path.name}（{elapsed:.1f}s）",
            stage=3,
            payload={
                "group_key": group_key,
                "long_csv": str(long_path),
                "wide_csv": str(wide_path),
                "elapsed": elapsed,
            },
        )

    return SkillRunResult(
        skill=skill,
        company=company,
        year=None,
        output_path=long_path,
        returncode=proc.returncode,
        stdout="\n".join(tail[-100:]),
        stderr="",
        elapsed_seconds=elapsed,
        output_paths=[long_path, wide_path],
        years=list(years),
    )
