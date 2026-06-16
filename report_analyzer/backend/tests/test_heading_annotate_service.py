from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_MODULE_DIR = _HERE.parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from app.services.heading_annotate_service import ContextAwareHeadingAnnotator  # noqa: E402


def _annotate(text: str) -> tuple[str, int]:
    return ContextAwareHeadingAnnotator().annotate_text(text)


# ============== helpers ==============


def _with_anchor(
    *,
    pre_l1: str = "# 第一节 重要提示\n# 第二节 公司简介\n# 第三节 管理层讨论与分析\n",
    anchor_pre: str = "一、所处行业情况\n二、主要业务\n",
    anchor_line: str = "三、报告期内公司从事的业务\n",
    after_anchor: str = "（一）主营业务\n",
    next_l1: str | None = "第四节 公司治理",
    body_tail: str = "",
) -> str:
    parts: list[str] = [pre_l1 if pre_l1.endswith("\n") else pre_l1 + "\n"]
    for line in anchor_pre.splitlines():
        if line.strip():
            parts.append(f"# {line}\n")
    if anchor_line.strip():
        parts.append(f"# {anchor_line.strip()}\n")
    for line in after_anchor.splitlines():
        if line.strip():
            parts.append(f"# {line}\n")
    if next_l1 is not None:
        parts.append(f"# {next_l1}\n")
    if body_tail:
        parts.append(body_tail if body_tail.endswith("\n") else body_tail + "\n")
    return "".join(parts)


# ============== TestAnchorAndProbe ==============


class TestAnchorAndProbe:
    def test_chain_a_probe_uses_bracket_l3(self) -> None:
        text = _with_anchor(after_anchor="（一）主营业务分析\n")
        new, _ = _annotate(text)
        assert "## 三、报告期内公司从事的业务" in new
        assert "### （一）主营业务分析" in new

    def test_chain_b_probe_uses_numeric_l3(self) -> None:
        text = _with_anchor(after_anchor="1、主营业务分析\n")
        new, _ = _annotate(text)
        assert "## 三、报告期内公司从事的业务" in new
        assert "### 1、主营业务分析" in new

    def test_anchor_scope_can_reach_eof(self) -> None:
        text = _with_anchor(next_l1=None, after_anchor="（一）主营业务分析\n")
        new, _ = _annotate(text)
        assert "### （一）主营业务分析" in new

    def test_missing_anchor_raises(self) -> None:
        with pytest.raises(ValueError):
            _annotate("# 第一节 重要提示\n# 一、子标题\n")

    def test_anchor_without_number_raises(self) -> None:
        text = _with_anchor(anchor_line="报告期内公司从事的业务\n")
        with pytest.raises(ValueError):
            _annotate(text)

    def test_anchor_without_siblings_raises(self) -> None:
        text = _with_anchor(anchor_pre="", anchor_line="三、报告期内公司从事的业务\n")
        with pytest.raises(ValueError):
            _annotate(text)

    def test_empty_probe_raises(self) -> None:
        text = _with_anchor(after_anchor="")
        with pytest.raises(ValueError):
            _annotate(text)

    def test_anchor_with_zhuyao_yewu_chain_a(self) -> None:
        """宁德时代 2023/2024/2025 实际格式：anchor 含「主要业务」也命中。"""
        text = _with_anchor(
            anchor_line="三、报告期内公司从事的主要业务\n",
            after_anchor="（一）主营业务\n",
        )
        new, _ = _annotate(text)
        assert "## 三、报告期内公司从事的主要业务" in new
        assert "### （一）主营业务" in new

    def test_anchor_with_zhuyao_yewu_chain_b(self) -> None:
        text = _with_anchor(
            anchor_line="三、报告期内公司从事的主要业务\n",
            after_anchor="1、主营业务\n",
        )
        new, _ = _annotate(text)
        assert "## 三、报告期内公司从事的主要业务" in new
        assert "### 1、主营业务" in new


# ============== TestChainA ==============


