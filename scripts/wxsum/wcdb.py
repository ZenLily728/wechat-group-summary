"""WCDB/SQLCipher4 解密层：密钥提取（仅内存）、逐页解密、WAL 离线合并。

复用 vendor/wcdb-key-tool（TANGandXUE, MIT, 固定提交 79f1b5b）中已验证的
Config.Cipher 只读扫描、HMAC 校验与 AES-CBC(CNG) 原语；本模块额外实现：
- 密钥只保存在内存，不落盘
- SQLCipher4 加密的 WAL 帧校验（密文校验和链）与"离线 checkpoint"合并
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import importlib.util
import os
import struct
import sys

_VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "vendor", "wcdb-key-tool", "wcdb_key_tool_windows.py")


def _load_vendor():
    spec = importlib.util.spec_from_file_location("wcdb_vendor", _VENDOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wcdb = _load_vendor()

PAGE_SZ = wcdb.PAGE_SZ       # 4096
SALT_SZ = wcdb.SALT_SZ       # 16
IV_SZ = wcdb.IV_SZ           # 16
RESERVE_SZ = wcdb.RESERVE_SZ  # 80
HMAC_SZ = wcdb.HMAC_SZ       # 64


class KeyExtractionError(RuntimeError):
    pass


def extract_keys_in_memory(db_dir: str, log=print) -> dict[str, str]:
    """从运行中的微信进程提取所有数据库密钥。返回 {rel_path: enc_key_hex}。

    密钥只存在于返回的 dict 与本函数栈中，不写入任何文件。
    """
    db_files, salt_to_dbs = wcdb.collect_db_files(db_dir)
    if not db_files:
        raise KeyExtractionError(f"在 {db_dir} 未找到 .db 文件")
    log(f"发现 {len(db_files)} 个数据库，{len(salt_to_dbs)} 个独立 salt")

    import ctypes
    kernel32 = ctypes.windll.kernel32
    MEM_COMMIT = 0x1000
    READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}

    class MBI(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
            ("AllocationProtect", ctypes.c_uint32), ("_pad1", ctypes.c_uint32),
            ("RegionSize", ctypes.c_uint64), ("State", ctypes.c_uint32),
            ("Protect", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("_pad2", ctypes.c_uint32),
        ]

    def read_mem(h, addr, sz):
        buf = ctypes.create_string_buffer(sz)
        n = ctypes.c_size_t(0)
        if kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, sz, ctypes.byref(n)):
            return buf.raw[: n.value]
        return None

    def enum_regions(h):
        regs, addr = [], 0
        mbi = MBI()
        while addr < 0x7FFFFFFFFFFF:
            if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                break
            if mbi.State == MEM_COMMIT and mbi.Protect in READABLE and 0 < mbi.RegionSize < 500 * 1024 * 1024:
                regs.append((mbi.BaseAddress, mbi.RegionSize))
            nxt = mbi.BaseAddress + mbi.RegionSize
            if nxt <= addr:
                break
            addr = nxt
        return regs

    pids = wcdb._get_pids_windows()
    key_map: dict[str, str] = {}
    remaining = set(salt_to_dbs.keys())
    for pid, _mem in pids:
        h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)  # VM_READ | QUERY_INFO
        if not h:
            log(f"[WARN] 无法打开进程 PID={pid}（可尝试管理员身份运行）")
            continue
        try:
            regions = enum_regions(h)
            wcdb._scan_windows_v411_config_cipher(
                pid, regions,
                lambda b, s, _h=h: read_mem(_h, b, s),
                lambda a, s, _h=h: read_mem(_h, a, s),
                db_files, salt_to_dbs, key_map, remaining, log,
            )
        finally:
            kernel32.CloseHandle(h)
        if not remaining:
            break

    if not key_map:
        raise KeyExtractionError("未能提取到任何密钥（确认微信已登录且版本兼容）")

    result: dict[str, str] = {}
    for rel, _path, _sz, salt_hex, _p1 in db_files:
        if salt_hex in key_map:
            result[rel.replace("\\", "/")] = key_map[salt_hex]
    log(f"密钥提取完成: {len(result)}/{len(db_files)} 个数据库")
    return result


# ---------------------------------------------------------------------------
# 页面级解密与 HMAC 校验
# ---------------------------------------------------------------------------

def _mac_key(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=32)


def verify_page_hmac(enc_key: bytes, salt: bytes, page: bytes, pgno: int) -> bool:
    """校验任意页（含 WAL 帧页）的 HMAC-SHA512。"""
    if len(page) < PAGE_SZ:
        return False
    start = SALT_SZ if pgno == 1 else 0
    hm = hmac_mod.new(_mac_key(enc_key, salt), page[start: PAGE_SZ - RESERVE_SZ + IV_SZ],
                      hashlib.sha512)
    hm.update(struct.pack("<I", pgno))
    return hm.digest() == page[PAGE_SZ - HMAC_SZ: PAGE_SZ]


def decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    return wcdb._decrypt_page(enc_key, page, pgno)


# ---------------------------------------------------------------------------
# WAL：密文校验和链验证 + 离线 checkpoint 合并
# ---------------------------------------------------------------------------

WAL_HDR_SZ = 32
WAL_FRAME_HDR_SZ = 24


def _wal_checksum(data: bytes, s0: int, s1: int, big_endian: bool) -> tuple[int, int]:
    fmt = ">%dI" % (len(data) // 4) if big_endian else "<%dI" % (len(data) // 4)
    words = struct.unpack(fmt, data)
    for i in range(0, len(words) - 1, 2):
        s0 = (s0 + words[i] + s1) & 0xFFFFFFFF
        s1 = (s1 + words[i + 1] + s0) & 0xFFFFFFFF
    return s0, s1


def recover_wal_frames(wal_path: str, salt16: bytes, enc_key: bytes,
                       log=print) -> dict[int, bytes]:
    """读取加密 WAL，验证密文校验和链，解密已提交帧。

    返回 {pgno: 解密后的页内容(明文页)}。无效/未提交/校验失败的尾部帧被丢弃。
    """
    size = os.path.getsize(wal_path)
    if size < WAL_HDR_SZ:
        return {}
    with open(wal_path, "rb") as f:
        wal = f.read()
    magic, _ver, pgsz, _ckpt, s1, s2, c1, c2 = struct.unpack(">8I", wal[:WAL_HDR_SZ])
    if magic & 0xFFFFFFFE != 0x377F0682:
        log(f"[WARN] WAL magic 异常: {magic:#x}，跳过 WAL")
        return {}
    if pgsz != PAGE_SZ:
        log(f"[WARN] WAL 页大小 {pgsz} 与预期 {PAGE_SZ} 不符，跳过 WAL")
        return {}
    big_endian = bool(magic & 1)

    # 头部校验和（覆盖前 24 字节）
    c1, c2 = _wal_checksum(wal[:24], 0, 0, big_endian)
    hc1, hc2 = struct.unpack(">2I", wal[24:32])
    if (c1, c2) != (hc1, hc2):
        log("[WARN] WAL 头部校验和不匹配，跳过 WAL")
        return {}

    frames: dict[int, bytes] = {}
    pending: dict[int, bytes] = {}
    off = WAL_HDR_SZ
    frame_sz = WAL_FRAME_HDR_SZ + PAGE_SZ
    n_frames = (len(wal) - WAL_HDR_SZ) // frame_sz
    applied_txns = 0
    for i in range(n_frames):
        fh = wal[off: off + WAL_FRAME_HDR_SZ]
        pgno, dbsize, fs1, fs2, fc1, fc2 = struct.unpack(">6I", fh)
        page = wal[off + WAL_FRAME_HDR_SZ: off + frame_sz]
        off += frame_sz
        if fs1 != s1 or fs2 != s2:
            if i == 0:
                log("[INFO] WAL 无当前代帧（近期已 checkpoint，主库即为最新）")
            else:
                log(f"[INFO] WAL 帧 {i + 1} salt 变化（旧代帧），停止合并")
            break
        c1, c2 = _wal_checksum(fh[:8], c1, c2, big_endian)
        c1, c2 = _wal_checksum(page, c1, c2, big_endian)
        if (c1, c2) != (fc1, fc2):
            log(f"[INFO] WAL 帧 {i + 1} 校验和不匹配（可能为撕裂写入），丢弃后续帧")
            break
        if not verify_page_hmac(enc_key, salt16, page, pgno):
            log(f"[INFO] WAL 帧 {i + 1} (pgno={pgno}) HMAC 校验失败，丢弃后续帧")
            break
        pending[pgno] = decrypt_page(enc_key, page, pgno)
        if dbsize:  # commit 帧
            frames.update(pending)
            pending.clear()
            applied_txns += 1
            frames[-1] = dbsize  # 哨兵：最后一个 commit 的库大小（页数）
    if applied_txns:
        log(f"WAL 合并: {applied_txns} 个已提交事务, {len([k for k in frames if k > 0])} 页")
    return frames


def decrypt_database_with_wal(db_path: str, wal_path: str | None, enc_key: bytes,
                              out_path: str, log=print) -> dict:
    """解密主库并按"离线 checkpoint"语义合并 WAL，输出一致性明文快照。"""
    file_size = os.path.getsize(db_path)
    total_pages = file_size // PAGE_SZ
    with open(db_path, "rb") as f:
        raw = f.read()
    page1 = raw[:PAGE_SZ]
    salt16 = page1[:SALT_SZ]
    if not wcdb.verify_enc_key(enc_key, page1):
        raise KeyExtractionError(f"{os.path.basename(db_path)} 第一页 HMAC 校验失败")

    pages: dict[int, bytes] = {}
    for pgno in range(1, total_pages + 1):
        pages[pgno] = decrypt_page(enc_key, raw[(pgno - 1) * PAGE_SZ: pgno * PAGE_SZ], pgno)

    wal_pages = 0
    final_dbsize = total_pages
    if wal_path and os.path.exists(wal_path):
        frames = recover_wal_frames(wal_path, salt16, enc_key, log=log)
        final_dbsize = frames.pop(-1, total_pages) if frames else total_pages
        for pgno, content in frames.items():
            pages[pgno] = content
        wal_pages = len(frames)

    # 尊重最后一个 commit 帧声明的库大小（可能小于主库页数 = 截断）
    for pgno in list(pages):
        if pgno > final_dbsize:
            del pages[pgno]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        for pgno in range(1, final_dbsize + 1):
            f.write(pages.get(pgno, b"\x00" * PAGE_SZ))
    return {"pages": final_dbsize, "wal_pages_applied": wal_pages}
