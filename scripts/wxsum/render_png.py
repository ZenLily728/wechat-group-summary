"""PNG 长图渲染：Pillow 本地绘制，中文字体，按内容计算高度，超长自动编号分图。"""
from __future__ import annotations

import json
import os

from PIL import Image, ImageDraw, ImageFont

WIDTH = 900
MARGIN = 40
MAX_HEIGHT = 16000  # 单图高度上限，超出则编号分图

BG = (245, 246, 248)
CARD = (255, 255, 255)
GREEN = (7, 193, 96)
DARK = (31, 35, 41)
GRAY = (138, 143, 153)
BLUE = (87, 107, 149)
BORDER = (229, 230, 235)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise RuntimeError("未找到可用中文字体（msyh/simhei/simsun）")


class Canvas:
    """流式排版：append 文本块，自动换行、分页。"""

    def __init__(self):
        self.pages: list[Image.Image] = []
        self.img: Image.Image | None = None
        self.draw: ImageDraw.ImageDraw | None = None
        self.y = 0
        self._new_page()

    def _new_page(self):
        self.img = Image.new("RGB", (WIDTH, MAX_HEIGHT), BG)
        self.draw = ImageDraw.Draw(self.img)
        self.y = MARGIN

    def _ensure(self, h: int):
        if self.y + h > MAX_HEIGHT - MARGIN:
            self._flush_page()
            self._new_page()

    def _flush_page(self):
        if self.img is None:
            return
        final = self.img.crop((0, 0, WIDTH, min(self.y + MARGIN, MAX_HEIGHT)))
        self.pages.append(final)
        self.img = None

    # --------------------------------------------------------------
    def text(self, s: str, size=16, color=DARK, bold=False, indent=0,
             line_gap=6, para_gap=10, width=None):
        font = _font(size)
        w = (width or WIDTH - 2 * MARGIN) - indent
        for para in s.split("\n"):
            lines = _wrap(self.draw, para, font, w)
            if not lines:
                lines = [""]
            for ln in lines:
                h = font.getbbox("国")[3] + line_gap
                self._ensure(h)
                self.draw.text((MARGIN + indent, self.y), ln, font=font, fill=color)
                self.y += h
            self.y += para_gap

    def title_bar(self, title: str, sub_lines: list[str]):
        h = 90 + 26 * len(sub_lines)
        self._ensure(h + 20)
        self.draw.rounded_rectangle(
            [MARGIN, self.y, WIDTH - MARGIN, self.y + h], radius=14, fill=GREEN)
        self.draw.text((MARGIN + 22, self.y + 18), title, font=_font(26), fill=(255, 255, 255))
        yy = self.y + 58
        for ln in sub_lines:
            self.draw.text((MARGIN + 22, yy), ln, font=_font(14), fill=(230, 255, 240))
            yy += 26
        self.y += h + 24

    def section(self, title: str):
        self._ensure(50)
        self.draw.rectangle([MARGIN, self.y + 6, MARGIN + 5, self.y + 30], fill=GREEN)
        self.draw.text((MARGIN + 14, self.y), title, font=_font(21), fill=DARK)
        self.y += 44

    def card(self, lines: list[tuple[str, int, tuple]]):
        """圆角卡片内排若干 (text, size, color) 段落。"""
        pad = 16
        # 先量高度
        total = pad
        meas: list[list[str]] = []
        for text, size, _c in lines:
            font = _font(size)
            wrapped = []
            for para in text.split("\n"):
                wrapped += _wrap(self.draw, para, font, WIDTH - 2 * MARGIN - 2 * pad) or [""]
            meas.append(wrapped)
            total += len(wrapped) * (size + 6) + 6
        total += pad
        self._ensure(total + 12)
        top = self.y
        self.draw.rounded_rectangle(
            [MARGIN, top, WIDTH - MARGIN, top + total], radius=10,
            fill=CARD, outline=BORDER)
        yy = top + pad
        for (text, size, color), wrapped in zip(lines, meas):
            font = _font(size)
            for ln in wrapped:
                self.draw.text((MARGIN + pad, yy), ln, font=font, fill=color)
                yy += size + 6
            yy += 6
        self.y = top + total + 14

    def spacer(self, h=10):
        self.y += h

    def save(self, out_dir: str, base="report") -> list[str]:
        self._flush_page()
        paths = []
        if len(self.pages) == 1:
            p = os.path.join(out_dir, f"{base}.png")
            self.pages[0].save(p, optimize=True)
            paths.append(p)
        else:
            for i, pg in enumerate(self.pages, 1):
                p = os.path.join(out_dir, f"{base}_{i}.png")
                pg.save(p, optimize=True)
                paths.append(p)
        return paths


