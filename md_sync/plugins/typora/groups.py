"""Typora 主题分组规则（Qt 与 Web 共用，不依赖 GUI）。"""

from __future__ import annotations

# 分组条件：同一 GitHub 仓库的所有主题为一组，它们通常共享公共前缀
# （参考 https://theme.typora.io/ 按作者仓库分组）。前缀按「长的在前」排序，
# 避免短前缀误吞（如 claude-like 需先于 claude）。未命中的主题归入「其他主题」。
TYPORA_GROUPS: list[tuple[str, str]] = [
    ("Claude-like", "claude-like"),
    ("Novel Tex", "novel-tex-"),
    ("Animal Island", "animal-island"),
    ("Esther Inspired", "esther-inspired-"),
    ("Neil JetBrains Mono", "neil-jetbrains-mono"),
    ("Middle East", "middle-east-"),
    ("Bit Clean", "bit-clean"),
    ("Blue Topaz", "blue-topaz"),
    ("Eyes Green", "eyes-green"),
    ("Konayuki", "konayuki-"),
    ("See-Yue", "see-yue-"),
    ("Themeable", "themeable"),
    ("Autumnus", "autumnus"),
    ("Everforest", "everforest"),
    ("Paperglow", "paperglow"),
    ("Redefine", "redefine"),
    ("Solarized", "solarized"),
    ("Lightmind", "lightmind"),
    ("Lostkeys", "lostkeys"),
    ("Monospace", "monospace"),
    ("Neumorphism", "neumorphism"),
    ("Happysimple", "happysimple"),
    ("Gruvbox", "gruvbox"),
    ("Inkwell", "inkwell"),
    ("Ladder", "ladder"),
    ("Lapis", "lapis"),
    ("Liquid", "liquid"),
    ("MDMDT", "mdmdt"),
    ("MLike", "mlike"),
    ("Onigiri", "onigiri"),
    ("Scrolls", "scrolls"),
    ("Sonnet", "sonnet"),
    ("Tailwind", "tailwind"),
    ("Terminal", "terminal"),
    ("Vintage", "vintage"),
    ("Virgo", "virgo"),
    ("Bloom", "bloom-"),
    ("Nexmoe", "nexmoe-"),
    ("Paradox", "paradox-"),
    ("Quartz", "quartz-"),
    ("Riwaq", "riwaq-"),
    ("Dogs", "dogs-"),
    ("I-W", "i-w-"),
    ("Pink", "pink-"),
    ("Crisp", "crisp-"),
    ("Clean", "clean-"),
    ("Compact", "compact"),
    ("Fluent", "fluent"),
    ("Folio", "folio"),
    ("Jinxiu", "jinxiu"),
    ("Ceylon", "ceylon"),
    ("Cement", "cement"),
    ("Amatriz", "amatriz"),
    ("Bluetex", "bluetex"),
    ("Alto", "alto"),
    ("iA Typora", "ia-typora"),
    ("One Dark", "onedark"),
    ("One Light", "onelight"),
    ("GitHub", "github"),
    ("Notion", "notion"),
    ("Purple", "purple-"),
    ("Phycat", "phycat-"),
    ("Drake", "drake"),
    ("vlook", "vlook-"),
    ("Seniva", "seniva"),
    ("Next", "next"),
    ("Ravel", "ravel"),
    ("Pie", "pie"),
    ("Print", "print"),
    ("Claude", "claude"),
    ("Mint", "mint"),
    ("Mo", "mo-"),
    ("DYZJ", "dyzj"),
    ("Haru", "haru"),
    ("Inside", "inside"),
    ("Vue", "vue"),
    ("Xy", "xy"),
]

# 无法匹配任何分组前缀的主题归入这一组
OTHER_GROUP = "其他主题"


def typora_group_key(stem: str) -> str | None:
    """返回 typora 主题 CSS stem 所属分组名，未命中返回 None。"""
    # 精确匹配优先（如 "mo" 主题：前缀 "mo-" 不含裸 "mo"，但 "mo" 不能作
    # 前缀否则会误吞 morandigarden）
    if stem.lower() == "mo":
        return "Mo"
    for name, prefix in TYPORA_GROUPS:
        if stem.lower().startswith(prefix):
            return name
    return None
