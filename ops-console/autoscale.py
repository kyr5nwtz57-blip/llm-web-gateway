"""自动调权：按实测速度给各渠道自动升降权重。

数据源：new-api 的 logs 表（type=2 消费日志，含 channel_id + use_time 秒）。
写入：channels.weight + abilities.weight（new-api 的 SyncChannelCache 会在
CHANNEL_UPDATE_FREQUENCY 秒内自动从 DB 刷新内存缓存，无需重启）。

规则：
- 五家平权起步（基准权重 100）；
- 近窗口内平均耗时快于中位数的 → 升权，慢的 → 降权，夹在 [MIN_W, MAX_W]；
- 样本不足（< MIN_SAMPLES）的渠道保持原权重不动；
- 被禁用的渠道给最低权重（恢复后轻载起步）；
- 平滑（旧新各半）+ 最小变化阈值，防来回抖。

全程不调用任何模型接口。
"""
import asyncio
import json
import os
import sqlite3
import statistics
import time

DB_PATH = os.getenv("NEWAPI_DB", "/data/newapi/one-api.db")
STATE_FILE = os.getenv("AUTOSCALE_STATE", "/data/newapi/autoscale_state.json")
ENABLED = os.getenv("AUTOSCALE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
INTERVAL = int(os.getenv("AUTOSCALE_INTERVAL_SEC", "300"))
WINDOW_MIN = int(os.getenv("AUTOSCALE_WINDOW_MIN", "90"))
MIN_SAMPLES = int(os.getenv("AUTOSCALE_MIN_SAMPLES", "2"))
MIN_W = int(os.getenv("AUTOSCALE_MIN_WEIGHT", "20"))
MAX_W = int(os.getenv("AUTOSCALE_MAX_WEIGHT", "300"))
BASE = 100
SMOOTH_NEW = 0.5
MIN_DELTA = 10

_last_report = {"last_run": 0, "channels": [], "note": "未运行"}
_task = None


def _connect():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.execute("PRAGMA busy_timeout=20000")
    return con


def _free_chat_channels(con):
    rows = con.execute(
        """SELECT c.id, c.name, c.status, c.weight
           FROM channels c JOIN abilities a ON a.channel_id = c.id
           WHERE a.model = 'free-chat' AND a.'group' = 'default'
           GROUP BY c.id"""
    ).fetchall()
    return {
        r[0]: {"name": r[1], "status": r[2], "weight": r[3]} for r in rows
    }


def _window_stats(con, since):
    rows = con.execute(
        """SELECT channel_id, COUNT(*), AVG(use_time)
           FROM logs
           WHERE type = 2 AND model_name = 'free-chat' AND created_at >= ?
           GROUP BY channel_id""",
        (since,),
    ).fetchall()
    return {r[0]: {"n": r[1], "avg": float(r[2] or 0)} for r in rows}


def compute_targets(stats, channels):
    """纯函数：返回 {channel_id: 目标权重}。样本不足的不出现（保持原权重）。"""
    eligible = {
        cid: s
        for cid, s in stats.items()
        if cid in channels
        and channels[cid]["status"] == 1
        and s["n"] >= MIN_SAMPLES
        and s["avg"] > 0
    }
    if not eligible:
        return {}, None
    med = statistics.median(s["avg"] for s in eligible.values())
    targets = {}
    for cid, s in eligible.items():
        t = round(BASE * (med / s["avg"]))
        targets[cid] = max(MIN_W, min(MAX_W, t))
    return targets, med


def run_once(dry_run=False):
    global _last_report
    if not os.path.exists(DB_PATH):
        _last_report = {
            "last_run": int(time.time()),
            "channels": [],
            "note": f"数据库不存在: {DB_PATH}",
        }
        return _last_report

    since = int(time.time()) - WINDOW_MIN * 60
    con = _connect()
    try:
        channels = _free_chat_channels(con)
        stats = _window_stats(con, since)
        targets, med = compute_targets(stats, channels)

        report_channels = []
        changed = 0
        for cid in sorted(channels):
            meta = channels[cid]
            old = meta["weight"]
            s = stats.get(cid, {"n": 0, "avg": None})
            if meta["status"] != 1:
                new = MIN_W
            elif cid in targets:
                blended = round(old * SMOOTH_NEW + targets[cid] * (1 - SMOOTH_NEW))
                new = old if abs(blended - old) < MIN_DELTA else blended
            else:
                new = old
            is_changed = new != old
            report_channels.append(
                {
                    "id": cid,
                    "name": meta["name"],
                    "status": meta["status"],
                    "old": old,
                    "new": new,
                    "n": s["n"],
                    "avg": round(s["avg"], 1) if s["avg"] else None,
                }
            )
            if is_changed and not dry_run:
                con.execute("UPDATE channels SET weight=? WHERE id=?", (new, cid))
                con.execute(
                    "UPDATE abilities SET weight=? "
                    "WHERE model='free-chat' AND channel_id=?",
                    (new, cid),
                )
            if is_changed:
                changed += 1
                print(
                    f"[autoscale] ch{cid} {meta['name']}: {old} -> {new} "
                    f"(近{WINDOW_MIN}分 {s['n']}次, 均{s['avg']:.1f}s, 池中位"
                    f"{med:.1f}s)" if s["avg"] else
                    f"[autoscale] ch{cid} {meta['name']}: {old} -> {new}",
                    flush=True,
                )
        if not dry_run:
            con.commit()
    finally:
        con.close()

    _last_report = {
        "last_run": int(time.time()),
        "dry_run": dry_run,
        "window_min": WINDOW_MIN,
        "median_avg": round(med, 1) if med else None,
        "changed": changed,
        "channels": report_channels,
        "note": "",
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_last_report, f, ensure_ascii=False)
    except Exception:
        pass
    return _last_report


def report():
    r = dict(_last_report)
    r["enabled"] = ENABLED
    r["interval_sec"] = INTERVAL
    return r


async def _loop():
    await asyncio.sleep(20)
    while True:
        try:
            await asyncio.to_thread(run_once, False)
        except Exception as exc:
            print(f"[autoscale] 本轮失败(跳过): {exc}", flush=True)
        await asyncio.sleep(INTERVAL)


def start():
    global _task
    if not ENABLED:
        print("[autoscale] 已停用（AUTOSCALE_ENABLED=false）", flush=True)
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
        print(
            f"[autoscale] 已启动：每 {INTERVAL}s 一轮，窗口 {WINDOW_MIN} 分钟",
            flush=True,
        )
