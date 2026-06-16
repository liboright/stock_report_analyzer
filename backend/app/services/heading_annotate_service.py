"""上下文感知标题标注 v2（heading_annotate_service）。

算法 4 步：
  1. anchor 定位 —— 找首个 # 行 + 行体含「报告期内公司从事」
  2. 决定走哪条链 —— anchor 管辖范围内是否有 `（一）/（二）/（三）`
  3. 全文 two-pass 改写 # 数 —— 按"父级 + 当前行体样式"动态判定层级
  4. 同级序号校验 —— 父级换则子级重置；违规行去 # 变正文

层级与样式（按 §2.1）：

  链 A（anchor 区出现 （一）/（二）/（三））：
    L1 第X节 → L2 一、 → L3 （一） → L4 1、/1./1 → L5 （1）/（1). → L6 1）/1).

  链 B（anchor 区无 （一）/（二）/（三））：
    L1 第X节 → L2 一、 → L3 1、/1./1 → L4 （1）/（1). → L5 1）/1).

非编号 # 行一律去 #；`A./B.` 也不是子标题。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------- 正则常量 ----------


RE_L1 = re.compile(r"^第([一二三四五六七八九十百千]+)节")
RE_L2_CN = re.compile(r"^([一二三四五六七八九十百千]+)、")
RE_BR_L3 = re.compile(r"^[（(]([一二三四五六七八九十百千]+)[）)]")
RE_NUM_FAMILY = re.compile(r"^(\d+)(?:[、.\s]?)(.*)$")
RE_BR_NUM_FAMILY = re.compile(r"^[（(](\d+)[）)](?:[、.\s]?)(.*)$")
RE_NUM_PAREN_FAMILY = re.compile(r"^(\d+)[）)](?:[、.\s]?)(.*)$")

RE_INLINE_PUNCT = re.compile(r"[，。；：？]$")
RE_HASH_PREFIX = re.compile(r"^(#+)\s*(.*)$")


# ---------- 模式编号 ----------


PAT_L1 = "L1"
PAT_L2_CN = "L2_CN"
PAT_BR_L3 = "BR_L3"
PAT_NUM_FAMILY = "NUM_FAMILY"
PAT_BR_NUM_FAMILY = "BR_NUM_FAMILY"
PAT_NUM_PAREN_FAMILY = "NUM_PAREN_FAMILY"


# ---------- 链定义 ----------


_LEVEL_TABLE_A = {
    1: PAT_L1,
    2: PAT_L2_CN,
    3: PAT_BR_L3,
    4: PAT_NUM_FAMILY,
    5: PAT_BR_NUM_FAMILY,
    6: PAT_NUM_PAREN_FAMILY,
}
_LEVEL_TABLE_B = {
    1: PAT_L1,
    2: PAT_L2_CN,
    3: PAT_NUM_FAMILY,
    4: PAT_BR_NUM_FAMILY,
    5: PAT_NUM_PAREN_FAMILY,
}


# ---------- 内部数据结构 ----------


@dataclass(frozen=True)
class _ProbeResult:
    chain: str
    level_table: dict


@dataclass
class _LineJudgment:
    new_hash_count: int
    is_violation: bool
    observed_level: Optional[int]


@dataclass
class _ParentContext:
    raw_hash: dict = field(default_factory=dict)
    last: dict = field(default_factory=dict)


# ---------- 纯函数 ----------


def _anchor_scope(lines):
    anchor_idx = -1
    for i, line in enumerate(lines):
        m = RE_HASH_PREFIX.match(line)
        if not m:
            continue
        body = m.group(2).strip()
        if "报告期内公司从事" in body:
            anchor_idx = i
            break
    if anchor_idx == -1:
        raise ValueError("未找到 anchor 行（# 行 + 含'报告期内公司从事'）")

    scope_end = len(lines)
    for j in range(anchor_idx + 1, len(lines)):
        if _is_l1_heading_line(lines[j]):
            scope_end = j
            break
    return anchor_idx, scope_end


def _is_l1_heading_line(line):
    m = RE_HASH_PREFIX.match(line)
    if not m:
        return False
    body = m.group(2).strip()
    if not body:
        return False
    return RE_L1.match(body) is not None


def _looks_numbered(body_stripped):
    return bool(
        RE_L1.match(body_stripped)
        or RE_L2_CN.match(body_stripped)
        or RE_BR_L3.match(body_stripped)
        or RE_NUM_FAMILY.match(body_stripped)
        or RE_BR_NUM_FAMILY.match(body_stripped)
        or RE_NUM_PAREN_FAMILY.match(body_stripped)
    )


def _probe_chain(scope_lines):
    has_br_l3 = False
    has_num_or_paren = False
    for line in scope_lines:
        m = RE_HASH_PREFIX.match(line)
        if not m:
            continue
        body = m.group(2).strip()
        if not body:
            continue
        m_br = RE_BR_L3.match(body)
        if m_br:
            tail = body[m_br.end():].strip()
            if not (tail and RE_INLINE_PUNCT.search(tail)):
                has_br_l3 = True
                continue
        if RE_NUM_FAMILY.match(body) or RE_BR_NUM_FAMILY.match(body) or RE_NUM_PAREN_FAMILY.match(body):
            has_num_or_paren = True

    if has_br_l3:
        return _ProbeResult(chain="A", level_table=_LEVEL_TABLE_A)
    if has_num_or_paren:
        return _ProbeResult(chain="B", level_table=_LEVEL_TABLE_B)
    raise ValueError("anchor 子章节内未找到有效子标题（既无 '（一）' 也无 '1、' / '（1）' / '1）'）")


def _to_int(s):
    if s.isdigit():
        return int(s)
    digit_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total, section = 0, 0
    for ch in s:
        if ch in digit_map:
            section += digit_map[ch]
        elif ch == "十":
            section = (section or 1) * 10
            total += section
            section = 0
        elif ch == "百":
            section = (section or 1) * 100
            total += section
            section = 0
        elif ch == "千":
            section = (section or 1) * 1000
            total += section
            section = 0
    return total + section


def _try_match(body):
    s = body.strip()
    if not s:
        return None
    for pat_name, regex in [
        (PAT_L1, RE_L1),
        (PAT_L2_CN, RE_L2_CN),
        (PAT_BR_L3, RE_BR_L3),
        (PAT_NUM_PAREN_FAMILY, RE_NUM_PAREN_FAMILY),
        (PAT_BR_NUM_FAMILY, RE_BR_NUM_FAMILY),
        (PAT_NUM_FAMILY, RE_NUM_FAMILY),
    ]:
        m = regex.match(s)
        if not m:
            continue
        tail = s[m.end():].strip()
        if tail and RE_INLINE_PUNCT.search(tail):
            return None
        return pat_name, m.group(1)
    return None


def _validate_sibling(obs_level, num_str, raw_line, ctx):
    parent_path = tuple(
        ctx.raw_hash[h] for h in sorted(ctx.raw_hash) if h < obs_level
    )
    prev_path, prev_num = ctx.last.get(obs_level, (None, 0))
    is_first = (prev_path != parent_path)
    cur_int = _to_int(num_str)

    if is_first and cur_int != 1:
        return True, True
    if not is_first and cur_int != prev_num + 1:
        return True, False

    ctx.raw_hash[obs_level] = hash(raw_line)
    for deeper in [k for k in ctx.raw_hash if k > obs_level]:
        del ctx.raw_hash[deeper]
        ctx.last.pop(deeper, None)
    ctx.last[obs_level] = (parent_path, cur_int)
    return False, is_first


def _judge_line(line, idx, anchor_idx, scope_end, probe, ctx):
    m = RE_HASH_PREFIX.match(line)
    if not m:
        return _LineJudgment(0, False, None)

    body = m.group(2)
    is_strict = idx == anchor_idx or anchor_idx < idx < scope_end

    if not body.strip():
        if idx == anchor_idx:
            raise ValueError(f"anchor/probe 范围内第 {idx + 1} 行为空 # 行，无法作为可信标题")
        return _LineJudgment(0, False, None)

    match_result = _try_match(body)
    if match_result is None:
        body_strip = body.strip()
        if idx == anchor_idx or (is_strict and _looks_numbered(body_strip)):
            raise ValueError(f"anchor/probe 范围内第 {idx + 1} 行无法识别为合规标题: {line}")
        return _LineJudgment(0, False, None)

    pat_name, num_str = match_result

    # 模式在链表里**最浅**出现的层级
    obs_level = None
    for lvl in sorted(probe.level_table):
        if probe.level_table[lvl] == pat_name:
            obs_level = lvl
            break
    if obs_level is None:
        if is_strict:
            raise ValueError(f"anchor/probe 范围内第 {idx + 1} 行样式 {pat_name} 不在所选链 {probe.chain} 中: {line}")
        return _LineJudgment(0, False, None)

    violation, _is_first = _validate_sibling(obs_level, num_str, line, ctx)
    if violation:
        if is_strict:
            raise ValueError(f"anchor/probe 范围内第 {idx + 1} 行序号违规: {line}")
        return _LineJudgment(0, True, obs_level)
    return _LineJudgment(obs_level, False, obs_level)


def _apply_judgment(line, j):
    m = RE_HASH_PREFIX.match(line)
    if j.new_hash_count == 0:
        return m.group(2).lstrip() if m else line
    if not m:
        return line
    body = m.group(2)
    return f"{'#' * j.new_hash_count} {body}"


# ---------- public API ----------


class ContextAwareHeadingAnnotator:
    """上下文感知标题标注器（v2）。"""

    def __init__(self) -> None:
        self._count: int = 0

    @property
    def annotated_count(self) -> int:
        return self._count

    def annotate_text(self, md_text: str) -> tuple:
        self._count = 0
        ctx = _ParentContext()
        lines = md_text.split("\n")

        anchor_idx, scope_end = _anchor_scope(lines)
        probe = _probe_chain(lines[anchor_idx: scope_end])

        out_lines = []
        for i, line in enumerate(lines):
            judgment = _judge_line(line, i, anchor_idx, scope_end, probe, ctx)
            out_lines.append(_apply_judgment(line, judgment))
            if judgment.new_hash_count > 0:
                self._count += 1
        return "\n".join(out_lines), self._count

    def annotate_business_md(self, md_path: Path) -> int:
        if not md_path.is_file():
            raise FileNotFoundError(f"业务 MD 不存在: {md_path}")
        text = md_path.read_text(encoding="utf-8")
        new, _count = self.annotate_text(text)
        if new != text:
            md_path.write_text(new, encoding="utf-8")
        return self._count
