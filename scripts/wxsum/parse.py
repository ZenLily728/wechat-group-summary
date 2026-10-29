"""消息内容解析：类型解码、zstd 解压、XML 提取（引用/链接/文件卡片等）。

微信 4.x 的 local_type 是打包值：低 32 位为基础类型，高 32 位为 appmsg 子类型。
部分消息的 message_content 为 zstd 压缩的 XML（魔数 0xFD2FB528）。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

_SENDER_PREFIX = re.compile(r"^([A-Za-z0-9_\-]+(?:@[A-Za-z0-9.]+)?):\n")

TYPE_NAMES = {
    1: "文本",
    3: "图片",
    34: "语音",
    43: "视频",
    47: "表情",
    42: "名片",
    48: "位置",
    49: "应用消息",
    65: "图文",
    66: "笔记",
    10000: "系统消息",
    244813135921: "引用",  # 57<<32|49 常见形态，仍以 XML 为准
}

# 微信 4.x 中观察到的"自己发出"标记位（sender 为本人时 real_sender_id 映射为自己）


def decompress_if_needed(body):
    """message_content 可能是 zstd 压缩的 bytes/str。返回解压后的 str 或原文。"""
    if isinstance(body, str):
        raw = body.encode("latin-1", errors="ignore")
        if not raw.startswith(ZSTD_MAGIC):
            return body
    else:
        raw = bytes(body)
        if not raw.startswith(ZSTD_MAGIC):
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:
                return repr(raw)
    try:
        import zstandard
        dctx = zstandard.ZstdDecompressor()
        data = dctx.stream_reader(raw)
        out = data.read().decode("utf-8", errors="replace")
        return out
    except Exception:
        return "<无法解压的压缩内容>"


def _xml(text: str) -> ET.Element | None:
    text = text.strip()
    if not text.startswith("<"):
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        # 有些 XML 含未转义字符，尝试包一层
        try:
            return ET.fromstring(f"<root>{text}</root>")
        except ET.ParseError:
            return None


def _txt(elem: ET.Element | None, path: str) -> str:
    if elem is None:
        return ""
    e = elem.find(path)
    return (e.text or "").strip() if e is not None and e.text else ""


def _refer_text(content: str) -> str:
    """引用的原始内容可能是纯文本，也可能是嵌套的 appmsg XML（引用了引用/链接）。"""
    c = (content or "").strip()
    if c.startswith("<"):
        x = _xml(c)
        if x is not None:
            t = _txt(x, ".//title")
            if t:
                return t
            plain = re.sub(r"<[^>]+>", "", c).strip()
            return plain[:200]
        return c[:200]
    return c


def _parse_sysmsg(text: str) -> str:
    """把 sysmsgtemplate 渲染成可读文本，如 "A"邀请"B"加入了群聊。"""
    x = _xml(text)
    if x is None:
        return re.sub(r"<[^>]+>", "", text).strip()[:300]
    tmpl = x.find(".//content_template/template")
    if tmpl is None or not tmpl.text:
        return re.sub(r"<[^>]+>", "", text).strip()[:300]
    out = tmpl.text
    for link in x.findall(".//link_list/link"):
        name = link.get("name", "")
        nicks = [ (m.findtext("nickname") or "") for m in link.findall(".//member") ]
        sep = link.findtext("separator") or "、"
        out = out.replace(f"${name}$", sep.join(n for n in nicks if n))
    return out


def parse_message(msg: dict) -> dict:
    """把原始消息行解析为结构化消息。输入来自 Store.fetch_messages。"""
    lt = msg["local_type"]
    base = lt & 0xFFFFFFFF
    sub = (lt >> 32) & 0xFFFFFFFF
    body = msg.get("body") or ""
    if isinstance(body, bytes):
        body = decompress_if_needed(body)  # 统一成 str（含 zstd 压缩文本）
    # 群消息前缀 "wxid_xxx:\n" 可能在压缩包内部，解压后再剥离
    m_prefix = _SENDER_PREFIX.match(body)
    if m_prefix:
        body = body[m_prefix.end():]

    parsed = {
        "type": base,
        "subtype": sub if base == 49 else None,
        "kind": TYPE_NAMES.get(base, f"类型{base}"),
        "text": "",
        "quote": None,
        "link": None,
        "file": None,
        "voice_text": None,
    }

    if base == 1:
        parsed["text"] = body.strip()
        return parsed

    if base in (3, 43, 47, 34, 48, 10000):
        if base == 34:
            # 语音：若 XML 内含转写文本则提取（voicetrans 元素的 text 属性）
            x = _xml(body)
            vt = ""
            if x is not None:
                node = x.find(".//voicetrans")
                if node is not None:
                    vt = node.get("text") or (node.text or "")
                if not vt:
                    vt = _txt(x, ".//voicevoicetrans") or _txt(x, ".//transcript")
            if vt:
                parsed["voice_text"] = vt
                parsed["text"] = f"[语音转写] {vt}"
        if base == 10000:
            parsed["text"] = _parse_sysmsg(body)
        return parsed

    content = body
    x = _xml(content)

    if base == 42:  # 名片
        parsed["text"] = "[名片]"
        if x is not None:
            nick = x.get("nickname") or _txt(x, "nickname")
            uname = x.get("username") or ""
            parsed["text"] = f"[名片] {nick or uname}"
        return parsed

    if base == 49 and x is not None:
        app = x.find("appmsg")
        inner_type = _txt(app, "type") if app is not None else ""
        title = _txt(app, "title")
        des = _txt(app, "des")
        url = _txt(app, "url")
        finder = x.find(".//finderFeed")
        if finder is not None or inner_type == "51":
            # 视频号分享
            nick = _txt(finder, "nickname") if finder is not None else ""
            fdesc = _txt(finder, "desc") if finder is not None else ""
            parsed["kind"] = "视频号"
            parsed["text"] = f"[视频号] {nick}" + (f"：{fdesc}" if fdesc else "")
        elif sub == 57 or inner_type == "57":
            # 引用消息
            refer = app.find("refermsg") if app is not None else None
            parsed["kind"] = "引用"
            parsed["text"] = title
            parsed["quote"] = {
                "title": title,
                "refer_sender": _txt(refer, "displayname"),
                "refer_content": _refer_text(_txt(refer, "content")),
            }
        elif inner_type == "5" or (url and title):
            parsed["kind"] = "链接"
            parsed["link"] = {"title": title, "desc": des, "url": url}
            parsed["text"] = f"[链接] {title}" + (f" - {des}" if des else "")
        elif inner_type == "6":
            parsed["kind"] = "文件"
            parsed["file"] = {"name": title, "size": _txt(app, "totalsize")}
            parsed["text"] = f"[文件] {title}"
        elif inner_type in ("33", "36"):
            parsed["kind"] = "小程序"
            parsed["text"] = f"[小程序] {title}"
        elif inner_type:
            parsed["kind"] = f"应用({inner_type})"
            parsed["text"] = f"[{parsed['kind']}] {title or des}"
        else:
            parsed["text"] = (title or des or "").strip()
        return parsed

    # 兜底
    text = content.strip() if isinstance(content, str) else ""
    parsed["text"] = text[:500]
    return parsed


def summarize_type_label(parsed: dict) -> str:
    """用于统计的类型标签。"""
    if parsed["kind"] == "应用消息" and parsed.get("subtype"):
        return f"应用消息(sub={parsed['subtype']})"
    return parsed["kind"]
