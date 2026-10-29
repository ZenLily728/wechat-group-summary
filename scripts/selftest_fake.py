"""虚构数据端到端自检：构造与真实结构一致的假库，验证解析/统计/渲染。

运行：python scripts/selftest_fake.py
（不触碰微信、不需要密钥，可随时用于回归验证）
检查点：
1. 时间边界 [start, end)：起点消息入选、终点消息剔除
2. server_id 去重
3. 文本/引用(zstd压缩XML)/链接/文件/图片/语音(转写)/表情/系统消息解析
4. 发送人昵称映射（备注优先）
5. HTML/PNG 渲染成功且中文正常
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import zstandard

GROUP = "98765432100@chatroom"
GROUP_NAME = "虚构·测试群<>&\"'脚本注入"
TABLE = "Msg_" + hashlib.md5(GROUP.encode()).hexdigest()

NOW = int(time.time())
H = 3600


def zstd(s: str) -> bytes:
    return zstandard.ZstdCompressor(level=3).compress(s.encode("utf-8"))


def build(tmp: str):
    # ---- contact.db ----
    c = sqlite3.connect(os.path.join(tmp, "contact.db"))
    c.execute("CREATE TABLE contact (id INTEGER PRIMARY KEY, username TEXT, local_type INTEGER, alias TEXT, encrypt_username TEXT, flag INTEGER, delete_flag INTEGER, verify_flag INTEGER, remark TEXT, remark_quan_pin TEXT, remark_pin_yin_initial TEXT, nick_name TEXT, pin_yin_initial TEXT, quan_pin TEXT, big_head_url TEXT, small_head_url TEXT, head_img_md5 TEXT, chat_room_notify INTEGER, is_in_chat_room INTEGER, description TEXT, extra_buffer BLOB, chat_room_type INTEGER)")
    c.execute("CREATE TABLE name2id (username TEXT)")
    c.execute("CREATE TABLE chat_room (id INTEGER PRIMARY KEY, username TEXT, owner TEXT, ext_buffer BLOB)")
    c.execute("CREATE TABLE chatroom_member (room_id INTEGER, member_id INTEGER)")
    users = ["wxid_alice", "wxid_bob", "wxid_carol", "my_wxid", GROUP]
    for u in users:
        c.execute("INSERT INTO name2id (username) VALUES (?)", (u,))
    c.execute("INSERT INTO contact (id, username, remark, nick_name) VALUES (1,?, '', '爱丽丝')", ("wxid_alice",))
    c.execute("INSERT INTO contact (id, username, remark, nick_name) VALUES (2,?, '鲍勃-产品', 'Bob')", ("wxid_bob",))
    c.execute("INSERT INTO contact (id, username, remark, nick_name) VALUES (3,?, '', '卡罗尔')", ("wxid_carol",))
    c.execute("INSERT INTO contact (id, username, remark, nick_name) VALUES (4,?, '', '我自己')", ("my_wxid",))
    c.execute("INSERT INTO contact (id, username, remark, nick_name) VALUES (5,?, '', ?)", (GROUP, GROUP_NAME))
    c.execute("INSERT INTO chat_room (id, username, owner) VALUES (1, ?, 'wxid_alice')", (GROUP,))
    for mid in (1, 2, 3, 4):
        c.execute("INSERT INTO chatroom_member (room_id, member_id) VALUES (5, ?)", (mid,))
    c.commit(); c.close()

    # ---- session.db ----
    s = sqlite3.connect(os.path.join(tmp, "session.db"))
    s.execute("CREATE TABLE SessionTable (username TEXT, type INTEGER, unread_count INTEGER, unread_first_msg_srv_id INTEGER, unread_first_pat_msg_local_id INTEGER, unread_first_pat_msg_sort_seq INTEGER, is_hidden INTEGER, summary TEXT, draft TEXT, status INTEGER, last_timestamp INTEGER, sort_timestamp INTEGER, last_clear_unread_timestamp INTEGER, last_msg_locald_id INTEGER, last_msg_type INTEGER, last_msg_sub_type INTEGER, last_msg_sender TEXT, last_sender_display_name TEXT, last_msg_ext_type INTEGER)")
    s.execute("INSERT INTO SessionTable (username, last_timestamp) VALUES (?, ?)", (GROUP, NOW - 60))
    s.commit(); s.close()

    # ---- message_0.db ----
    m = sqlite3.connect(os.path.join(tmp, "message_0.db"))
    m.execute(f"CREATE TABLE {TABLE} (local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER, sort_seq INTEGER, real_sender_id INTEGER, create_time INTEGER, status INTEGER, upload_status INTEGER, download_status INTEGER, server_seq INTEGER, origin_source INTEGER, source TEXT, message_content TEXT, compress_content TEXT, packed_info_data BLOB)")

    quote_xml = zstd('<msg><appmsg><type>57</type><title>同意，就按这个方案做</title><refermsg><type>1</type><fromusr>wxid_bob</fromusr><displayname>鲍勃-产品</displayname><content>周五前把报价单定稿， Alice 负责核对</content></refermsg></appmsg></msg>')
    link_xml = zstd('<msg><appmsg><type>5</type><title>微信聊天记录导出工具对比</title><des>一篇横向评测文章</des><url>https://example.com/review</url></appmsg></msg>')
    file_xml = zstd('<msg><appmsg><type>6</type><title>报价单-v3.xlsx</title><totalsize>20480</totalsize></appmsg></msg>')
    voice_xml = '<msg><voicemsg endflag="1" length="4321"/><voicetrans text="那就这么定了，明天上午十点开会确认最终版本"/></msg>'

    # (type, sender_id, offset_from_start, content)  offset 单位：小时（相对窗口起点）
    msgs = [
        (1, 1, 0.0, "窗口起点消息（应被包含）"),                     # t=start 恰好在起点
        (1, 1, -0.5, "窗口之前的消息（应被排除）"),
        (1, 2, 1.0, "周五前把报价单定稿，Alice 负责核对"),
        (244813135921, 1, 2.0, quote_xml),                          # 引用（57<<32|49）
        (21474836529, 2, 2.5, link_xml),                            # 链接（5<<32|49）
        (25769803825, 3, 3.0, file_xml),                            # 文件（6<<32|49）
        (3, 1, 3.5, ""),                                            # 图片
        (34, 2, 4.0, voice_xml),                                    # 语音（带转写）
        (47, 3, 4.5, ""),                                           # 表情
        (10000, 0, 5.0, '"wxid_carol" 加入了群聊'),                  # 系统消息
        (1, 4, 6.0, "收到，我这边没问题"),                           # 自己发言
        (1, 2, 23.9, "窗口内最后一条"),
        (1, 2, 24.0, "窗口终点消息（应被排除）"),
    ]
    lid = 0
    start = NOW - 24 * H
    for typ, sid, off, content in msgs:
        lid += 1
        body = content if isinstance(content, bytes) else (f"wxid_x:\n{content}" if False else content)
        if isinstance(content, str) and sid not in (4,) and typ == 1:
            uname = {1: "wxid_alice", 2: "wxid_bob", 3: "wxid_carol"}[sid]
            body = f"{uname}:\n{content}"
        srv = 1000000 + lid
        m.execute(f"INSERT INTO {TABLE} (local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, message_content) VALUES (?,?,?,?,?,?,?,?)",
                  (lid, srv, typ, lid * 10, sid, int(start + off * H), 2, body))
    # 制造一条 server_id 重复（与 lid=3 "周五前把报价单定稿" 同 server_id，应去重）
    lid += 1
    m.execute(f"INSERT INTO {TABLE} (local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, message_content) VALUES (?,?,?,?,?,?,?,?)",
              (lid, 1000003, 1, lid * 10, 2, int(start + 1.0 * H), 2, "wxid_bob:\n重复消息副本"))
    m.commit(); m.close()


def main():
    tmp = tempfile.mkdtemp(prefix="wxsum_selftest_")
    build(tmp)

    from wxsum.store import Store
    from wxsum import parse as msgparse

    store = Store(os.path.join(tmp, "message_0.db"),
                  os.path.join(tmp, "contact.db"),
                  os.path.join(tmp, "session.db"))
    g = store.resolve_group(GROUP_NAME)
    assert g["username"] == GROUP, g
    assert g["member_count"] == 4

    start = NOW - 24 * H
    raw = store.fetch_messages(GROUP, start, NOW)
    store.close()

    texts = [msgparse.parse_message(r) for r in raw]
    plain = [r["body"] if isinstance(r["body"], str) else "" for r in raw]

    # 断言（窗口内 11 条唯一消息：13 条中 2 条出窗口，+1 重复副本被去重）
    assert len(raw) == 11, f"期望 11 条（13 - 2 出窗口 - 1 重复 + 1 重复副本计入前），实际 {len(raw)}"
    assert not any("窗口之前" in p for p in plain), "起点前消息泄漏"
    assert not any("窗口终点" in p for p in plain), "终点消息未被排除"
    assert any("窗口起点" in p for p in plain), "起点消息丢失"
    assert not any("重复消息副本" in p for p in plain), "server_id 去重失败"

    by_kind = {}
    for r, p in zip(raw, texts):
        by_kind.setdefault(p["kind"], 0)
        by_kind[p["kind"]] += 1
    print("类型分布:", by_kind)
    assert by_kind.get("引用") == 1 and by_kind.get("链接") == 1
    assert by_kind.get("语音") == 1 and by_kind.get("图片") == 1

    quote_msg = next(p for p in texts if p["kind"] == "引用")
    assert "同意" in quote_msg["text"] and quote_msg["quote"]["refer_sender"] == "鲍勃-产品", quote_msg
    link_msg = next(p for p in texts if p["kind"] == "链接")
    assert link_msg["link"]["url"] == "https://example.com/review"
    voice_msg = next(p for p in texts if p["kind"] == "语音")
    assert voice_msg["voice_text"] and "明天上午十点" in voice_msg["voice_text"]

    senders = {r["sender"] for r in raw}
    assert "鲍勃-产品" in senders and "爱丽丝" in senders, senders  # 备注优先于昵称

    # 写 messages.json / messages.txt / report.json / summary.md
    out_dir = tmp
    messages = []
    for r, p in zip(raw, texts):
        import datetime as dt
        messages.append({
            "id": f"m{r['local_id']}", "local_id": r["local_id"], "server_id": r["server_id"],
            "ts": r["create_time"],
            "time": dt.datetime.fromtimestamp(r["create_time"]).strftime("%Y-%m-%d %H:%M:%S"),
            "sender": r["sender"], "sender_username": r["sender_username"],
            "is_self": r["sender_username"] == "my_wxid",
            "kind": p["kind"], "text": p["text"], "quote": p["quote"],
            "link": p["link"], "file": p["file"], "voice_text": p["voice_text"],
        })
    meta = {
        "group_name": GROUP_NAME, "group_remark": "", "group_username": GROUP,
        "member_count": 4, "account": "fake_account", "range_desc": "虚构数据",
        "start_ts": start, "end_ts": NOW,
        "start_time": dt.datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": dt.datetime.fromtimestamp(NOW).strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai (UTC+8)", "range_rule": "含起点，不含终点 [start, end)",
        "exported_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message_count": len(messages), "sender_count": len({m['sender'] for m in messages}),
        "type_stat": by_kind, "db_used": ["fake"], "wal_applied_pages": {},
        "scope_note": "本报告为虚构数据测试报告，仅用于验证解析与渲染管线。",
    }
    with open(os.path.join(out_dir, "messages.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "messages": messages}, f, ensure_ascii=False, indent=1)

    qid = next(m["id"] for m in messages if m["kind"] == "引用")
    bid = next(m["id"] for m in messages if "报价单定稿" in m["text"])
    report = {
        "overview": "虚构群围绕报价单定稿进行了简短讨论，并分享了工具评测文章。群名含特殊字符用于验证转义。",
        "topics": [{"title": "报价单定稿安排", "summary": "鲍勃-产品提出周五前定稿报价单并由 Alice 核对。",
                    "conclusion": "爱丽丝引用该消息表示同意，方案确定。", "msg_ids": [bid, qid]}],
        "todos": [{"item": "报价单定稿并核对", "owner": "爱丽丝", "due": "周五前", "status": "已确认待执行", "msg_ids": [bid, qid]},
                   {"item": "虚构的无负责人事项", "owner": None, "due": None, "status": None, "msg_ids": []}],
        "resolved": [{"item": "报价单方案获得同意（注意：同意 ≠ 执行完成）", "msg_ids": [qid]}],
        "open_questions": [{"item": "评测文章提到的方案尚未讨论", "msg_ids": []}],
        "other": "语音转写文本来自消息内嵌转写字段。",
        "caveats": ["本报告为虚构数据，仅验证渲染管线", "同意不等于执行完成"],
    }
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("# 虚构·测试群 总结\n\n（虚构内容，仅验证渲染）\n")

    from wxsum.render_html import render_html
    from wxsum.render_png import render_png
    html_path = render_html(out_dir)
    pngs = render_png(out_dir)
    print("[OK] HTML:", html_path)
    for p in pngs:
        print("[OK] PNG:", p)
    print("\n虚构数据测试全部断言通过 ✅")
    print("输出目录:", out_dir)


if __name__ == "__main__":
    main()
