import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("glm2api")

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = DATA_DIR / "glm_refresh_token.txt"

SERVICE_KEY = os.getenv("GLM_API_KEY", "")
ADMIN_PASSWORD = os.getenv("GLM_ADMIN_PASSWORD", "")

BASE = "https://chatglm.cn"
IMAGE_ASSISTANT_ID = "65a232c082ff90a2ad2f15e2"
_sem = asyncio.Semaphore(2)
_access_token = ""
_access_expires = 0.0

TOOL_CALL_RE = re.compile(
    r"<\|tool_call\|>\s*(.*?)\s*<\|/tool_call\|>", re.DOTALL
)


def _sign():
    a = str(int(time.time() * 1000))
    digits = [int(c) for c in a]
    check = (sum(digits) - digits[-2]) % 10
    ts = a[:-2] + str(check) + a[-1]
    nonce = uuid.uuid4().hex
    sign = hashlib.md5(
        f"{ts}-{nonce}-8a1317a7468aa3ad86e997d08f3f31cb".encode()
    ).hexdigest()
    return ts, nonce, sign


def _headers(token: str):
    ts, nonce, sign = _sign()
    return {
        "Accept": "text/event-stream",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "App-Name": "chatglm",
        "Authorization": f"Bearer {token}",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "Origin": BASE,
        "Pragma": "no-cache",
        "Referer": f"{BASE}/main/alltoolsdetail",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "X-App-Fr": "browser_extension",
        "X-App-Platform": "pc",
        "X-App-Version": "0.0.1",
        "X-Device-Id": uuid.uuid4().hex,
        "X-Exp-Groups": "mainchat_server_app:exp:A",
        "X-Lang": "zh",
        "X-Nonce": nonce,
        "X-Request-Id": uuid.uuid4().hex,
        "X-Sign": sign,
        "X-Timestamp": ts,
    }


async def _refresh_token() -> str:
    global _access_token, _access_expires
    if _access_token and time.time() < _access_expires:
        return _access_token
    rt = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not rt:
        raise RuntimeError("refresh_token 未配置")
    async with _sem, httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE}/chatglm/user-api/user/refresh",
            headers=_headers(rt),
            json={},
        )
        data = r.json()
    if data.get("status") not in (0, None):
        raise RuntimeError(f"刷新 access_token 失败: {data.get('message')}")
    _access_token = data["result"]["access_token"]
    new_rt = data["result"].get("refresh_token")
    if new_rt and new_rt != rt:
        TOKEN_FILE.write_text(new_rt, encoding="utf-8")
    _access_expires = time.time() + 3300
    return _access_token


def _tool_prompt(tools):
    blocks = []
    for t in tools:
        fn = t.get("function", t)
        blocks.append(
            f"### {fn.get('name')}\n{fn.get('description','')}\n"
            f"参数: {json.dumps(fn.get('parameters', {}), ensure_ascii=False)}"
        )
    return (
        "\n\n你可以使用以下工具。需要调用工具时，严格只输出下面的格式块，每个工具调用一块，"
        "不要输出解释或 Markdown：\n"
        "<|tool_call|>\n"
        '{"name": "工具名", "arguments": {参数对象}}\n'
        "<|/tool_call|>\n\n" + "\n\n".join(blocks)
    )


def _prepare_messages(messages, tools):
    out = [dict(m) for m in messages]
    if tools:
        prompt = _tool_prompt(tools)
        idx = next((i for i, m in enumerate(out) if m.get("role") == "system"), None)
        if idx is None:
            out.insert(0, {"role": "system", "content": prompt})
        else:
            out[idx] = {**out[idx], "content": str(out[idx].get("content", "")) + prompt}
    converted = []
    for m in out:
        role = m.get("role", "user")
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": f"工具 {m.get('name','')} 返回结果：\n{m.get('content','')}",
                }
            )
        else:
            converted.append(m)
    return converted


def _flatten(messages) -> str:
    if len(messages) < 2:
        return "\n".join(str(m.get("content", "")) for m in messages)
    role_map = {
        "system": "<|im_start|>system",
        "assistant": "<|im_start|>assistant",
        "user": "<|im_start|>user",
    }
    out = ""
    for m in messages:
        out += (
            f"{role_map.get(m.get('role'), '<|im_start|>user')}\n"
            f"{m.get('content', '')}\n<|im_end|>\n"
        )
    return out


def _body(messages):
    return {
        "assistant_id": "65940acff94777010aa6b796",
        "conversation_id": "",
        "project_id": "",
        "chat_type": "user_chat",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": _flatten(messages)}]}
        ],
        "meta_data": {
            "if_plus_model": True,
            "is_networking": True,
            "is_test": False,
            "platform": "pc",
            "cogview": {"rm_label_watermark": False},
        },
    }