class TestChainA:
    """anchor 区出现 （一）/（二） → 链 A。"""

    def test_full_chain_a_to_l6(self) -> None:
        text = _with_anchor(
            after_anchor=(
                "（一）主营业务\n"
                "1、收入\n"
                "（1) 境内\n"
                "1）线上\n"
            ),
        )
        new, _ = _annotate(text)
        assert "### （一）主营业务" in new
        assert "#### 1、收入" in new
        assert "##### （1) 境内" in new
        assert "###### 1）线上" in new

    def test_chain_a_bracket_l3_sequence_continuous(self) -> None:
        text = _with_anchor(
            after_anchor="（一）首项\n（二）次项\n（三）第三\n",
        )
        new, _ = _annotate(text)
        assert "### （一）首项" in new
        assert "### （二）次项" in new
        assert "### （三）第三" in new


# ============== TestChainB ==============


class TestChainB:
    """anchor 区无 （一）/（二） → 链 B。"""

    def test_full_chain_b_to_l5(self) -> None:
        text = _with_anchor(
            after_anchor=(
                "1、主营业务\n"
                "（1）境内\n"
                "1）线上\n"
            ),
        )
        new, _ = _annotate(text)
        assert "### 1、主营业务" in new
        assert "#### （1）境内" in new
        assert "##### 1）线上" in new


# ============== TestSeparatorFamily ==============


class TestSeparatorFamily:
    def test_l3_equivalents_chain_b(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n2. 次项\n3 第三\n",
        )
        new, _ = _annotate(text)
        assert "### 1、首项" in new
        assert "### 2. 次项" in new
        assert "### 3 第三" in new

    def test_l4_equivalents_chain_b(self) -> None:
        text = _with_anchor(
            after_anchor=(
                "1、首项\n"
                "（1）首项\n"
                "（2). 次项\n"
                "（3) 第三\n"
            ),
        )
        new, _ = _annotate(text)
        assert "#### （1）首项" in new
        assert "#### （2). 次项" in new
        assert "#### （3) 第三" in new

    def test_l5_equivalents_chain_b(self) -> None:
        text = _with_anchor(
            after_anchor=(
                "1、首项\n"
                "（1）首项\n"
                "1）首\n"
                "2). 次\n"
                "3) 第三\n"
            ),
        )
        new, _ = _annotate(text)
        assert "##### 1）首" in new
        assert "##### 2). 次" in new
        assert "##### 3) 第三" in new


# ============== TestSiblingValidation ==============


class TestSiblingValidation:
    def test_l2_sequence_normal_chain_b(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="# 一、A\n# 二、B\n# 三、C\n",
        )
        new, _ = _annotate(text)
        assert "## 一、A" in new
        assert "## 二、B" in new
        assert "## 三、C" in new

    def test_skip_first_violation_removes_hash(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="# 二、跳过一\n# 三、继续跳\n",
        )
        new, _ = _annotate(text)
        assert "## 二、跳过一" not in new
        assert "二、跳过一" in new
        assert "## 三、继续跳" not in new
        assert "三、继续跳" in new

    def test_skip_middle_violation_does_not_update_ctx(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="# 一、合规\n# 三、跳号\n# 四、继续\n",
        )
        new, _ = _annotate(text)
        assert "## 一、合规" in new
        assert "## 三、跳号" not in new
        assert "## 四、继续" not in new

    def test_l3_resets_on_parent_change(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail=(
                "# 一、父A\n# 1、子A1\n# 2、子A2\n"
                "# 二、父B\n# 1、子B1\n"
            ),
        )
        new, _ = _annotate(text)
        assert "## 一、父A" in new
        assert "### 1、子A1" in new
        assert "## 二、父B" in new
        assert "### 1、子B1" in new

    def test_l1_skip_violation_removes_hash(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="# 第八节 跳号节\n",
        )
        new, _ = _annotate(text)
        assert "# 第四节 公司治理" in new
        assert "# 第八节 跳号节" not in new
        assert "第八节 跳号节" in new

    def test_l1_multi_char_chinese(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail=(
                "# 第五节 X\n# 第六节 X\n# 第七节 X\n"
                "# 第八节 X\n# 第九节 X\n# 第十节 X\n# 第十一节 备查\n"
            ),
        )
        new, _ = _annotate(text)
        assert "# 第十一节 备查" in new
        assert "## 第十一节" not in new


# ============== TestStrictRegion ==============


