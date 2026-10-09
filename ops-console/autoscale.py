"""自动调权：按实测速度和失败情况给各渠道自动升降权重。

数据源（全部本地，零模型调用）：
- new-api 的 logs 表：近窗口每渠道成功请求的 use_time（秒）与次数；
- new-api 容器日志：窗口内每渠道失败请求数（channel error 行，按请求 id 去重）。

写入：channels.weight + abilities.weight（渠道缓存约 60s 自动重载，无需重启）。

规则：
- 五家平权起步（基准 100）；
- 快于池中位数的升权、慢的降权：目标 = 100 × 中位耗时 / 该家耗时，夹 [MIN_W, MAX_W]；
- 窗口内失败 ≥ FAIL_MIN 次：按成功率折算目标；全失败则给最低权重；
- 样本不足（< MIN_SAMPLES）且无失败的不动；被禁用的给最低权重；
- 平滑（旧新各半）+ 最小变化阈值，防来回抖；
- 可暂停（面板开关，持久化到 state 文件）。
"""
import asyncio
import json
import os
import re
import sqlite3
import statistics
import time
from datetime import datetime, timezone

DB_PATH = os.getenv("NEWAPI_DB", "/data/newapi/one-api.db")
STATE_FILE = os.getenv("AUTOSCALE_STATE", "/data/newapi/autoscale_state.json")
NEWAPI_CONTAINER = os.getenv("NEWAPI_CONTAINER", "new-api")
ENABLED_ENV = os.getenv("AUTOSCALE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
INTERVAL = int(os.getenv("AUTOSCALE_INTERVAL_SEC", "300"))
WINDOW_MIN = int(os.getenv("AUTOSCALE_WINDOW_MIN", "90"))
MIN_SAMPLES = int(os.getenv("AUTOSCALE_MIN_SAMPLES", "2"))
FAIL_MIN = int(os.getenv("AUTOSCALE_FAIL_MIN", "2"))
MIN_W = int(os.getenv("AUTOSCALE_MIN_WEIGHT", "20"))
MAX_W = int(os.getenv("AUTOSCALE_MAX_WEIGHT", "300"))
BASE = 100
SMOOTH_NEW = 0.5
MIN_DELTA = 10
HISTORY_MAX = 50

_REQ_ID_RE = re.compile(r"\| ([0-9a-zA-Z]{25,}) \|")
_CH_ERR_RE = re.compile(r"channel error \(channel #(\d+)")

_last_report = {"last_run": 0, "channels": [], "note": "未运行"}
_task = None
_next_run = 0
_enabled_state = None  # None=跟随 env；True/False=面板开关持久值
_history = []


def _load_state():
    global _enabled_state, _history, _last_report
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("enabled"), bool):
            _enabled_state = d["enabled"]
        if isinstance(d.get("history"), list):
            _history = d["history"][-HISTORY_MAX:]
        if isinstance(d.get("last_report"), dict) and d["last_report"].get("channels"):
            _last_report = d["last_report"]
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[autoscale] state 读取失败(忽略): {exc}", flush=True)


def _save_state():
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "enabled": _enabled_state,
                    "history": _history[-HISTORY_MAX:],
                    "last_report": _last_report,
                },
                f,
                ensure_ascii=False,
            )
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        print(f"[autoscale] state 保存失败: {exc}", flush=True)


def enabled():
    return ENABLED_ENV if _enabled_state is None else _enabled_state


def set_enabled(value: bool):
    global _enabled_state
    _enabled_state = bool(value)
    _save_state()
    print(f"[autoscale] 面板{'启用' if value else '暂停'}自动调权", flush=True)
    return enabled()


def _connect():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.execute("PRAGMA busy_timeout=20000")
    return con


def _free_chat_channels(con):
    rows = con.execute(
        """SELECT c.id, c.name, c.status, c.weight
           FROM channels c JOIN abilities a ON a.channel_id = c.id
           WHERE a.model = 'free-chat' AND a.'group' = 'default' AND a.priority = 0
           GROUP BY c.id"""
    ).fetchall()
    return {r[0]: {"name": r[1], "status": r[2], "weight": r[3]} for r in rows}


def _window_stats(con, since):
    # 只统计"有产出"的请求（completion_tokens > 0）作为耗时样本——
    # 0 token 的假成功（空回复）绝不能算"快"，否则会把坏渠道误升权。
    rows = con.execute(
        """SELECT channel_id, COUNT(*), AVG(use_time)
           FROM logs
           WHERE type = 2 AND model_name = 'free-chat' AND created_at >= ?
             AND completion_tokens > 0
           GROUP BY channel_id""",
        (since,),
    ).fetchall()
    return {r[0]: {"n": r[1], "avg": float(r[2] or 0)} for r in rows}