def _part_text(part) -> str:
    text = ""
    for item in part.get("content") or []:
        if item.get("type") == "text":
            text += item.get("text", "")
    return text


def parse_tool_calls(full_text):
    calls = []
    for i, m in enumerate(TOOL_CALL_RE.finditer(full_text)):
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
            name = obj.get("name", "")
            args = obj.get("arguments", {})
            args_str = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        except json.JSONDecodeError:
            name, args_str = "", raw
        calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": name, "arguments": args_str},
            }
        )
    clean = TOOL_CALL_RE.sub("", full_text).strip()
    return calls, clean


TOOL_CALL_MARKER = "<|tool_call|>"


def _plain_view(full_text: str) -> str:
    view = TOOL_CALL_RE.sub("", full_text)
    idx = view.rfind(TOOL_CALL_MARKER)
    if idx != -1:
        view = view[:idx]
    for k in range(len(TOOL_CALL_MARKER) - 1, 0, -1):
        if view.endswith(TOOL_CALL_MARKER[:k]):
            view = view[:-k]
            break
    return view


async def _collect_events(messages, assistant_id=None):
    token = await _refresh_token()
    body = _body(messages)
    if assistant_id:
        body["assistant_id"] = assistant_id
    client = httpx.AsyncClient(timeout=httpx.Timeout(300))
    req = client.build_request(
        "POST",
        BASE + "/chatglm/backend-api/assistant/stream",
        headers=_headers(token),
        json=body,
    )
    r = await client.send(req, stream=True)
    if r.status_code != 200:
        text = await r.aread()
        await r.aclose()
        await client.aclose()
        raise RuntimeError(f"上游 HTTP {r.status_code}: {text[:200]!r}")
    return client, r


async def _iter_events(r):
    data_line = ""
    async for line in r.aiter_lines():
        if line.startswith("data:"):
            data_line = line[5:].strip()
        elif line == "" and data_line:
            try:
                yield json.loads(data_line)
            except json.JSONDecodeError:
                logger.warning("skip non-JSON sse data: %s", data_line[:200])
            data_line = ""


app = FastAPI(title="glm-web2api")


def _authorized(authorization: str) -> bool:
    return bool(SERVICE_KEY) and authorization == f"Bearer {SERVICE_KEY}"


@app.get("/health")
async def health():
    ok = TOKEN_FILE.exists() and bool(TOKEN_FILE.read_text(encoding="utf-8").strip())
    return JSONResponse(
        {"status": "ok" if ok else "degraded"}, status_code=200 if ok else 503
    )


@app.get("/v1/models")
async def models(authorization: str = Header(default="")):
    if not _authorized(authorization):
        return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
    return {
        "object": "list",
        "data": [{"id": "glm", "object": "model", "owned_by": "chatglm-web"}],
    }


@app.post("/v1/chat/completions")
async def chat(request: Request, authorization: str = Header(default="")):
    if not _authorized(authorization):
        return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
    payload = await request.json()
    tools = payload.get("tools") or []
    messages = _prepare_messages(payload.get("messages", []), tools)
    do_stream = bool(payload.get("stream"))

    if do_stream:
        try:
            client, r = await _collect_events(messages)
        except RuntimeError as exc:
            logger.error("stream upstream failed: %s", exc)
            return JSONResponse(
                {"error": {"message": str(exc)}}, status_code=502
            )

        async def gen():
            full = ""
            my_full = ""
            emitted_plain = 0
            try:
                async for ev in _iter_events(r):
                    full = "".join(_part_text(p) for p in ev.get("parts") or [])
                    if full.startswith(my_full):
                        my_full = full
                    else:
                        my_full += full
                    visible = _plain_view(my_full)
                    if len(visible) > emitted_plain:
                        piece = visible[emitted_plain:]
                        emitted_plain = len(visible)
                        chunk = {
                            "object": "chat.completion.chunk",
                            "model": "glm",
                            "choices": [
                                {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                            ],
                        }
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    if ev.get("status") == "finish":
                        calls, _ = parse_tool_calls(full)
                        if not calls and emitted_plain == 0:
                            logger.warning(
                                "stream finished with empty reply (soft rate-limit?)"
                            )
                            err = {
                                "error": {
                                    "message": "上游返回空回复（疑似软限流），请重试"
                                }
                            }
                            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                            return
                        if calls:
                            tc = {
                                "object": "chat.completion.chunk",
                                "model": "glm",
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"tool_calls": calls},
                                        "finish_reason": "tool_calls",
                                    }
                                ],
                            }
                            yield f"data: {json.dumps(tc, ensure_ascii=False)}\n\n"
                        else:
                            end = {
                                "object": "chat.completion.chunk",
                                "model": "glm",
                                "choices": [
                                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                                ],
                            }
                            yield f"data: {json.dumps(end)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                if emitted_plain == 0:
                    err = {
                        "error": {"message": "上游中断且未返回内容（疑似软限流），请重试"}
                    }
                    yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                    return
            finally:
                await r.aclose()
                await client.aclose()

        return StreamingResponse(gen(), media_type="text/event-stream")

    full = ""
    client, r = await _collect_events(messages)
    try:
        async for ev in _iter_events(r):
            full = "".join(_part_text(p) for p in ev.get("parts") or [])
            if ev.get("status") == "finish":
                break
    finally:
        await r.aclose()
        await client.aclose()

    calls, clean = parse_tool_calls(full)
    if not calls and not clean.strip():
        logger.warning("non-stream empty reply (soft rate-limit?)")
        return JSONResponse(
            {"error": {"message": "上游返回空回复（疑似软限流），请重试"}},
            status_code=502,
        )
    message = {"role": "assistant", "content": clean if not calls else None}
    if calls:
        message["tool_calls"] = calls
    return {
        "object": "chat.completion",
        "model": "glm",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
    }


