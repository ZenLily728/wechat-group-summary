"""wxsum 命令行入口。

用法：
  python -m wxsum groups                          列出本机群聊（含群ID/成员数/最近活跃）
  python -m wxsum export --group 完整群名 [--range 24h|48h|72h | --start "YYYY-MM-DD HH:MM" --end ...] [--account 账号目录名]
  python -m wxsum render --dir outputs/<目录>      由 report.json + messages.json 生成 index.html / report.png
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from .pipeline import export_messages, pick_account
from .snapshot import Snapshot


def cmd_groups(args):
    acct = pick_account(args.account)
    with Snapshot(acct["dir"]) as snap:
        from .store import Store
        store = Store(snap.db_path("message/message_0.db"),
                      snap.db_path("contact/contact.db"),
                      snap.db_path("session/session.db"))
        groups = store.list_groups()
        store.close()
    for g in groups:
        ts = dt.datetime.fromtimestamp(g["last_ts"]).strftime("%m-%d %H:%M") if g["last_ts"] else "-"
        print(f"{g['username']:32} {ts}  {g['name']}" + (f"（备注:{g['remark']}）" if g["remark"] else ""))
    print(f"\n共 {len(groups)} 个群聊")


def cmd_export(args):
    out = export_messages(
        group_name=args.group,
        range_spec=args.range if not args.start else None,
        start=args.start, end=args.end,
        account=args.account,
        out_root=args.out,
    )
    print(json.dumps({"out_dir": out}, ensure_ascii=False))


def cmd_render(args):
    from .render_html import render_html
    from .render_png import render_png
    html_path = render_html(args.dir)
    print(f"[+] HTML: {html_path}")
    pngs = render_png(args.dir)
    for p in pngs:
        print(f"[+] PNG: {p}")


def main():
    ap = argparse.ArgumentParser(prog="wxsum", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("groups", help="列出群聊")
    g.add_argument("--account")
    g.set_defaults(fn=cmd_groups)

    e = sub.add_parser("export", help="导出指定群聊指定时间窗的消息")
    e.add_argument("--group", required=True, help="完整群名（或备注）")
    e.add_argument("--range", default="24h", choices=["24h", "48h", "72h"])
    e.add_argument("--start", help='自定义起点 "2026-10-27 12:00"（含）')
    e.add_argument("--end", help='自定义终点（不含）')
    e.add_argument("--account", help="账号目录名（多账号时用于显式指定）")
    e.add_argument("--out", default="outputs")
    e.set_defaults(fn=cmd_export)

    r = sub.add_parser("render", help="渲染 HTML/PNG 报告")
    r.add_argument("--dir", required=True)
    r.set_defaults(fn=cmd_render)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
