"""第三节（H2）拆分 service：包装 scripts/split_section3.py::split_section3。

产物落点（按 docs/artifacts.md 规范）：
    ``{公司}/md/clean/{公司}{年份}年年报/管理层讨论/{年份}/``

通过 importlib 动态加载（脚本路径已通过 settings.SCRIPT_PATH 注入 sys.path）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import List

from app.config import get_settings


def _load_split_module():
    """从 SCRIPT_PATH/split_section3.py 加载模块。

    关键：split_section3.py 内部期望 ``<root>/deep-research-report/shared/tools`` 在
    sys.path 中以便 ``from annual_report_reader.utils import ...``。但脚本里硬编码的
    TOOLS_DIR 路径是 ``scripts/../deep-research-report/...``，对真实项目并不存在。
    我们在加载模块前显式把 ``DEEP_RESEARCH_PATH/shared/tools`` 加进 sys.path。
    """
    settings = get_settings()
    script_path = Path(settings.SCRIPT_PATH) / "split_section3.py"
    if not script_path.exists():
        raise FileNotFoundError(f"split_section3.py 不存在: {script_path}")

    # 把 annual_report_reader 父目录加进 sys.path
    tools_dir = str(Path(settings.DEEP_RESEARCH_PATH) / "shared" / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    # 避免重复加载
    mod_name = "_rd_split_section3"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, str(script_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def split_section3(company: str, year: int | str) -> List[Path]:
    """调用 split_section3.py 拆分第三节。

    关键：split_section3 顶部 ``from annual_report_reader.utils import REPORT_BASE_PATH``
    会把名字绑定到 split_section3 自己的模块命名空间。我们需要同时 patch：
      1. ``annual_report_reader.utils.REPORT_BASE_PATH``
      2. ``split_section3.REPORT_BASE_PATH``（因为后者才是真正被引用的）
    才能让脚本写入我们指定的目录。

    Returns: 生成的 mid_file 路径列表。
    Raises: FileNotFoundError if 第三节源文件不存在。
    """
    mod = _load_split_module()
    year_str = str(year)

    settings = get_settings()

    target = Path(settings.REPORT_DATA_PATH)
    # patch annual_report_reader.utils
    import annual_report_reader.utils as _au_utils
    original_au = _au_utils.REPORT_BASE_PATH
    _au_utils.REPORT_BASE_PATH = target
    # patch split_section3 模块自己导入的引用
    original_mod = getattr(mod, "REPORT_BASE_PATH", None)
    if original_mod is not None:
        mod.REPORT_BASE_PATH = target
    try:
        out_dir = target / company / "md" / "clean" / f"{company}{year}年年报" / "管理层讨论"
        out_dir.mkdir(parents=True, exist_ok=True)

        # 记录调用前的文件快照（仅 md）
        before = set(p.name for p in out_dir.glob("*.md")) if out_dir.exists() else set()

        mod.split_section3(company, year_str)

        after = set(p.name for p in out_dir.glob("*.md"))
        new_names = after - before
    finally:
        _au_utils.REPORT_BASE_PATH = original_au
        if original_mod is not None:
            mod.REPORT_BASE_PATH = original_mod

    return sorted([out_dir / n for n in new_names])
