#!/usr/bin/env python3
"""生成 gatecheck 终端演示图（SVG，文本矢量，README 直接引用）。

内容逐行取自 2026-10-31 本机实跑输出，仅隐去运行时 owner 字段。
用法：python3 docs/render-demo-svg.py docs/demo.svg
"""
import sys
from xml.sax.saxutils import escape

W, H = 1040, 650
BG, BAR = "#0D1117", "#161B22"
C = {
    "white": "#E6EDF3",
    "dim": "#7D8A97",
    "green": "#3FB950",
    "red": "#F85149",
    "orange": "#D29922",
    "blue": "#58A6FF",
}

LINES = [
    ("$ gatecheck lint drafts/ --platform toutiao", "white"),
    ("", "white"),
    ("⚠ G001 drafts/demo.md [toutiao] — 没有旁挂 meta，正文也找不到可点/可跑的锚点", "orange"),
    ("   fix: 补 <name>.meta.json 的 sources[]，或正文加入官方链接/可复现命令", "dim"),
    ("", "white"),
    ("✗ G002 drafts/demo.md:5 [toutiao] — 对外数量未核实：'合集共九篇'", "red"),
    ("   fix: 改成「本系列第 N 篇」这类不承诺总量的说法", "dim"),
    ("   或在 meta.verified_numbers 写前台实测数", "dim"),
    ("", "white"),
    ("ℹ G007 drafts/demo.md [toutiao] — 未记录 AI 声明状态", "blue"),
    ("结论：FAIL（1 fail / 1 warn / 1 info）", "white"),
    ("", "white"),
    ('$ gatecheck claim --platform toutiao --title "八月电费复盘"', "white"),
    ("✓ 已占锁：toutiao / 八月电费复盘", "green"),
    ("", "white"),
    ('$ gatecheck verify --platform toutiao --title "八月电费复盘" \\', "white"),
    ('    --evidence "列表里没有同标题行"', "white"),
    ("✓ 发前复查已记录（2026-10-31T08:04:06+08:00）", "green"),
    ("  现在 60 秒内点发布；超时重跑 verify", "dim"),
    ("", "white"),
    ('$ gatecheck publish --platform toutiao --title "八月电费复盘"', "white"),
    ("✗ 发前复查已过期 5 秒（窗口 60 秒），重新 verify 再点发布", "red"),
]


def build() -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="Menlo, SFMono-Regular, Consolas, \'Liberation Mono\', monospace" font-size="17">',
        f'<rect width="{W}" height="{H}" fill="{BG}"/>',
        f'<rect width="{W}" height="38" fill="{BAR}"/>',
    ]
    for i, color in enumerate(("#FF5F56", "#FFBD2E", "#27C942")):
        out.append(f'<circle cx="{24 + i * 24}" cy="19" r="6.5" fill="{color}"/>')
    out.append(f'<text x="100" y="25" fill="{C["dim"]}" font-size="15">gatecheck — demo</text>')

    top = 62
    for content, key in LINES:
        if content:
            baseline = top + 15
            out.append(f'<text x="24" y="{baseline}" fill="{C[key]}" xml:space="preserve">{escape(content)}</text>')
        top += 28
    out.append("</svg>")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else "docs/demo.svg"
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(build())
    print(f"wrote {dest}")
