import asyncio
import base64
import calendar
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from pathlib import Path

import docker
import httpx
from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import autoscale
import maintenance
from page import PAGE

ADMIN_PASSWORD = os.getenv("OPS_ADMIN_PASSWORD", "")
COOKIE_NAME = "ops_session"
SESSION_TOKEN = (
    hmac.new(
        ADMIN_PASSWORD.encode("utf-8"), b"ops-console-session", hashlib.sha256
    ).hexdigest()
    if ADMIN_PASSWORD
    else ""
)


def _authed(request: Request) -> bool:
    if not ADMIN_PASSWORD:
        return False
    if request.headers.get("authorization", "") == f"Bearer {ADMIN_PASSWORD}":
        return True
    return request.cookies.get(COOKIE_NAME) == SESSION_TOKEN

GLM = Path("/data/glm/glm_refresh_token.txt")
DOUBAO = Path("/data/doubao/doubao_sessionid.txt")
DEEPSEEK_DB = Path("/data/deepseek/deeperseeker.db")
KIMI_JSON = Path("/data/kimi/kimi_accounts.json")

ADAPTERS = {
    "glm": {
        "name": "智谱 GLM",
        "port": 8000,
        "container": "glm2api",
        "how": "chatglm.cn → F12 → Application → Cookies → chatglm_refresh_token 的 Value（要已登录，eyJ 开头）",
    },
    "deepseek": {
        "name": "DeepSeek",
        "port": 4000,
        "container": "deeperseeker",
        "how": "chat.deepseek.com → F12 → Console 执行：copy(JSON.parse(localStorage.getItem('userToken')).value) ｜ Console 被拦：先手动敲 allow pasting 回车；或 F12→Application→Local Storage→userToken 的值（一整坨 JSON）里只抄 eyJ 开头那一段",
    },
    "kimi": {
        "name": "Kimi",
        "port": 8000,
        "container": "kimi2api",
        "how": "kimi.com → F12 → Console 执行：copy(localStorage.getItem('refresh_token'))（要 refresh_token，不是 access_token）｜ Console 被拦：先手动敲 allow pasting 回车；或 F12→Application→Local Storage→refresh_token 整段手抄",
    },
    "doubao": {
        "name": "豆包",
        "port": 8000,
        "container": "doubao2api",
        "how": "doubao.com → F12 → Application → Cookies → sessionid 的 Value",
    },
}

INFRA = {
    "omni-caddy": "唯一入口（宿主 127.0.0.1:3000）",
    "new-api": "聚合层（渠道/权重/重试/禁用）",
    "media-router": "生图/生视频双平台故障转移",
    "ops-console": "运维台（本页）",
    "portainer": "Docker 可视管理（可选）",
}


def _cred_present(key):
    try:
        if key == "glm":
            return bool(GLM.read_text(encoding="utf-8").strip())
        if key == "doubao":
            return bool(DOUBAO.read_text(encoding="utf-8").strip())
        if key == "deepseek":
            con = sqlite3.connect(DEEPSEEK_DB)
            r = con.execute("select count(*) from tokens where token != ''").fetchone()[0]
            con.close()
            return bool(r)
        if key == "kimi":
            d = json.loads(KIMI_JSON.read_text(encoding="utf-8"))
            return any(a.get("raw_token") for a in d.get("accounts", []))
    except Exception:
        return False
    return False


KEY_ENV = {"glm": "GLM_KEY", "deepseek": "DEEPSEEK_KEY", "kimi": "KIMI_KEY", "doubao": "DOUBAO_KEY"}
CRED_FILE = {
    "glm": Path("/data/glm/.generated-creds.txt"),
    "deepseek": Path("/data/deepseek/.generated-creds.txt"),
    "kimi": Path("/data/kimi/.generated-creds.txt"),
    "doubao": Path("/data/doubao/.generated-creds.txt"),
}


def _service_key(key):
    try:
        for line in CRED_FILE[key].read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENAI_API_KEY="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    except Exception:
        pass
    return os.getenv(KEY_ENV[key], "")

