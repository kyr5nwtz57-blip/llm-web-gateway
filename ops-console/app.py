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
from fastapi.responses import HTMLResponse, JSONResponse

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
    "glm": {"name": "智谱 GLM", "port": 8000, "container": "glm2api"},
    "deepseek": {"name": "DeepSeek", "port": 4000, "container": "deeperseeker"},
    "kimi": {"name": "Kimi", "port": 8000, "container": "kimi2api"},
    "doubao": {"name": "豆包", "port": 8000, "container": "doubao2api"},
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


def _parse_log_ts(value):
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?", value or "")
    if not m:
        return None
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    try:
        return time.mktime(time.strptime(f"{m.group(1)}.{frac}", "%Y-%m-%dT%H:%M:%S.%f"))
    except ValueError:
        return None


def _recent_error(container_name):
    try:
        client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
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


app = FastAPI(title="ops-console")


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
        samesite="lax",
        path="/",
    )
    return resp


def _conn_info():
    import os
    return [
        {"group": "使用", "name": "全模态网关（OpenAI 兼容）", "url": "http://127.0.0.1:3000/v1",
         "key": os.getenv("GATEWAY_KEY", ""), "note": "模型名统一用 free-chat（聊天/生图/生视频同一名字）"},
        {"group": "使用", "name": "全模态网关（Anthropic）", "url": "http://127.0.0.1:3000",
         "key": os.getenv("GATEWAY_KEY", ""), "note": "CCswitch Claude 用，路径 /v1/messages"},
        {"group": "维护", "name": "统一运维台（本页）", "url": "http://127.0.0.1:3000/ops/",
         "key": "—", "note": ""},
        {"group": "维护", "name": "new-api 原生后台", "url": "http://127.0.0.1:3010",
         "key": "—", "note": "渠道/日志，平时不用开"},
        {"group": "适配器", "name": "智谱 GLM（主）", "url": "http://127.0.0.1:8083",
         "key": _service_key("glm"), "note": "对话/检索/生图/生视频"},
        {"group": "适配器", "name": "DeepSeek（主）", "url": "http://127.0.0.1:8082",
         "key": _service_key("deepseek"), "note": "对话/工具/推理"},
        {"group": "适配器", "name": "Kimi（备）", "url": "http://127.0.0.1:8081",
         "key": _service_key("kimi"), "note": "对话/检索"},
        {"group": "适配器", "name": "豆包（备）", "url": "http://127.0.0.1:8084",
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


@app.post("/api/credential/{key}")
async def set_credential(
    key: str,
    request: Request,
    credential: str = Form(...),
):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if key not in ADAPTERS:
        return JSONResponse({"error": "未知适配器"}, status_code=400)
    try:
        _write_credential(key, credential)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    try:
        client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        client.containers.get(ADAPTERS[key]["container"]).restart()
        client.close()
    except Exception as exc:
        return JSONResponse(
            {"warning": f"凭证已保存，但适配器未能重启: {exc}"}, status_code=200
        )
    return {"success": True}


@app.get("/", response_class=HTMLResponse)
async def console_page():
    return PAGE


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全模态网关 · 统一运维台</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:24px}
.wrap{max-width:860px;margin:0 auto}
h2{font-weight:600;margin:0 0 4px}
.sub{color:#888;font-size:13px;margin-bottom:20px}
.login{background:#171a21;border:1px solid #262b36;border-radius:10px;padding:16px;margin-bottom:16px}
input,textarea,button{font:inherit;border-radius:8px;border:1px solid #2c323f;background:#10131a;color:#e6e6e6;padding:9px 11px}
input{width:220px}
textarea{width:100%;box-sizing:border-box;min-height:64px;font-family:ui-monospace,monospace;font-size:12px}
button{cursor:pointer;background:#2563eb;border-color:#2563eb;color:#fff}
button.ghost{background:transparent;color:#9aa4b2;border-color:#2c323f}
.card{background:#171a21;border:1px solid #262b36;border-radius:12px;padding:15px 16px;margin-bottom:12px}
.row{display:flex;align-items:center;gap:10px}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
.ok{background:#22c55e}.bad{background:#ef4444}.warn{background:#eab308}.quota{background:#e5e7eb}.down{background:#6b7280}.err{background:#f97316}
.name{font-weight:600;width:110px}
.meta{color:#888;font-size:12px}
.grow{flex:1}
.hidden{display:none}
.msg{font-size:13px;margin-top:8px}
</style></head><body><div class="wrap">
<h2>全模态网关 · 统一运维台</h2>
<div class="sub">一个入口查看四家状态、统一更换登录凭证。凭证仅在本机写入，重启对应适配器后生效。</div>

<div id="login" class="login">
  <div class="row">
    <input id="pw" type="password" placeholder="运维台密码">
    <button onclick="login()">进入</button>
  </div>
</div>

<div id="panel" class="hidden">
  <div id="conn"></div>
  <div class="row" style="margin:6px 0 14px">
    <h3 style="margin:18px 0 0;font-size:15px">适配器状态</h3>
    <div class="grow"></div>
    <button class="ghost" onclick="load()">刷新状态</button>
  </div>
  <div id="cards"></div>
</div>
</div>
<script>
let pw="";
function mask(k){return k==='—'?'—':(k.slice(0,6)+'••••'+k.slice(-4));}
async function copyText(t){try{await navigator.clipboard.writeText(t);}catch(e){}}
async function loadConn(){
  const r=await fetch('api/info');
  const d=await r.json();
  const box=document.getElementById('conn');
  let html=`<h3 style="margin:4px 0 10px;font-size:15px">连接信息（全部端点与 Key）</h3>`;
  html+=`<table style="width:100%;border-collapse:collapse;font-size:13px">`;
  d.connections.forEach((c,i)=>{
    html+=`<tr style="border-bottom:1px solid #262b36">
      <td style="padding:8px 6px;color:#7dd3fc;width:56px">${c.group}</td>
      <td style="padding:8px 6px;width:170px">${c.name}</td>
      <td style="padding:8px 6px"><span style="color:#9aa4b2">${c.url}</span>
        <button class="ghost" style="padding:2px 7px;font-size:11px;margin-left:4px" onclick="copyText('${c.url}')">复制</button>
        <div style="color:#666;font-size:11px;margin-top:2px">${c.note}</div></td>
      <td style="padding:8px 6px;width:230px">
        <span class="kv" data-full="${c.key}">${mask(c.key)}</span>
        <button class="ghost" style="padding:2px 7px;font-size:11px" onclick="copyText('${c.key}')">复制Key</button>
        <button class="ghost" style="padding:2px 7px;font-size:11px" onclick="toggleKey(this)">显示</button>
      </td></tr>`;
  });
  html+=`</table>`;
  box.innerHTML=html;
}
function toggleKey(btn){
  const s=btn.previousElementSibling.previousElementSibling;
  const showing=s.dataset.show==='1';
  s.textContent=showing?mask(s.dataset.full):s.dataset.full;
  s.dataset.show=showing?'0':'1';
  btn.textContent=showing?'显示':'隐藏';
}
function login(){
  const value=document.getElementById('pw').value;
  const body=new URLSearchParams({password:value});
  fetch('api/login',{method:'POST',body}).then(r=>{
    if(r.ok){pw=value;load(true);}
    else alert('密码错误');
  });
}
async function load(first){
  const r=await fetch('api/status');
  if(!r.ok){
    if(first){showLogin();}
    return;
  }
  document.getElementById('login').classList.add('hidden');
  document.getElementById('panel').classList.remove('hidden');
  await loadConn();
  const d=await r.json();
  const box=document.getElementById('cards');
  box.innerHTML='';
  d.adapters.forEach(a=>{
    const dotClass={ok:'ok',expired:'bad',limited:'warn',quota:'quota',down:'down',error:'err'}[a.state]||'down';
    const stateText={ok:'正常',expired:'凭证过期 · 去换凭证',limited:'限流/风控 · 等一会',quota:'积分/额度不足 · 等恢复',down:'进程未响应',error:'适配器报错 · 看详情'}[a.state]||a.state;
    const card=document.createElement('div');
    card.className='card';
    card.innerHTML=`
      <div class="row">
        <span class="dot ${dotClass}"></span>
        <span class="name">${a.name}</span>
        <span class="meta">${stateText}${a.detail&&a.state!=='ok'?' · '+a.detail:''}</span>
        <div class="grow"></div>
        <button class="ghost" onclick="toggle('${a.key}',this)">更换凭证</button>
      </div>
      <div class="hidden" style="margin-top:12px">
        <textarea id="t-${a.key}" placeholder="粘贴该平台新的登录凭证（sessionid / refresh_token / userToken）"></textarea>
        <div class="row" style="margin-top:8px">
          <div class="grow"></div>
          <button class="ghost" onclick="toggle('${a.key}',this)">取消</button>
          <button onclick="save('${a.key}',this)">保存并重启适配器</button>
        </div>
        <div class="msg" id="m-${a.key}"></div>
      </div>`;
    box.appendChild(card);
  });
}
function toggle(key,btn){
  const card=btn.closest('.card').querySelector('.hidden');
  card.classList.toggle('hidden');
}
async function save(key,btn){
  const val=document.getElementById('t-'+key).value;
  const msg=document.getElementById('m-'+key);
  msg.textContent='保存中…';
  const body=new URLSearchParams({credential:val});
  const r=await fetch('api/credential/'+key,{method:'POST',body});
  const d=await r.json();
  if(d.success){msg.textContent='✅ 已保存，适配器已重启，稍后点“刷新状态”确认。';}
  else if(d.warning){msg.textContent='⚠️ '+d.warning;}
  else {msg.textContent='❌ '+d.error;}
}
function showLogin(){
  document.getElementById('login').classList.remove('hidden');
  document.getElementById('panel').classList.add('hidden');
}
load(true);
</script></body></html>"""
