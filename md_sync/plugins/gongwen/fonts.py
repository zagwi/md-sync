"""gongwen 插件字体检测与免费字体安装。

GB/T 9704-2012 需要四种字体：小标宋（标题）、黑体（一级标题）、
楷体（二级标题）、仿宋（正文）。本机若无对应字体，渲染会回退到
Noto，字形不标准。

免费替代方案：Fandol 字体集（GPL v3 + font exception，可自由分发，
CTAN 官方分发），一套四款：FandolSong / FandolHei / FandolKai /
FandolFang，正好覆盖四个公文角色。

本模块只负责检测与安装，不涉及 GUI。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# 每个公文角色可接受的字体族（按优先级；命中其一即视为已具备）
FONT_ROLES: dict[str, list[str]] = {
    "小标宋（标题）": [
        "FZXiaoBiaoSong-B05S", "FZXiaoBiaoSong-B05B",
        "方正小标宋简体", "方正小标宋_GBK", "FandolSong",
        "华文中宋", "STZhongsong", "SimSun", "宋体",
    ],
    "黑体（一级标题）": [
        "SimHei", "黑体", "STHeiti", "Heiti SC", "FandolHei",
    ],
    "楷体（二级标题）": [
        "KaiTi", "楷体", "STKaiti", "Kaiti SC", "FandolKai", "AR PL UKai CN",
    ],
    "仿宋（正文）": [
        "FangSong", "仿宋", "FangSong_GB2312", "STFangSong", "FandolFang",
    ],
}

# Fandol 免费字体集（CTAN 官方镜像，会重定向到就近镜像）
_FONT_ZIP_URL = "https://mirrors.ctan.org/fonts/fandol.zip"
_INSTALL_DIR = Path.home() / ".local/share/fonts/md-sync-fandol"


def _installed_families() -> set[str]:
    """Return the set of lowercase font family names known to fontconfig."""
    try:
        out = subprocess.run(
            ["fc-list", ":", "family"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return set()
    families: set[str] = set()
    for line in out.splitlines():
        for fam in line.split(","):
            fam = fam.strip()
            if fam:
                families.add(fam.lower())
    return families


def missing_fonts() -> list[str]:
    """Return the list of missing 公文 font roles ([] = all present)."""
    families = _installed_families()
    return [
        role for role, candidates in FONT_ROLES.items()
        if not any(c.lower() in families for c in candidates)
    ]


def install_fonts() -> list[str]:
    """Download and install the Fandol font set into the user font dir.

    Refreshes the fontconfig cache afterwards. Returns the list of installed
    ``.otf`` filenames; raises on any failure (caller surfaces the error).
    """
    tmp: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(suffix=".zip")
        tmp = Path(tmp_name)
        with os.fdopen(fd, "wb") as f:
            req = urllib.request.Request(_FONT_ZIP_URL, headers={"User-Agent": "md-sync/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                shutil.copyfileobj(resp, f)

        _INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        installed: list[str] = []
        with zipfile.ZipFile(tmp) as z:
            for name in z.namelist():
                base = Path(name).name
                if base.lower().endswith(".otf") and base.startswith("Fandol"):
                    z.extract(name, _INSTALL_DIR)
                    installed.append(base)
        subprocess.run(
            ["fc-cache", "-f", str(_INSTALL_DIR)],
            capture_output=True, text=True, timeout=60,
        )
        return installed
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