_QUOTA_RE = re.compile(r"积分不足|余额不足|欠费|充值|1113")
_EXPIRED_RE = re.compile(
    r"Authorization Failed|invalid token|unauthorized|40104|40001|40014|"
    r"token.{0,8}expired|已过期|登录已失效|sessionid|401\b|403\b",
    re.I,
)
_LIMITED_RE = re.compile(
    r"频繁|频率|rate.?limit|too many|429\b|1302|1305|1308|1309|1310|过载|block|40008",
    re.I,
)
_ERROR_HINT_RE = re.compile(
    r"error|failed|失败|401|403|429|积分|余额|频繁|过载|unauthorized|expired|block|1302|1305|1113|40008|40001|40014|1003",
    re.I,
)
_ACCESS_RE = re.compile(r'HTTP/\d\.\d"\s+(\d{3})')
_BACKUP_NAME_RE = re.compile(r"^one-api-\d{8}-\d{4,6}\.db\Z")


def _docker_client():
    return docker.DockerClient(base_url="unix:///var/run/docker.sock")


async def _alive(container, port, service_key):
    try:
        headers = {"Authorization": f"Bearer {service_key}"} if service_key else {}
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"http://{container}:{port}/v1/models", headers=headers
            )
            return r.status_code
    except Exception:
        return 0


async def _http_code(url):
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(url)
            return r.status_code
    except Exception:
        return 0


def _parse_log_ts(value):
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?", value or "")
    if not m:
        return None
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    try:
        return calendar.timegm(
            time.strptime(f"{m.group(1)}.{frac}", "%Y-%m-%dT%H:%M:%S.%f")
        )
    except ValueError:
        return None


def _recent_error(container_name):
    try:
        client = _docker_client()
        container = client.containers.get(container_name)
        started_at = _parse_log_ts(
            container.attrs.get("State", {}).get("StartedAt", "")
        )
        raw = container.logs(tail=120, timestamps=True).decode("utf-8", errors="replace")
        client.close()
    except Exception:
        return None
    for line in reversed(raw.splitlines()):
        if not line.strip():
            continue
        ts_text, _, text = line.partition(" ")
        ts = _parse_log_ts(ts_text)
        if ts is not None and started_at is not None and ts < started_at - 1:
            break
        access = _ACCESS_RE.search(text)
        if access:
            if 200 <= int(access.group(1)) < 300:
                return None
            continue
        if _ERROR_HINT_RE.search(text):
            return text.strip()[-220:]
    return None


def infer_state(http, cred, err):
    if http == 0:
        return "down", "适配器进程未响应"
    if not cred:
        return "expired", "未配置登录凭证"
    if err:
        if _QUOTA_RE.search(err):
            return "quota", "额度/积分不足：" + err[-120:]
        if _EXPIRED_RE.search(err):
            return "expired", "凭证疑似失效：" + err[-120:]
        if _LIMITED_RE.search(err):
            return "limited", "限流/风控：" + err[-120:]
        return "error", "适配器报错：" + err[-120:]
    return "ok", "运行正常"