def _silent_fail_counts(con, since):
    """窗口内"0 token 的假成功"按失败计（客户端主动断开 client_gone 的除外）。"""
    rows = con.execute(
        """SELECT channel_id, COUNT(*)
           FROM logs
           WHERE type = 2 AND model_name = 'free-chat' AND created_at >= ?
             AND completion_tokens = 0
             AND (other IS NULL OR other NOT LIKE '%client_gone%')
           GROUP BY channel_id""",
        (since,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _fail_counts(since):
    """读 new-api 容器日志，统计窗口内每渠道「失败的请求」数（请求 id 去重）。

    一行格式（docker timestamps 前缀）：
    2026-10-09T07:35:13.123Z [ERR] ... | <reqid> | channel error (channel #12, ...)
    """
    try:
        import docker
        client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        try:
            raw = client.containers.get(NEWAPI_CONTAINER).logs(
                timestamps=True, since=int(since)
            )
        finally:
            client.close()
    except Exception as exc:
        print(f"[autoscale] 读 new-api 日志失败(本轮失败数按 0): {exc}", flush=True)
        return {}
    seen = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        if "channel error" not in line:
            continue
        head, _, rest = line.partition(" ")
        try:
            ts = datetime.fromisoformat(head.rstrip("Z")).replace(
                tzinfo=timezone.utc
            ).timestamp()
        except ValueError:
            continue
        if ts < since:
            continue
        m = _CH_ERR_RE.search(rest)
        rid = _REQ_ID_RE.search(rest)
        if not m:
            continue
        cid = int(m.group(1))
        key = (cid, rid.group(1) if rid else line)
        seen[key] = True
    out = {}
    for cid, _ in seen:
        out[cid] = out.get(cid, 0) + 1
    return out


def compute_targets(stats, fails, channels):
    """纯函数：返回 ({channel_id: 目标权重}, 中位耗时)。样本不足的不出现。"""
    eligible = {
        cid: s
        for cid, s in stats.items()
        if cid in channels
        and channels[cid]["status"] == 1
        and s["n"] >= MIN_SAMPLES
        and s["avg"] > 0
    }
    targets = {}
    med = None
    if eligible:
        med = statistics.median(s["avg"] for s in eligible.values())
        for cid, s in eligible.items():
            t = round(BASE * (med / s["avg"]))
            f = fails.get(cid, 0)
            if f >= FAIL_MIN:
                t = round(t * s["n"] / (s["n"] + f))
            targets[cid] = max(MIN_W, min(MAX_W, t))
    for cid, meta in channels.items():
        if meta["status"] != 1:
            continue
        if cid not in targets and fails.get(cid, 0) >= FAIL_MIN:
            targets[cid] = MIN_W
    return targets, med


def run_once(dry_run=False, reason="auto"):
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
        fails = _fail_counts(since)
        for cid, n in _silent_fail_counts(con, since).items():
            fails[cid] = fails.get(cid, 0) + n
        targets, med = compute_targets(stats, fails, channels)

        report_channels = []
        changed = 0
        for cid in sorted(channels):
            meta = channels[cid]
            old = meta["weight"]
            s = stats.get(cid, {"n": 0, "avg": None})
            f = fails.get(cid, 0)
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
                    "fails": f,
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
                _history.append(
                    {
                        "ts": int(time.time()),
                        "id": cid,
                        "name": meta["name"],
                        "old": old,
                        "new": new,
                        "why": reason,
                    }
                )
                del _history[:-HISTORY_MAX]
                print(
                    f"[autoscale] ch{cid} {meta['name']}: {old} -> {new} "
                    f"(近{WINDOW_MIN}分 成功{s['n']}次 失败{f}次"
                    + (f", 均{s['avg']:.1f}s, 池中位{med:.1f}s" if s["avg"] and med else "")
                    + ")",
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
    if not dry_run:
        _save_state()
    return _last_report


def report():
    r = dict(_last_report)
    r["enabled"] = enabled()
    r["interval_sec"] = INTERVAL
    r["next_run"] = _next_run
    r["history"] = list(reversed(_history[-20:]))
    return r


def enable_channel(cid: int):
    """把被禁用（自动/手动）的 free-chat 渠道重新启用（不动权重）。"""
    con = _connect()
    try:
        ok = con.execute(
            """SELECT 1 FROM abilities
               WHERE model='free-chat' AND \"group\"='default' AND channel_id=?""",
            (cid,),
        ).fetchone()
        if not ok:
            return False
        con.execute(
            "UPDATE channels SET status=1 WHERE id=? AND status!=1", (cid,)
        )
        con.execute(
            "UPDATE abilities SET enabled=1 "
            "WHERE model='free-chat' AND channel_id=?",
            (cid,),
        )
        con.commit()
        print(f"[autoscale] ch{cid} 已手动重新启用（约 60s 生效）", flush=True)
        return True
    finally:
        con.close()


async def _loop():
    global _next_run
    await asyncio.sleep(20)
    while True:
        try:
            if enabled():
                await asyncio.to_thread(run_once, False, "auto")
        except Exception as exc:
            print(f"[autoscale] 本轮失败(跳过): {exc}", flush=True)
        _next_run = int(time.time()) + INTERVAL
        await asyncio.sleep(INTERVAL)


def start():
    global _task
    _load_state()
    if not ENABLED_ENV and _enabled_state is None:
        print("[autoscale] 环境变量停用（AUTOSCALE_ENABLED=false）", flush=True)
    _task = asyncio.create_task(_loop())
    print(
        f"[autoscale] 已启动：每 {INTERVAL}s 一轮，窗口 {WINDOW_MIN} 分钟，"
        f"当前{'启用' if enabled() else '暂停'}",
        flush=True,
    )
