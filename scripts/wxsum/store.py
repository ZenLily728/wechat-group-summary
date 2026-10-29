"""明文快照上的查询层：群聊解析、消息窗口读取、发送人映射。"""
from __future__ import annotations

import hashlib
import re
import sqlite3


class GroupResolutionError(RuntimeError):
    def __init__(self, msg, candidates=None):
        super().__init__(msg)
        self.candidates = candidates or []


def msg_table_name(talker_username: str) -> str:
    return "Msg_" + hashlib.md5(talker_username.encode()).hexdigest()


class Store:
    def __init__(self, message_db: str, contact_db: str, session_db: str):
        self.msg = sqlite3.connect(message_db)
        self.contact = sqlite3.connect(contact_db)
        self.session = sqlite3.connect(session_db)
        self.msg.execute("PRAGMA query_only=ON")
        self.contact.execute("PRAGMA query_only=ON")
        self.session.execute("PRAGMA query_only=ON")

    def close(self):
        for c in (self.msg, self.contact, self.session):
            c.close()

    # ------------------------------------------------------------------
    def list_groups(self) -> list[dict]:
        """所有群聊：username、群名、备注、最近活跃时间。"""
        rows = self.contact.execute(
            "SELECT username, nick_name, remark FROM contact "
            "WHERE username LIKE '%@chatroom' ORDER BY id").fetchall()
        last_ts = {u: t for u, t in self.session.execute(
            "SELECT username, last_timestamp FROM SessionTable")}
        out = []
        for username, nick, remark in rows:
            out.append({
                "username": username,
                "name": nick or "",
                "remark": remark or "",
                "last_ts": last_ts.get(username),
            })
        return out

    def resolve_group(self, name: str) -> dict:
        """按完整群名（群名或备注精确匹配）定位群聊。同名时返回候选列表报错。"""
        groups = self.list_groups()
        exact = [g for g in groups if g["name"] == name or g["remark"] == name]
        if not exact:
            raise GroupResolutionError(
                f"未找到完整群名/备注为「{name}」的群聊", candidates=groups)
        if len(exact) > 1:
            raise GroupResolutionError(
                f"存在 {len(exact)} 个同名群聊「{name}」，需要进一步消歧",
                candidates=exact)
        g = exact[0]
        # 附加稳定元信息（群主、成员数）
        row = self.contact.execute(
            "SELECT owner FROM chat_room WHERE username=?", (g["username"],)).fetchone()
        g["owner"] = row[0] if row else None
        rid = self._room_int_id(g["username"])
        if rid is not None:
            g["member_count"] = self.contact.execute(
                "SELECT COUNT(*) FROM chatroom_member WHERE room_id=?", (rid,)).fetchone()[0]
        return g

    def _room_int_id(self, room_username: str) -> int | None:
        row = self.contact.execute(
            "SELECT rowid FROM name2id WHERE username=?", (room_username,)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    def sender_display_map(self, usernames: set[str]) -> dict[str, str]:
        """username -> 显示名（优先备注，其次昵称）。"""
        out = {}
        for u in usernames:
            row = self.contact.execute(
                "SELECT remark, nick_name FROM contact WHERE username=?", (u,)).fetchone()
            if row:
                out[u] = row[0] or row[1] or u
            else:
                out[u] = u
        return out

    def id_to_username(self, ids: set[int]) -> dict[int, str]:
        out = {}
        for i in ids:
            row = self.contact.execute(
                "SELECT username FROM name2id WHERE rowid=?", (i,)).fetchone()
            if row:
                out[i] = row[0]
        return out

    # ------------------------------------------------------------------
    def fetch_messages(self, group_username: str, start_ts: int, end_ts: int) -> list[dict]:
        """读取指定群 [start_ts, end_ts) 窗口内的全部消息（含去重、排序）。"""
        table = msg_table_name(group_username)
        exists = self.msg.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        if not exists:
            return []
        rows = self.msg.execute(
            f"SELECT local_id, server_id, local_type, sort_seq, real_sender_id, "
            f"create_time, status, message_content, compress_content "
            f"FROM {table} WHERE create_time >= ? AND create_time < ? "
            f"ORDER BY create_time, sort_seq, local_id", (start_ts, end_ts)).fetchall()

        sender_ids = {r[4] for r in rows if r[4]}
        id_map = self.id_to_username(sender_ids)
        usernames = set(id_map.values())
        # 群消息文本前缀里的 wxid 也纳入映射
        for r in rows:
            body = r[7]
            if isinstance(body, bytes):
                continue  # 二进制（zstd）内容不含明文发送者前缀
            m = _SENDER_PREFIX.match(body or "")
            if m:
                usernames.add(m.group(1))
        display = self.sender_display_map(usernames)

        seen_server, out = set(), []
        for (local_id, server_id, local_type, sort_seq, real_sender_id,
             create_time, status, content, compress) in rows:
            # 去重：server_id 非零时按 server_id；否则按 local_id
            dedup_key = ("srv", server_id) if server_id else ("loc", local_id)
            if dedup_key in seen_server:
                continue
            seen_server.add(dedup_key)
            sender_username = id_map.get(real_sender_id, "")
            body = content if isinstance(content, (str, bytes)) else (content or "")
            if isinstance(body, str):
                m = _SENDER_PREFIX.match(body)
                if m:
                    if not sender_username:
                        sender_username = m.group(1)
                    body = body[m.end():]
            out.append({
                "local_id": local_id,
                "server_id": server_id,
                "local_type": local_type,
                "sort_seq": sort_seq,
                "create_time": create_time,
                "sender_username": sender_username,
                "sender": display.get(sender_username, sender_username or "未知"),
                "is_self": False,  # 由调用方根据本人 wxid 填充
                "body": body,
                "compress_content": compress,
            })
        return out


_SENDER_PREFIX = re.compile(r"^([A-Za-z0-9_\-]+(?:@[A-Za-z0-9.]+)?):\n")
