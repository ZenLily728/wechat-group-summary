"""导出编排：快照 -> 群聊定位 -> 时间窗读取 -> messages.json / messages.txt。"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time

from . import parse as msgparse
from .snapshot import Snapshot, find_data_root, list_accounts
from .store import Store, GroupResolutionError

TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")

RANGE_PRESETS = {"24h": 24, "48h": 48, "72h": 72}


def resolve_time_window(range_spec: str | None, start: str | None, end: str | None,
                        now: float | None = None) -> tuple[int, int, str]:
    """返回 (start_ts, end_ts, 描述)。范围含起点、不含终点；截止时间固定为当前时刻。"""
    end_ts = int(now if now is not None else time.time())
    if range_spec:
        hours = RANGE_PRESETS.get(range_spec)
        if hours is None:
            raise ValueError(f"不支持的范围预设: {range_spec}（可选 24h/48h/72h）")
        start_ts = end_ts - hours * 3600
        desc = f"最近{hours}小时"
    else:
        if not start or not end:
            raise ValueError("自定义范围需要 --start 与 --end（格式 2026-10-27 12:00）")
        start_ts = int(dt.datetime.strptime(start, "%Y-%m-%d %H:%M")
                       .replace(tzinfo=TZ).timestamp())
        end_ts = int(dt.datetime.strptime(end, "%Y-%m-%d %H:%M")
                     .replace(tzinfo=TZ).timestamp())
        if end_ts <= start_ts:
            raise ValueError("结束时间必须晚于开始时间")
        desc = "自定义范围"
    return start_ts, end_ts, desc


def pick_account(account_name: str | None, log=print) -> dict:
    root = find_data_root()
    if not root:
        raise RuntimeError("未找到 xwechat_files 数据目录")
    accounts = list_accounts(root)
    if not accounts:
        raise RuntimeError(f"{root} 下没有账号目录")
    if account_name:
        matched = [a for a in accounts if account_name in a["name"] or account_name == a["wxid"]]
        if len(matched) != 1:
            raise RuntimeError(f"账号匹配不唯一或不存在: {account_name} -> "
                               f"{[a['name'] for a in accounts]}")
        return matched[0]
    currents = [a for a in accounts if a.get("is_current")]
    if len(currents) == 1:
        log(f"[*] 当前登录账号: {currents[0]['name']}（共 {len(accounts)} 个账号，互不混读）")
        return currents[0]
    raise RuntimeError("无法确定当前登录账号，请用 --account 指定: "
                       + ", ".join(a["name"] for a in accounts))


def export_messages(group_name: str, range_spec: str | None = "24h",
                    start: str | None = None, end: str | None = None,
                    account: str | None = None, out_root: str = "outputs",
                    log=print) -> str:
    """完整导出流程，返回输出目录。"""
    start_ts, end_ts, range_desc = resolve_time_window(range_spec, start, end)
    tz_name = "Asia/Shanghai (UTC+8)"
    log(f"[*] 统计窗口: [{_fmt(start_ts)}, {_fmt(end_ts)}) {tz_name} —— 含起点不含终点")

    acct = pick_account(account, log=log)
    with Snapshot(acct["dir"], log=log) as snap:
        store = Store(snap.db_path("message/message_0.db"),
                      snap.db_path("contact/contact.db"),
                      snap.db_path("session/session.db"))
        try:
            group = store.resolve_group(group_name)
            log(f"[*] 群聊定位: {group['name']} <{group['username']}> "
                f"成员数={group.get('member_count', '未知')}")
            raw_msgs = store.fetch_messages(group["username"], start_ts, end_ts)
            my_wxid = acct["wxid"]
            messages = []
            for m in raw_msgs:
                p = msgparse.parse_message(m)
                messages.append({
                    "id": f"m{m['local_id']}",
                    "local_id": m["local_id"],
                    "server_id": m["server_id"],
                    "ts": m["create_time"],
                    "time": _fmt(m["create_time"]),
                    "sender": m["sender"],
                    "sender_username": m["sender_username"],
                    "is_self": m["sender_username"] == my_wxid,
                    "kind": p["kind"],
                    "text": p["text"],
                    "quote": p["quote"],
                    "link": p["link"],
                    "file": p["file"],
                    "voice_text": p["voice_text"],
                })
        finally:
            store.close()
        snap_stats = dict(snap.stats)

    senders = sorted({m["sender"] for m in messages})
    type_stat: dict[str, int] = {}
    for m in messages:
        type_stat[m["kind"]] = type_stat.get(m["kind"], 0) + 1

    meta = {
        "group_name": group["name"],
        "group_remark": group.get("remark") or "",
        "group_username": group["username"],
        "member_count": group.get("member_count"),
        "account": acct["name"],
        "range_desc": range_desc,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start_time": _fmt(start_ts),
        "end_time": _fmt(end_ts),
        "timezone": tz_name,
        "range_rule": "含起点，不含终点 [start, end)",
        "exported_at": _fmt(int(time.time())),
        "message_count": len(messages),
        "sender_count": len(senders),
        "type_stat": type_stat,
        "db_used": ["message/message_0.db", "contact/contact.db", "session/session.db"],
        "wal_applied_pages": {k: v["wal_pages_applied"] for k, v in snap_stats.items()},
        "scope_note": ("本报告仅覆盖本机微信客户端已同步到本地的消息；"
                       "本地未同步的历史消息不在统计范围内。"),
    }

    ts_dir = dt.datetime.fromtimestamp(end_ts).strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", group["name"])[:40]
    out_dir = os.path.abspath(os.path.join(out_root, f"{ts_dir}_{safe}"))
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "messages.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "messages": messages}, f, ensure_ascii=False, indent=1)

    with open(os.path.join(out_dir, "messages.txt"), "w", encoding="utf-8") as f:
        f.write(f"# {meta['group_name']}  消息导出\n")
        f.write(f"# 窗口: [{meta['start_time']}, {meta['end_time']}) {tz_name}（含起点不含终点）\n")
        f.write(f"# 共 {meta['message_count']} 条 / {meta['sender_count']} 人发言\n\n")
        for m in messages:
            line = f"[{m['id']}|{m['time']}] {m['sender']}"
            if m["kind"] != "文本":
                line += f"（{m['kind']}）"
            f.write(line + "\n")
            if m["text"]:
                f.write("  " + m["text"].replace("\n", "\n  ") + "\n")
            if m["quote"]:
                f.write(f"  └ 引用 {m['quote']['refer_sender']}: "
                        f"{m['quote']['refer_content'][:200]}\n")
            if m["link"]:
                f.write(f"  └ 链接: {m['link']['url']}\n")
    log(f"[+] 导出完成: {len(messages)} 条消息 -> {out_dir}")
    return out_dir


def _fmt(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M:%S")
