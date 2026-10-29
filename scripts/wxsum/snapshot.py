"""快照管理：只读复制 -> 解密到临时目录 -> 用完即焚。

安全约束：
- 原始数据库全程只读；先复制 db+wal 到临时目录再解密，避免长时间占用活动文件
- 密钥仅存内存；临时明文库在退出时先覆写再删除
"""
from __future__ import annotations

import os
import shutil
import tempfile

from . import wcdb

# 本项目只需要这三类库即可完成任务（不多读）
NEEDED_DBS = ["message/message_0.db", "contact/contact.db", "session/session.db"]


class SnapshotError(RuntimeError):
    pass


def find_data_root() -> str | None:
    """定位 xwechat_files 根目录（读微信配置或扫描常见盘符）。"""
    appdata = os.environ.get("APPDATA", "")
    cfg_dir = os.path.join(appdata, "Tencent", "xwechat", "config")
    if os.path.isdir(cfg_dir):
        import glob
        for ini in glob.glob(os.path.join(cfg_dir, "*.ini")):
            for enc in ("utf-8", "gbk"):
                try:
                    with open(ini, "r", encoding=enc) as f:
                        content = f.read(2048).strip()
                    if content and os.path.isdir(os.path.join(content, "xwechat_files")):
                        return os.path.join(content, "xwechat_files")
                except (UnicodeDecodeError, OSError):
                    continue
    for drive in "cdefg":
        p = f"{drive}:/xwechat_files"
        if os.path.isdir(p):
            return p
    home = os.path.expanduser("~/Documents/xwechat_files")
    return home if os.path.isdir(home) else None


def list_accounts(data_root: str) -> list[dict]:
    """列出账号目录，并基于 all_users/login/*/key_info.db 的修改时间标记当前登录账号。"""
    accounts = []
    login_dir = os.path.join(data_root, "all_users", "login")
    login_mtimes: dict[str, float] = {}
    if os.path.isdir(login_dir):
        for name in os.listdir(login_dir):
            ki = os.path.join(login_dir, name, "key_info.db")
            if os.path.exists(ki):
                login_mtimes[name] = os.path.getmtime(ki)
    for name in sorted(os.listdir(data_root)):
        d = os.path.join(data_root, name)
        if not os.path.isdir(d) or name in ("all_users", "Backup"):
            continue
        if not os.path.isdir(os.path.join(d, "db_storage")):
            continue
        wxid = name.rsplit("_", 1)[0]  # 目录名形如 wxid_xxx_hash 或 微信号_hash
        accounts.append({
            "dir": d,
            "name": name,
            "wxid": wxid,
            "login_mtime": login_mtimes.get(wxid),
        })
    if accounts and any(a["login_mtime"] for a in accounts):
        current = max((a for a in accounts if a["login_mtime"]),
                      key=lambda a: a["login_mtime"])
        for a in accounts:
            a["is_current"] = (a is current)
    return accounts


class Snapshot:
    """解密快照上下文管理器。"""

    def __init__(self, account_dir: str, log=print):
        self.account_dir = account_dir
        self.db_storage = os.path.join(account_dir, "db_storage")
        self.log = log
        self.tmpdir: str | None = None
        self.paths: dict[str, str] = {}   # rel -> 明文快照路径
        self.stats: dict[str, dict] = {}

    def __enter__(self) -> "Snapshot":
        self.log("[*] 提取数据库密钥（只读扫描微信进程内存）...")
        keys = wcdb.extract_keys_in_memory(self.db_storage, log=self.log)
        self.tmpdir = tempfile.mkdtemp(prefix="wxsum_", dir=_secure_tmp_root())
        try:
            for rel in NEEDED_DBS:
                src = os.path.join(self.db_storage, rel.replace("/", os.sep))
                if not os.path.exists(src):
                    raise SnapshotError(f"缺少数据库文件: {src}")
                key_hex = keys.get(rel)
                if not key_hex:
                    raise SnapshotError(f"未获得 {rel} 的密钥")
                enc_key = bytes.fromhex(key_hex)
                # 复制 db+wal 一致性快照：若复制期间微信做了 checkpoint，
                # WAL 头会被重写导致首帧 salt 不匹配 —— 检测到就重试
                copy_db = os.path.join(self.tmpdir, "copy_" + rel.replace("/", "_"))
                copy_wal = copy_db + "-wal"
                wal_src = src + "-wal"
                has_wal = False
                for attempt in range(2):
                    shutil.copyfile(src, copy_db)
                    has_wal = os.path.exists(wal_src) and os.path.getsize(wal_src) > wcdb.WAL_HDR_SZ
                    if has_wal:
                        shutil.copyfile(wal_src, copy_wal)
                        if _wal_self_consistent(copy_wal):
                            break
                        if attempt == 0:
                            self.log(f"  [INFO] {rel} WAL 首帧 salt 与头部不一致，重复制一次以排除撕裂拷贝")
                    else:
                        break
                out = os.path.join(self.tmpdir, "plain_" + rel.replace("/", "_"))
                st = wcdb.decrypt_database_with_wal(
                    copy_db, copy_wal if has_wal else None, enc_key, out, log=self.log)
                # 加密副本立即销毁
                _shred(copy_db)
                if has_wal:
                    _shred(copy_wal)
                self.paths[rel] = out
                self.stats[rel] = st
                self.log(f"  [OK] {rel}: {st['pages']} 页, WAL 合并 {st['wal_pages_applied']} 页")
        except Exception:
            self._cleanup()
            raise
        finally:
            keys.clear()  # 密钥使命结束
        return self

    def db_path(self, rel: str) -> str:
        return self.paths[rel]

    def __exit__(self, *exc):
        self._cleanup()
        return False

    def _cleanup(self):
        if self.tmpdir and os.path.isdir(self.tmpdir):
            for name in os.listdir(self.tmpdir):
                _shred(os.path.join(self.tmpdir, name))
            try:
                os.rmdir(self.tmpdir)
            except OSError:
                shutil.rmtree(self.tmpdir, ignore_errors=True)
            self.log("[*] 临时明文数据库已清理")
        self.tmpdir = None


def _wal_self_consistent(wal_path: str) -> bool:
    """WAL 首个帧的 salt 与头部一致（说明复制期间未发生 checkpoint 轮转）。"""
    import struct
    with open(wal_path, "rb") as f:
        hdr = f.read(wcdb.WAL_HDR_SZ)
        f1 = f.read(wcdb.WAL_FRAME_HDR_SZ)
    if len(hdr) < wcdb.WAL_HDR_SZ or len(f1) < wcdb.WAL_FRAME_HDR_SZ:
        return True  # 空 WAL，无需合并
    _m, _v, pgsz, _c, s1, s2, _c1, _c2 = struct.unpack(">8I", hdr)
    if pgsz == 0 or pgsz > 65536:
        return True
    _pgno, _db, fs1, fs2, _f1, _f2 = struct.unpack(">6I", f1)
    return (fs1, fs2) == (s1, s2)


def _secure_tmp_root() -> str:
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".secure_tmp")
    os.makedirs(root, exist_ok=True)
    return root


def _shred(path: str):
    """覆写后删除临时文件（尽力而为的清理）。"""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            chunk = b"\x00" * 65536
            left = size
            while left > 0:
                n = min(left, 65536)
                f.write(chunk[:n])
                left -= n
            f.flush()
            os.fsync(f.fileno())
        os.remove(path)
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass
