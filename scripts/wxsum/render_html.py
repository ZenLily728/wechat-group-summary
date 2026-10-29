"""离线 HTML 报告渲染：单文件、无外部依赖、内容转义、原文默认折叠。"""
from __future__ import annotations

import html
import json
import os


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _refs(ids, msg_index):
    links = []
    for mid in ids or []:
        m = msg_index.get(mid)
        if m:
            links.append(f'<a href="#msg-{_e(mid)}" class="ref" title="{_e(m["time"])} '
                         f'{_e(m["sender"])}: {_e(m["text"][:80])}">[{_e(mid)}]</a>')
        else:
            links.append(f'<span class="ref ref-missing">[{_e(mid)}?]</span>')
    return " ".join(links)


def render_html(out_dir: str) -> str:
    with open(os.path.join(out_dir, "messages.json"), encoding="utf-8") as f:
        data = json.load(f)
    with open(os.path.join(out_dir, "report.json"), encoding="utf-8") as f:
        report = json.load(f)
    summary_md = ""
    md_path = os.path.join(out_dir, "summary.md")
    if os.path.exists(md_path):
        with open(md_path, encoding="utf-8") as f:
            summary_md = f.read()

    meta = data["meta"]
    messages = data["messages"]
    msg_index = {m["id"]: m for m in messages}

    def section_title(t):
        return f'<h2>{_e(t)}</h2>'

    parts = []
    parts.append(section_title("📋 概览"))
    parts.append(f'<p class="overview">{_e(report.get("overview", ""))}</p>')

    if report.get("topics"):
        parts.append(section_title("💬 主要话题与讨论结论"))
        for i, t in enumerate(report["topics"], 1):
            parts.append('<div class="card">')
            parts.append(f'<h3>{i}. {_e(t.get("title", ""))}</h3>')
            parts.append(f'<p>{_e(t.get("summary", ""))}</p>')
            if t.get("conclusion"):
                parts.append(f'<p class="conclusion"><b>结论：</b>{_e(t["conclusion"])}</p>')
            parts.append(f'<p class="refs">来源：{_refs(t.get("msg_ids"), msg_index)}</p>')
            parts.append('</div>')

    if report.get("todos"):
        parts.append(section_title("✅ 待办事项"))
        parts.append('<table><thead><tr><th>事项</th><th>负责人</th><th>时间要求</th>'
                     '<th>当前状态</th><th>来源</th></tr></thead><tbody>')
        for t in report["todos"]:
            parts.append(
                f'<tr><td>{_e(t.get("item", ""))}</td>'
                f'<td>{_e(t.get("owner") or "未明确")}</td>'
                f'<td>{_e(t.get("due") or "未明确")}</td>'
                f'<td>{_e(t.get("status") or "未明确")}</td>'
                f'<td>{_refs(t.get("msg_ids"), msg_index)}</td></tr>')
        parts.append('</tbody></table>')

    if report.get("resolved"):
        parts.append(section_title("✔️ 已解决 / 已确认事项"))
        parts.append('<ul>')
        for t in report["resolved"]:
            parts.append(f'<li>{_e(t.get("item", ""))} '
                         f'<span class="refs">{_refs(t.get("msg_ids"), msg_index)}</span></li>')
        parts.append('</ul>')

    if report.get("open_questions"):
        parts.append(section_title("❓ 尚未解决的问题"))
        parts.append('<ul>')
        for t in report["open_questions"]:
            parts.append(f'<li>{_e(t.get("item", ""))} '
                         f'<span class="refs">{_refs(t.get("msg_ids"), msg_index)}</span></li>')
        parts.append('</ul>')

    if report.get("other"):
        parts.append(section_title("ℹ️ 其他信息"))
        parts.append(f'<p>{_e(report["other"])}</p>')

    if report.get("caveats"):
        parts.append(section_title("⚠️ 口径与注意事项"))
        parts.append('<ul>' + ''.join(f'<li>{_e(c)}</li>' for c in report["caveats"]) + '</ul>')

    # 原始消息（默认折叠）
    parts.append(section_title("📨 原始消息（默认折叠，点击展开核对）"))
    parts.append('<details class="raw"><summary>展开 / 收起全部 '
                 f'{len(messages)} 条消息</summary><div class="msgs">')
    for m in messages:
        cls = "msg self" if m.get("is_self") else "msg"
        parts.append(
            f'<div class="{cls}" id="msg-{_e(m["id"])}">'
            f'<span class="mid">[{_e(m["id"])}]</span> '
            f'<span class="mtime">{_e(m["time"])}</span> '
            f'<b class="msender">{_e(m["sender"])}</b>'
            + (f'<span class="mkind">（{_e(m["kind"])}）</span>' if m["kind"] != "文本" else "")
            + f'<div class="mtext">{_e(m["text"])}</div>'
            + (f'<div class="mquote">└ 引用 {_e(m["quote"]["refer_sender"])}: '
               f'{_e(m["quote"]["refer_content"])}</div>' if m.get("quote") else "")
            + (f'<div class="mlink">└ <a href="{_e(m["link"]["url"])}">'
               f'{_e(m["link"]["url"])}</a></div>' if m.get("link") else "")
            + '</div>')
    parts.append('</div></details>')

    body = "\n".join(parts)
    type_stat = " · ".join(f"{_e(k)} {v}" for k, v in meta.get("type_stat", {}).items())
    page = _TEMPLATE
    plain = {
        "GROUP": meta["group_name"], "START": meta["start_time"], "END": meta["end_time"],
        "TZ": meta["timezone"], "RULE": meta["range_rule"],
        "COUNT": meta["message_count"], "SENDERS": meta["sender_count"],
        "MEMBERS": meta.get("member_count") or "未知",
        "EXPORTED": meta["exported_at"], "SCOPE": meta["scope_note"],
    }
    for k, v in plain.items():
        page = page.replace("{{" + k + "}}", _e(v))
    page = page.replace("{{TYPESTAT}}", type_stat)   # 已逐项转义
    page = page.replace("{{BODY}}", body)            # 已构造为 HTML
    # BODY 已是 HTML，其余替换值转义
    out = os.path.join(out_dir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return out


_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{GROUP}} - 群聊总结</title>
<style>
:root{color-scheme:light}
*{box-sizing:border-box}
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
margin:0;background:#f5f6f8;color:#1f2329;line-height:1.75}
.wrap{max-width:860px;margin:0 auto;padding:20px 16px 60px}
header.top{background:linear-gradient(135deg,#07c160,#05a657);color:#fff;
border-radius:14px;padding:22px 22px 18px;margin-bottom:18px}
header.top h1{font-size:20px;margin:0 0 8px}
.meta{font-size:13px;opacity:.95}
.meta div{margin:2px 0}
.badge{display:inline-block;background:rgba(255,255,255,.18);border-radius:6px;
padding:1px 8px;margin:2px 6px 2px 0;font-size:12px}
h2{font-size:17px;border-left:4px solid #07c160;padding-left:10px;margin:26px 0 12px}
h3{font-size:15px;margin:0 0 6px}
.card{background:#fff;border:1px solid #e5e6eb;border-radius:10px;padding:14px 16px;margin:10px 0}
.overview{background:#fff;border:1px solid #e5e6eb;border-radius:10px;padding:14px 16px}
.conclusion{color:#0a7d43}
.refs{font-size:12px;color:#8a8f99}
.ref{display:inline-block;background:#eef0f3;border-radius:4px;padding:0 6px;
margin:1px 2px;font-size:11px;color:#576b95;text-decoration:none}
.ref-missing{color:#c00}
table{width:100%;border-collapse:collapse;background:#fff;font-size:13px}
th,td{border:1px solid #e5e6eb;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#f2f3f5}
details.raw{background:#fff;border:1px solid #e5e6eb;border-radius:10px;padding:10px 14px}
details.raw summary{cursor:pointer;color:#576b95;font-size:14px}
.msg{padding:8px 4px;border-bottom:1px dashed #eceef1;font-size:13.5px}
.msg.self{background:#f0f9f1}
.mid{color:#9aa0a6;font-size:11px}
.mtime{color:#9aa0a6;font-size:12px;margin-left:4px}
.msender{color:#576b95}
.mkind{color:#b06010;font-size:12px}
.mtext{margin-top:2px;white-space:pre-wrap;word-break:break-word}
.mquote,.mlink{color:#8a8f99;font-size:12px;margin-top:2px;word-break:break-all}
footer{margin-top:30px;font-size:12px;color:#9aa0a6;text-align:center}
@media(max-width:600px){.wrap{padding:12px 8px 40px}header.top h1{font-size:17px}}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
<h1>{{GROUP}}</h1>
<div class="meta">
<div>统计窗口：<b>{{START}} ~ {{END}}</b>（{{TZ}}，{{RULE}}）</div>
<div>
<span class="badge">消息 {{COUNT}} 条</span>
<span class="badge">发言 {{SENDERS}} 人</span>
<span class="badge">群成员 {{MEMBERS}} 人</span>
<span class="badge">导出于 {{EXPORTED}}</span>
</div>
<div style="margin-top:6px;font-size:12px">类型分布：{{TYPESTAT}}</div>
<div style="margin-top:6px;font-size:12px">⚠ {{SCOPE}}</div>
</div>
</header>
{{BODY}}
<footer>本报告由本地工具链生成，数据来自本机微信已同步记录 · 网页为离线单文件，不含外部资源</footer>
</div>
</body>
</html>
"""