app = FastAPI(title="ops-console", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
async def _startup():
    autoscale.start()
    asyncio.create_task(maintenance.backup_loop())


@app.post("/api/login")
async def api_login(password: str = Form(...)):
    if not ADMIN_PASSWORD or not hmac.compare_digest(password, ADMIN_PASSWORD):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    resp = JSONResponse({"success": True})
    resp.set_cookie(
        COOKIE_NAME,
        SESSION_TOKEN,
        max_age=315360000,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return resp


def _conn_info():
    gw = os.getenv("GATEWAY_KEY", "")
    return [
        {"group": "使用", "name": "全模态网关（OpenAI 兼容）", "url": "http://127.0.0.1:3000/v1",
         "key": gw, "note": "模型名统一用 free-chat（聊天/生图/生视频同一名字）"},
        {"group": "使用", "name": "全模态网关（Anthropic）", "url": "http://127.0.0.1:3000",
         "key": gw, "note": "CCswitch Claude 用，路径 /v1/messages"},
        {"group": "维护", "name": "统一运维台（本页）", "url": "http://127.0.0.1:3000/ops/",
         "key": "—", "note": ""},
        {"group": "维护", "name": "new-api 原生后台", "url": "http://127.0.0.1:3000/",
         "key": "—", "note": "浏览器直接开根路径（与 API 同入口）"},
        {"group": "维护", "name": "Portainer（Docker 管理）", "url": "http://127.0.0.1:9000",
         "key": "—", "note": "可选工具"},
        {"group": "容器内", "name": "new-api", "url": "new-api:3000",
         "key": "—", "note": "无宿主端口；仅容器网络内以容器名互访（排障用）"},
        {"group": "容器内", "name": "ops-console / media-router", "url": "ops-console:8000 / media-router:8000",
         "key": "—", "note": "无宿主端口（排障用）"},
        {"group": "适配器", "name": "智谱 GLM", "url": "glm2api:8000",
         "key": _service_key("glm"), "note": "对话/检索/生图/生视频"},
        {"group": "适配器", "name": "DeepSeek", "url": "deeperseeker:4000",
         "key": _service_key("deepseek"), "note": "对话/工具/推理"},
        {"group": "适配器", "name": "Kimi", "url": "kimi2api:8000",
         "key": _service_key("kimi"), "note": "对话/检索"},
        {"group": "适配器", "name": "豆包", "url": "doubao2api:8000",
         "key": _service_key("doubao"), "note": "对话/生图"},
    ]


@app.get("/api/info")
async def api_info(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"connections": _conn_info()}


@app.get("/api/status")
async def api_status(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    result = []
    for key, meta in ADAPTERS.items():
        cred = _cred_present(key)
        http = await _alive(meta["container"], meta["port"], _service_key(key))
        err = _recent_error(meta["container"])
        state, detail = infer_state(http, cred, err)
        result.append(
            {
                "key": key,
                "name": meta["name"],
                "container": meta["container"],
                "how": meta["how"],
                "credential": cred,
                "http": http,
                "state": state,
                "detail": detail,
            }
        )
    return {"adapters": result}


def _write_credential(key, value):
    value = value.strip()
    if not value:
        raise ValueError("凭证不能为空")
    if key == "glm":
        GLM.write_text(value, encoding="utf-8")
    elif key == "doubao":
        DOUBAO.write_text(value, encoding="utf-8")
    elif key == "deepseek":
        con = sqlite3.connect(DEEPSEEK_DB)
        row = con.execute("select id from tokens order by id limit 1").fetchone()
        if row:
            con.execute(
                "update tokens set token=?, status='ACTIVE', rate_limited_until=NULL where id=?",
                (value, row[0]),
            )
        else:
            con.execute(
                "insert into tokens(alias,token,status,last_used) values(NULL,?,'ACTIVE',?)",
                (value, time.time()),
            )
        con.commit()
        con.close()
    elif key == "kimi":
        d = json.loads(KIMI_JSON.read_text(encoding="utf-8"))
        accounts = d.get("accounts", [])
        if not accounts:
            raise ValueError("Kimi 账号不存在，请先在适配器初始化")
        a = accounts[0]
        a["raw_token"] = value
        a["cached_access_token"] = ""
        a["cached_access_expires_at"] = 0
        a["cached_access_updated_at"] = 0
        KIMI_JSON.write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        raise ValueError("未知适配器")


def _jwt_payload(token):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:
        return None


def _cred_warn(key, value):
    """保存前的本地格式预检（不打上游），返回提示或 None。"""
    v = value.strip()
    if key == "glm":
        p = _jwt_payload(v)
        if p is None:
            return "格式不像智谱 refresh_token（通常 eyJ 开头的三段式），请核对取值位置"
        if p.get("is_guest"):
            return "这是游客凭证（is_guest=true），无法使用——请先登录 chatglm.cn 再取"
        exp = p.get("exp")
        if exp and exp < time.time():
            return "该凭证已过期（exp 已过），请重新登录获取"
    elif key == "deepseek":
        p = _jwt_payload(v)
        if p is None:
            return "格式不像 DeepSeek userToken（应为 JWT），确认用 copy(JSON.parse(localStorage.getItem('userToken')).value) 取的"
        exp = p.get("exp")
        if exp and exp < time.time():
            return "该 token 已过期（exp 已过），请重新登录获取"
    elif key == "kimi":
        if len(v) < 40:
            return "长度偏短——Kimi 要的是 localStorage 的 refresh_token（不是 access_token）"
    elif key == "doubao":
        if len(v) < 10:
            return "长度偏短，应为 doubao.com 的 sessionid 值"
    return None


@app.post("/api/credential/{key}")
async def set_credential(
    key: str,
    request: Request,
    credential: str = Form(""),
):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if key not in ADAPTERS:
        return JSONResponse({"error": "未知适配器"}, status_code=400)
    warn = _cred_warn(key, credential)
    try:
        _write_credential(key, credential)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    try:
        client = _docker_client()
        client.containers.get(ADAPTERS[key]["container"]).restart()
        client.close()
    except Exception as exc:
        return JSONResponse(
            {"warning": f"凭证已保存，但适配器未能重启: {exc}"}, status_code=200
        )
    result = {"success": True}
    if warn:
        result["warn"] = warn
    return result


def _restart_whitelist():
    return set(INFRA) - {"ops-console"} | {m["container"] for m in ADAPTERS.values()}


def _logs_whitelist():
    return set(INFRA) | {m["container"] for m in ADAPTERS.values()}


@app.get("/api/infra")
async def api_infra(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    def build():
        rows = []
        try:
            client = _docker_client()
        except Exception as exc:
            return {"containers": [], "error": f"Docker 不可达: {exc}"}
        try:
            for name, role in INFRA.items():
                row = {"name": name, "role": role, "running": False, "uptime": "",
                       "image": "—", "created": "", "self": name == "ops-console"}
                try:
                    ct = client.containers.get(name)
                    row["running"] = ct.status == "running"
                    img = ct.image
                    row["image"] = img.tags[0] if img.tags else img.short_id
                    cts = img.attrs.get("Created", "")[:10]
                    row["created"] = cts
                    ts = _parse_log_ts(ct.attrs.get("State", {}).get("StartedAt", ""))
                    if ts and row["running"]:
                        secs = int(time.time() - ts)
                        row["uptime"] = (
                            f"{secs // 86400}天{secs % 86400 // 3600}时"
                            if secs >= 86400
                            else f"{secs // 3600}时{secs % 3600 // 60}分"
                        )
                except Exception:
                    row["image"] = "未找到"
                rows.append(row)
            out = {"containers": rows}
            out.update(maintenance.disk_info())
            try:
                df = client.df()
                imgs = df.get("Images") or []
                out["docker_images"] = len(imgs)
                out["docker_images_gb"] = round(sum(i.get("Size", 0) for i in imgs) / 2**30, 1)
            except Exception:
                pass
            return out
        finally:
            client.close()

    return await asyncio.to_thread(build)


@app.post("/api/restart/{name}")
async def api_restart(name: str, request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if name not in _restart_whitelist():
        return JSONResponse({"error": "不允许重启该容器"}, status_code=400)

    def do():
        client = _docker_client()
        try:
            client.containers.get(name).restart(timeout=15)
        finally:
            client.close()

    try:
        await asyncio.to_thread(do)
        return {"success": True}
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=500)


@app.get("/api/logs/{name}")
async def api_logs(name: str, request: Request, lines: int = 80):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if name not in _logs_whitelist():
        return JSONResponse({"error": "不允许读取该容器"}, status_code=400)
    lines = max(10, min(300, lines))

    def do():
        client = _docker_client()
        try:
            raw = client.containers.get(name).logs(tail=lines)
        finally:
            client.close()
        return raw.decode("utf-8", errors="replace").splitlines()

    try:
        return {"lines": await asyncio.to_thread(do)}
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=500)


@app.post("/api/healthcheck")
async def api_healthcheck(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    checks = []

    def ck(name, ok, detail="", optional=False):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "optional": optional})

    def _ping():
        client = _docker_client()
        client.ping()
        client.close()

    try:
        await asyncio.to_thread(_ping)
        ck("Docker 引擎", True, "正常")
    except Exception as exc:
        ck("Docker 引擎", False, str(exc)[:120])
        return {"checks": checks, "all_ok": False}

    def _container_states():
        client = _docker_client()
        out = {}
        try:
            for n in list(INFRA) + [m["container"] for m in ADAPTERS.values()]:
                try:
                    out[n] = client.containers.get(n).status
                except Exception:
                    out[n] = "未找到"
        finally:
            client.close()
        return out

    for n, st in (await asyncio.to_thread(_container_states)).items():
        ck(f"容器 {n}", st == "running", st, optional=(n == "portainer"))

    code = await _http_code("http://new-api:3000/api/status")
    ck("new-api 接口", code == 200, f"HTTP {code}")
    code = await _http_code("http://omni-caddy/")
    ck("Caddy 入口（容器网内）", code in (200, 307, 308), f"HTTP {code}")
    code = await _http_code("http://host.docker.internal:3000/")
    ck("宿主入口 127.0.0.1:3000", code in (200, 307, 308), f"HTTP {code}")
    for key, meta in ADAPTERS.items():
        code = await _alive(meta["container"], meta["port"], _service_key(key))
        ck(f"适配器 {meta['name']}", code == 200, f"HTTP {code}")
    code = await _http_code("http://media-router:8000/docs")
    ck("media-router", code == 200, f"HTTP {code}")

    gw = os.getenv("GATEWAY_KEY", "")
    if not gw:
        ck("单 key 全链路一致", False, "运维台 GATEWAY_KEY 环境变量为空")
    else:
        ok = True
        dets = []
        gw_bare = gw[3:] if gw.startswith("sk-") else gw
        try:
            con = sqlite3.connect(autoscale.DB_PATH, timeout=10)
            keys = [r[0] for r in con.execute(
                "select key from tokens where deleted_at is null")]
            con.close()
            if gw not in keys and gw_bare not in keys:
                ok = False
                dets.append("new-api token 表不含该 key（不含 sk- 前缀的裸值）")
        except Exception as exc:
            ok = False
            dets.append("读 new-api DB 失败: " + str(exc)[:80])
        for k in ADAPTERS:
            if _service_key(k) != gw:
                ok = False
                dets.append(f"{k} 适配器 key 不一致")
        try:
            def _mr_env():
                client = _docker_client()
                try:
                    ct = client.containers.get("media-router")
                    return dict(
                        e.split("=", 1) for e in ct.attrs["Config"]["Env"] if "=" in e
                    )
                finally:
                    client.close()
            if (await asyncio.to_thread(_mr_env)).get("SERVICE_TOKEN") != gw:
                ok = False
                dets.append("media-router SERVICE_TOKEN 不一致")
        except Exception:
            dets.append("读 media-router env 失败")
        ck("单 key 全链路一致", ok, "；".join(dets) or "new-api / 四适配器 / media-router 全部一致")

    rep = autoscale.report()
    if rep["enabled"]:
        age = int(time.time()) - rep["last_run"] if rep["last_run"] else None
        n_pool = len(rep.get("channels") or [])
        ck("自动调权", age is not None and age < 4 * autoscale.INTERVAL and n_pool > 0,
           f"上次运行 {age} 秒前，池内渠道 {n_pool} 家" if age is not None else "从未运行")
    else:
        ck("自动调权", True, "已暂停（面板设置）")

    din = maintenance.disk_info()
    age = int(time.time()) - din["last_backup"] if din.get("last_backup") else None
    b_ok, b_detail = False, "还没有备份"
    if age is not None:
        items = maintenance.list_backups()["items"]
        integrity = "?"
        if items:
            newest = os.path.join(maintenance.BACKUP_DIR, items[0]["name"])
            try:
                con = sqlite3.connect(f"file:{newest}?mode=ro", uri=True)
                integrity = con.execute("PRAGMA quick_check").fetchone()[0]
                con.close()
            except Exception as exc:
                integrity = f"校验失败: {str(exc)[:60]}"
        b_ok = age < 2 * 86400 and integrity == "ok"
        b_detail = (f"最近备份 {age // 3600} 小时前（完整性 {integrity}），"
                    f"共 {din['backup_count']} 份")
    ck("数据库备份", b_ok, b_detail)
    free = din.get("disk_free_gb")
    ck("磁盘余量", free is None or free > 10, f"剩余 {free} GB / 共 {din.get('disk_total_gb')} GB")

    return {"checks": checks, "all_ok": all(c["ok"] for c in checks if not c.get("optional"))}


@app.get("/api/usage")
async def api_usage(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    def build():
        con = sqlite3.connect(autoscale.DB_PATH, timeout=10)
        try:
            now = time.time()
            lt = time.localtime(now)
            today0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
            rows = con.execute(
                """select channel_id, count(*), sum(prompt_tokens+completion_tokens), avg(use_time)
                   from logs where type=2 and created_at>=?
                   group by channel_id order by count(*) desc""",
                (today0,),
            ).fetchall()
            names = dict(con.execute("select id,name from channels").fetchall())
            by = [
                {"name": names.get(r[0], f"ch{r[0]}"), "n": r[1],
                 "tokens": r[2] or 0, "avg": round(r[3], 1) if r[3] else None}
                for r in rows
            ]
            days = con.execute(
                """select date(created_at,'unixepoch','localtime') d, count(*)
                   from logs where type=2 and created_at>=? group by d order by d desc""",
                (int(now) - 7 * 86400,),
            ).fetchall()
            days = [{"day": d, "n": n} for d, n in days]
            days.reverse()
        finally:
            con.close()
        return {
            "today": time.strftime("%Y-%m-%d", lt),
            "today_total": sum(x["n"] for x in by),
            "today_tokens": sum(x["tokens"] for x in by),
            "by_channel": by,
            "days": days,
        }

    return await asyncio.to_thread(build)


@app.get("/api/backup")
async def api_backup(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await asyncio.to_thread(maintenance.list_backups)


@app.post("/api/backup/run")
async def api_backup_run(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await asyncio.to_thread(maintenance.run_backup)


@app.get("/api/backup/download/{name}")
async def api_backup_download(name: str, request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not _BACKUP_NAME_RE.match(name):
        return JSONResponse({"error": "文件名不合法"}, status_code=400)
    path = os.path.join(maintenance.BACKUP_DIR, name)
    if not os.path.isfile(path):
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(path, media_type="application/octet-stream", filename=name)


@app.get("/api/autoscale")
async def api_autoscale(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await asyncio.to_thread(autoscale.report)


@app.post("/api/autoscale/run")
async def api_autoscale_run(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        return await asyncio.to_thread(autoscale.run_once, False, "manual")
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/autoscale/toggle")
async def api_autoscale_toggle(request: Request, enabled: bool = Form(...)):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    autoscale.set_enabled(enabled)
    return await asyncio.to_thread(autoscale.report)


@app.post("/api/channel/enable/{cid}")
async def api_channel_enable(cid: int, request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    ok = await asyncio.to_thread(autoscale.enable_channel, cid)
    if not ok:
        return JSONResponse({"error": "渠道不存在或不属于 free-chat 池"}, status_code=404)
    return {"success": True}


CHANNEL_TYPES = {1: "OpenAI 兼容", 14: "Anthropic 兼容"}
POOL_PRIORITIES = {"pool": 0, "backup": -1, "own": 0}


def _norm_base(url: str) -> str:
    return url.strip().rstrip("/")


@app.get("/api/channels")
async def api_channels(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    def build():
        con = sqlite3.connect(autoscale.DB_PATH, timeout=10)
        try:
            lt = time.localtime()
            today0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
            today = dict(con.execute(
                "select channel_id, count(*) from logs where type=2 and created_at>=? group by channel_id",
                (today0,),
            ).fetchall())
            prio = dict(con.execute(
                "select channel_id, priority from abilities where model='free-chat' and \"group\"='default'"
            ).fetchall())
            rows = con.execute(
                """select id,name,type,base_url,models,status,weight,priority,model_mapping
                   from channels order by id"""
            ).fetchall()
        finally:
            con.close()
        out = []
        for (cid, name, typ, url, models, status, weight, priority, mm) in rows:
            if cid in prio:
                place = "轮换池" if prio[cid] == 0 else "备用"
            else:
                place = "独立"
            out.append({
                "id": cid, "name": name, "type": typ,
                "type_name": CHANNEL_TYPES.get(typ, f"类型 {typ}"),
                "base_url": url or "", "models": models or "",
                "status": status, "weight": weight, "place": place,
                "today": today.get(cid, 0), "mapping": mm or "",
            })
        return {"channels": out}

    return await asyncio.to_thread(build)


@app.post("/api/channels/add")
async def api_channels_add(
    request: Request,
    name: str = Form(""),
    base_url: str = Form(""),
    key: str = Form(""),
    mode: str = Form(""),
    upstream_model: str = Form(""),
    own_models: str = Form(""),
    ctype: int = Form(1),
):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    name = name.strip()
    base_url = _norm_base(base_url)
    key = key.strip()
    if not name or not key:
        return JSONResponse({"error": "名称与 API Key 不能为空"}, status_code=400)
    if not base_url or not re.match(r"^https?://", base_url):
        return JSONResponse({"error": "Base URL 必填，且需以 http:// 或 https:// 开头"}, status_code=400)
    if ctype not in CHANNEL_TYPES:
        return JSONResponse({"error": "渠道类型不支持"}, status_code=400)
    if not mode:
        return JSONResponse({"error": "请选择用途（轮换/备用/独立模型名）"}, status_code=400)
    if mode not in POOL_PRIORITIES:
        return JSONResponse({"error": "用途模式不支持"}, status_code=400)
    if mode in ("pool", "backup"):
        um = upstream_model.strip()
        if not um:
            return JSONResponse({"error": "并入/备用模式需要填上游真实模型名"}, status_code=400)
        models = "free-chat"
        mapping = json.dumps({"free-chat": um}, ensure_ascii=False)
    else:
        ml = [m.strip() for m in re.split(r"[,，]", own_models) if m.strip()]
        if not ml:
            return JSONResponse({"error": "独立模型名不能为空"}, status_code=400)
        models = ",".join(ml)
        mapping = None
    priority = POOL_PRIORITIES[mode]

    def do():
        con = sqlite3.connect(autoscale.DB_PATH, timeout=20)
        con.execute("PRAGMA busy_timeout=20000")
        try:
            cur = con.execute(
                """insert into channels
                   (type,key,status,name,weight,created_time,base_url,models,"group",priority,auto_ban,model_mapping)
                   values (?,?,1,?,100,?,?,?,'default',?,1,?)""",
                (ctype, key, name, int(time.time()), base_url, models, priority, mapping),
            )
            cid = cur.lastrowid
            for m in models.split(","):
                con.execute(
                    """insert into abilities ("group",model,channel_id,enabled,priority,weight)
                       values ('default',?,?,1,?,100)""",
                    (m, cid, priority),
                )
            con.commit()
            return cid
        finally:
            con.close()

    cid = await asyncio.to_thread(do)
    print(f"[channels] 新增渠道 #{cid} {name} ({mode}, {models})", flush=True)
    return {"success": True, "id": cid, "note": "已添加，约 60 秒内自动生效（无需重启）"}


@app.post("/api/channels/update/{cid}")
async def api_channels_update(
    cid: int,
    request: Request,
    name: str = Form(""),
    base_url: str = Form(""),
    key: str = Form(""),
):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    sets, vals = [], []
    if name.strip():
        sets.append("name=?")
        vals.append(name.strip())
    if base_url.strip():
        sets.append("base_url=?")
        vals.append(_norm_base(base_url))
    if key.strip():
        sets.append("key=?")
        vals.append(key.strip())
    if not sets:
        return JSONResponse({"error": "没有要修改的字段"}, status_code=400)

    def do():
        con = sqlite3.connect(autoscale.DB_PATH, timeout=20)
        try:
            cur = con.execute(
                f"update channels set {','.join(sets)} where id=?", (*vals, cid)
            )
            con.commit()
            return cur.rowcount
        finally:
            con.close()

    n = await asyncio.to_thread(do)
    if not n:
        return JSONResponse({"error": "渠道不存在"}, status_code=404)
    return {"success": True, "note": "已更新，约 60 秒生效"}


@app.post("/api/channels/set_status/{cid}")
async def api_channels_set_status(cid: int, request: Request, enabled: bool = Form(...)):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    flag = 1 if enabled else 0

    def do():
        con = sqlite3.connect(autoscale.DB_PATH, timeout=20)
        try:
            cur = con.execute("update channels set status=? where id=?", (1 if flag else 2, cid))
            con.execute("update abilities set enabled=? where channel_id=?", (flag, cid))
            con.commit()
            return cur.rowcount
        finally:
            con.close()

    n = await asyncio.to_thread(do)
    if not n:
        return JSONResponse({"error": "渠道不存在"}, status_code=404)
    return {"success": True, "note": f"已{'启用' if flag else '停用'}，约 60 秒生效"}


@app.post("/api/channels/delete/{cid}")
async def api_channels_delete(cid: int, request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    def do():
        con = sqlite3.connect(autoscale.DB_PATH, timeout=20)
        try:
            row = con.execute("select name from channels where id=?", (cid,)).fetchone()
            if not row:
                return None
            con.execute("delete from channels where id=?", (cid,))
            con.execute("delete from abilities where channel_id=?", (cid,))
            con.commit()
            return row[0]
        finally:
            con.close()

    name = await asyncio.to_thread(do)
    if name is None:
        return JSONResponse({"error": "渠道不存在"}, status_code=404)
    print(f"[channels] 已删除渠道 #{cid} {name}", flush=True)
    return {"success": True, "deleted": name, "note": "已删除，约 60 秒生效"}


@app.post("/api/channels/test/{cid}")
async def api_channels_test(cid: int, request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    def get():
        con = sqlite3.connect(autoscale.DB_PATH, timeout=10)
        try:
            return con.execute(
                "select base_url,key from channels where id=?", (cid,)
            ).fetchone()
        finally:
            con.close()

    row = await asyncio.to_thread(get)
    if not row:
        return JSONResponse({"error": "渠道不存在"}, status_code=404)
    base, chan_key = (row[0] or "").rstrip("/"), row[1] or ""
    if not base:
        return {"ok": False, "detail": "该渠道未配置 base_url（内置适配器请用上方适配器状态灯判活）"}
    url = base if (base.endswith("/v1") or re.search(r"/v\d+$", base)) else base + "/v1"
    url += "/models"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers={"Authorization": f"Bearer {chan_key}"})
        if r.status_code == 200:
            return {"ok": True, "detail": "连接成功，key 有效（只查模型列表，不消耗额度）"}
        if r.status_code in (401, 403):
            return {"ok": False, "detail": f"HTTP {r.status_code}：key 无效或无权限"}
        return {
            "ok": False,
            "detail": f"HTTP {r.status_code}（不支持 /models 查询的服务属正常，以实际调用为准）",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"连接失败: {str(exc)[:120]}"}


@app.get("/", response_class=HTMLResponse)
async def console_page():
    return PAGE
