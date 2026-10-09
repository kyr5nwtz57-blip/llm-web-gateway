"""运维后台任务：每日自动备份 new-api 数据库 + 磁盘/备份状态。

备份用 sqlite3 在线备份 API（WAL 模式下安全），落盘在 new-api 数据目录的
backups/ 子目录；只自动裁剪自己命名规则（one-api-YYYYMMDD-HHMM.db）的旧件，
绝不触碰其他文件。全程零模型调用。
"""
import asyncio
import os
import re
import shutil
import sqlite3
import time

DB_PATH = os.getenv("NEWAPI_DB", "/data/newapi/one-api.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "/data/newapi/backups")
KEEP = int(os.getenv("BACKUP_KEEP", "10"))
INTERVAL = int(os.getenv("BACKUP_INTERVAL_SEC", str(24 * 3600)))
_NAME_RE = re.compile(r"^one-api-\d{8}-\d{4,6}\.db\Z")


def _auto_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    names = sorted(f for f in os.listdir(BACKUP_DIR) if _NAME_RE.match(f))
    return names


def run_backup():
    if not os.path.exists(DB_PATH):
        return {"ok": False, "error": f"数据库不存在: {DB_PATH}"}
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = "one-api-" + time.strftime("%Y%m%d-%H%M%S") + ".db"
    dest = os.path.join(BACKUP_DIR, name)
    src = sqlite3.connect(DB_PATH, timeout=20)
    try:
        dst = sqlite3.connect(dest, timeout=20)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    names = _auto_backups()
    pruned = []
    while len(names) > KEEP:
        victim = names.pop(0)
        try:
            os.remove(os.path.join(BACKUP_DIR, victim))
            pruned.append(victim)
        except OSError:
            break
    print(
        f"[backup] 已备份 {name} ({os.path.getsize(dest)//1024} KB), "
        f"现存 {len(names)} 份, 裁剪 {pruned or '无'}",
        flush=True,
    )
    return {
        "ok": True,
        "file": name,
        "size": os.path.getsize(dest),
        "pruned": pruned,
        "kept": len(names),
    }


def list_backups():
    items = []
    for n in reversed(_auto_backups()):
        p = os.path.join(BACKUP_DIR, n)
        try:
            st = os.stat(p)
            items.append({"name": n, "size": st.st_size, "mtime": int(st.st_mtime)})
        except OSError:
            continue
    return {"dir": BACKUP_DIR, "items": items, "keep": KEEP}


def disk_info():
    out = {}
    base = os.path.dirname(DB_PATH)
    probe = base if os.path.isdir(base) else "/"
    try:
        u = shutil.disk_usage(probe)
        out["disk_total_gb"] = round(u.total / 2**30, 1)
        out["disk_free_gb"] = round(u.free / 2**30, 1)
    except Exception as exc:
        out["disk_error"] = str(exc)
    names = _auto_backups()
    out["backup_count"] = len(names)
    out["last_backup"] = (
        int(os.stat(os.path.join(BACKUP_DIR, names[-1])).st_mtime) if names else None
    )
    return out


async def backup_loop():
    await asyncio.sleep(90)
    while True:
        try:
            await asyncio.to_thread(run_backup)
        except Exception as exc:
            print(f"[backup] 本轮失败(跳过): {exc}", flush=True)
        await asyncio.sleep(INTERVAL)