class TestStrictRegion:
    def test_probe_first_not_one_raises(self) -> None:
        text = _with_anchor(after_anchor="（二）跳过首项\n")
        with pytest.raises(ValueError):
            _annotate(text)

    def test_probe_skip_middle_raises(self) -> None:
        text = _with_anchor(after_anchor="（一）首项\n（三）跳号\n")
        with pytest.raises(ValueError):
            _annotate(text)

    def test_probe_inline_punct_raises(self) -> None:
        text = _with_anchor(after_anchor="（一）这是正文。\n")
        with pytest.raises(ValueError):
            _annotate(text)

    def test_anchor_line_with_proper_siblings_passes(self) -> None:
        text = _with_anchor()
        new, _ = _annotate(text)
        assert "## 三、报告期内公司从事的业务" in new


# ============== TestPreservation ==============


class TestPreservation:
    def test_non_hash_lines_untouched(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="一、无标题段落\n",
        )
        new, _ = _annotate(text)
        assert "一、无标题段落" in new

    def test_letter_dot_removed(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="# A.客户情况\n# B.供应商情况\n",
        )
        new, _ = _annotate(text)
        assert "# A.客户情况" not in new
        assert "A.客户情况" in new
        assert "##### A.客户情况" not in new

    def test_inline_punct_body_removed(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="# 一、这是正文。\n",
        )
        new, _ = _annotate(text)
        assert "## 一、这是正文。" not in new
        assert "一、这是正文。" in new

    def test_mid_title_punct_allowed(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="# 一、公司因商业秘密等特殊原因未披露\n",
        )
        new, _ = _annotate(text)
        assert "## 一、公司因商业秘密等特殊原因未披露" in new

    def test_empty_hash_removed(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="#\n正文\n",
        )
        new, _ = _annotate(text)
        assert not any(line.strip() == "#" for line in new.splitlines())

    def test_spaces_normalized(self) -> None:
        text = _with_anchor(
            after_anchor="1、首项\n",
            body_tail="#    一、业务\n",
        )
        new, _ = _annotate(text)
        assert "## 一、业务" in new
        assert "##    一、业务" not in new


# ============== TestRegression ==============


class TestRegression:
    def test_idempotent(self) -> None:
        text = _with_anchor(after_anchor="1、首项\n", body_tail="# 一、业务\n# 二、行业\n")
        new1, n1 = _annotate(text)
        new2, n2 = _annotate(new1)
        assert new2 == new1
        assert n2 == n1

    def test_annotate_business_md_overwrites_inplace(self, tmp_path: Path) -> None:
        md = tmp_path / "业务报告.md"
        md.write_text(_with_anchor(after_anchor="1、首项\n", body_tail="# 一、业务\n"), encoding="utf-8")
        n = ContextAwareHeadingAnnotator().annotate_business_md(md)
        assert n > 0
        assert "## 一、业务" in md.read_text(encoding="utf-8")

    def test_annotate_business_md_no_change_no_write(self, tmp_path: Path) -> None:
        md = tmp_path / "业务报告.md"
        text, _ = _annotate(_with_anchor(after_anchor="1、首项\n", body_tail="# 一、业务\n"))
        md.write_text(text, encoding="utf-8")
        mtime = md.stat().st_mtime_ns
        n = ContextAwareHeadingAnnotator().annotate_business_md(md)
        assert n > 0
        assert md.stat().st_mtime_ns == mtime

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ContextAwareHeadingAnnotator().annotate_business_md(tmp_path / "missing.md")

    def test_v1_compatible_interface(self) -> None:
        ann = ContextAwareHeadingAnnotator()
        assert hasattr(ann, "annotated_count")
        new, count = ann.annotate_text(_with_anchor(after_anchor="（一）主营业务\n"))
        assert isinstance(new, str)
        assert isinstance(count, int)
        assert ann.annotated_count == count


# ============== TestCrossModuleContract ==============


class TestCrossModuleContract:
    def test_worker_call_signature(self) -> None:
        attr = getattr(ContextAwareHeadingAnnotator(), "annotate_business_md")
        assert callable(attr)

    def test_mockable_interface(self, tmp_path: Path) -> None:
        class FakeAnnotator:
            def annotate_business_md(self, md_path: Path) -> int:
                return 3

        assert FakeAnnotator().annotate_business_md(tmp_path / "x.md") == 3

    def test_class_importable(self) -> None:
        assert ContextAwareHeadingAnnotator.__name__ == "ContextAwareHeadingAnnotator"