def _wrap(draw, text, font, width) -> list[str]:
    """按像素宽度换行（中文友好：逐字符测量）。"""
    if not text:
        return []
    lines, cur = [], ""
    for ch in text:
        trial = cur + ch
        if draw.textlength(trial, font=font) > width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def render_png(out_dir: str) -> list[str]:
    with open(os.path.join(out_dir, "messages.json"), encoding="utf-8") as f:
        data = json.load(f)
    with open(os.path.join(out_dir, "report.json"), encoding="utf-8") as f:
        report = json.load(f)
    meta = data["meta"]
    msg_index = {m["id"]: m for m in data["messages"]}

    c = Canvas()
    c.title_bar(meta["group_name"], [
        f"统计窗口：{meta['start_time']} ~ {meta['end_time']}（{meta['timezone']}）",
        f"含起点不含终点 · 消息 {meta['message_count']} 条 · 发言 {meta['sender_count']} 人"
        f" · 群成员 {meta.get('member_count') or '未知'} 人",
        f"导出于 {meta['exported_at']} · 仅含本机已同步记录",
    ])

    c.section("【概览】")
    c.card([(report.get("overview", ""), 16, DARK)])

    if report.get("topics"):
        c.section("【主要话题与讨论结论】")
        for i, t in enumerate(report["topics"], 1):
            lines = [(f"{i}. {t.get('title', '')}", 17, DARK),
                     (t.get("summary", ""), 15, DARK)]
            if t.get("conclusion"):
                lines.append(("结论：" + t["conclusion"], 15, (10, 125, 67)))
            refs = _ref_text(t.get("msg_ids"), msg_index)
            if refs:
                lines.append(("来源：" + refs, 12, GRAY))
            c.card(lines)

    if report.get("todos"):
        c.section("【待办事项】")
        for i, t in enumerate(report["todos"], 1):
            c.card([
                (f"{i}. {t.get('item', '')}", 16, DARK),
                (f"负责人：{t.get('owner') or '未明确'}　时间要求：{t.get('due') or '未明确'}"
                 f"　状态：{t.get('status') or '未明确'}", 14, BLUE),
                *([("来源：" + _ref_text(t.get("msg_ids"), msg_index), 12, GRAY)]
                  if t.get("msg_ids") else []),
            ])

    if report.get("resolved"):
        c.section("【已解决 / 已确认事项】")
        c.card([("\n".join(
            f"• {t.get('item', '')}"
            + (f"（{_ref_text(t.get('msg_ids'), msg_index)}）" if t.get("msg_ids") else "")
            for t in report["resolved"]), 15, DARK)])

    if report.get("open_questions"):
        c.section("【尚未解决的问题】")
        c.card([("\n".join(
            f"• {t.get('item', '')}"
            + (f"（{_ref_text(t.get('msg_ids'), msg_index)}）" if t.get("msg_ids") else "")
            for t in report["open_questions"]), 15, DARK)])

    if report.get("other"):
        c.section("【其他信息】")
        c.card([(report["other"], 15, DARK)])

    if report.get("caveats"):
        c.section("【口径与注意事项】")
        c.card([("\n".join(f"• {x}" for x in report["caveats"]), 14, GRAY)])

    c.spacer(20)
    c.text("本报告由本地工具链生成 · 数据来自本机微信已同步记录", 12, GRAY)
    paths = c.save(out_dir)
    if len(paths) > 1:
        print(f"[INFO] 内容过长，已分为 {len(paths)} 张编号长图（单图高度上限 "
              f"{MAX_HEIGHT}px，为避免裁切而分页）")
    return paths


def _ref_text(ids, msg_index) -> str:
    out = []
    for mid in ids or []:
        m = msg_index.get(mid)
        if m:
            out.append(f"[{mid}]{m['time'][5:16]} {m['sender']}")
        else:
            out.append(f"[{mid}?]")
    return " ".join(out)