@app.post("/v1/images/generations")
async def images_generations(request: Request, authorization: str = Header(default="")):
    if not _authorized(authorization):
        return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
    payload = await request.json()
    prompt = payload.get("prompt", "")
    n = min(int(payload.get("n") or 4), 4)
    client, r = await _collect_events(
        [{"role": "user", "content": prompt}], IMAGE_ASSISTANT_ID
    )
    urls = []
    try:
        async for ev in _iter_events(r):
            for part in ev.get("parts", []):
                for item in part.get("content", []):
                    if item.get("type") in ("image", "one_to_more_finish"):
                        for img in item.get("image", []):
                            u = img.get("image_url")
                            if u and u not in urls:
                                urls.append(u)
    finally:
        await r.aclose()
        await client.aclose()
    return {
        "created": int(time.time()),
        "data": [{"url": u} for u in urls[:n]],
    }


@app.post("/v1/videos/generations")
async def videos_generations(request: Request, authorization: str = Header(default="")):
    if not _authorized(authorization):
        return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
    payload = await request.json()
    prompt = payload.get("prompt", "")
    token = await _refresh_token()
    h = _headers(token)
    h["Referer"] = BASE + "/video"
    create_body = {
        "conversation_id": "",
        "prompt": prompt,
        "advanced_parameter_extra": {
            "emotional_atmosphere": "",
            "mirror_mode": "",
            "video_style": "",
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(360)) as client:
        r = await client.post(
            BASE + "/chatglm/video-api/v1/chat", headers=h, json=create_body
        )
        result = r.json().get("result") or {}
        chat_id = result.get("chat_id")
        if not chat_id:
            logger.error("video create failed: %s", r.text[:200])
            return JSONResponse(
                {"error": {"message": f"视频任务创建失败: {r.text[:200]}"}},
                status_code=502,
            )
        deadline = time.time() + 300
        while time.time() < deadline:
            await asyncio.sleep(10)
            at = await _refresh_token()
            hh = _headers(at)
            hh["Referer"] = BASE + "/video"
            rs = await client.get(
                BASE + f"/chatglm/video-api/v1/chat/status/{chat_id}", headers=hh
            )
            res = (rs.json() or {}).get("result") or {}
            status = res.get("status")
            if status == "finished":
                return {
                    "status": "completed",
                    "id": chat_id,
                    "video_url": res.get("video_url"),
                    "cover_url": res.get("cover_url"),
                    "duration": res.get("video_duration"),
                    "resolution": res.get("video_resolution"),
                }
            if status not in ("init", "processing"):
                logger.error("video generate failed: %s", json.dumps(res, ensure_ascii=False)[:200])
                return JSONResponse(
                    {"error": {"message": f"视频生成失败: {res}"}}, status_code=502
                )
    return JSONResponse({"error": {"message": "视频生成超时"}}, status_code=504)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return """<!doctype html><html><head><meta charset="utf-8"><title>GLM 适配器</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto">
<h3>智谱网页适配器 · 填入 refresh_token</h3>
<form method="post" action="/admin/save">
<input type="password" name="password" placeholder="管理密码" style="width:100%;padding:8px;margin:6px 0">
<textarea name="token" rows="4" placeholder="粘贴 chatglm_refresh_token 的值" style="width:100%;padding:8px;margin:6px 0"></textarea>
<button type="submit">保存</button>
</form></body></html>"""


@app.post("/admin/save")
async def admin_save(password: str = Form(...), token: str = Form(...)):
    if not ADMIN_PASSWORD or password != ADMIN_PASSWORD:
        return HTMLResponse("管理密码错误", status_code=403)
    TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    global _access_token, _access_expires
    _access_token, _access_expires = "", 0.0
    return HTMLResponse("<script>alert('已保存');location.href='/admin';</script>")
