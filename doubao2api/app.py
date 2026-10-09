import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.responses import StreamingResponse

from doubao2api.client import DoubaoChatClient, DoubaoChatError

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = DATA_DIR / "doubao_sessionid.txt"

SERVICE_KEY = os.getenv("DOUBAO_API_KEY", "")
ADMIN_PASSWORD = os.getenv("DOUBAO_ADMIN_PASSWORD", "")

_sem = asyncio.Semaphore(2)

app = FastAPI(title="doubao-web2api")


def _authorized(authorization: str) -> bool:
    return bool(SERVICE_KEY) and authorization == f"Bearer {SERVICE_KEY}"


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
        out += f"{role_map.get(m.get('role'), '<|im_start|>user')}\n{m.get('content', '')}\n<|im_end|>\n"
    return out


async def _stream_text(text):
    sessionid = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not sessionid:
        raise RuntimeError("sessionid 未配置")
    cookies = {"sessionid": sessionid}
    async with DoubaoChatClient(cookies=cookies, ms_token="", captcha_handler=None) as client:
        async for msg in client.chat_stream_samantha(text=text):
            # 2005 = 上游错误事件（如风控限流 710022004 rate limited）。
            # 绝不静默吞掉——否则会变成"空回复成功"，骗过故障转移与自动调权。
            if getattr(msg, "event_type", 0) == 2005:
                detail = str((getattr(msg, "raw", None) or {}).get("event_data", ""))[:300]
                raise DoubaoChatError(f"upstream error event: {detail}")
            if getattr(msg, "is_answer_chunk", False) and msg.text:
                yield msg.text


def _is_rate_limit_error(exc) -> bool:
    s = str(exc)
    return any(k in s for k in ("710022004", "710022002", "rate limited", "Rate limited"))


def _err_status(exc) -> int:
    return 429 if _is_rate_limit_error(exc) else 502


def _chunk_text(piece: str) -> str:
    return json.dumps(
        {
            "object": "chat.completion.chunk",
            "model": "doubao",
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        },
        ensure_ascii=False,
    )


@app.get("/health")
async def health():
    ok = TOKEN_FILE.exists() and bool(TOKEN_FILE.read_text(encoding="utf-8").strip())
    return JSONResponse({"status": "ok" if ok else "degraded"}, status_code=200 if ok else 503)


@app.get("/v1/models")
async def models(authorization: str = Header(default="")):
    if not _authorized(authorization):
        return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
    return {"object": "list", "data": [{"id": "doubao", "object": "model", "owned_by": "doubao-web"}]}


async def _collect_text(text: str) -> str:
    """完整跑一次上游调用，返回全部正文。出错或空答复都抛 DoubaoChatError，
    绝不返回"空成功"——否则会骗过 new-api 的故障转移与自动调权。"""
    parts = []
    async with _sem:
        async for piece in _stream_text(text):
            parts.append(piece)
    if not parts:
        raise DoubaoChatError("upstream returned empty response (no content)")
    return "".join(parts)


@app.post("/v1/chat/completions")
async def chat(request: Request, authorization: str = Header(default="")):
    if not _authorized(authorization):
        return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
    payload = await request.json()
    text = _flatten(payload.get("messages", []))
    do_stream = bool(payload.get("stream"))

    try:
        full = await _collect_text(text)
    except (DoubaoChatError, RuntimeError) as exc:
        return JSONResponse(
            {"error": {"message": f"doubao upstream failed: {exc}"}},
            status_code=_err_status(exc),
        )

    if do_stream:

        async def gen():
            yield f"data: {_chunk_text(full)}\n\n"
            end = json.dumps(
                {
                    "object": "chat.completion.chunk",
                    "model": "doubao",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            )
            yield f"data: {end}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return {
        "object": "chat.completion",
        "model": "doubao",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": full}, "finish_reason": "stop"}],
    }


def _client():
    sessionid = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not sessionid:
        raise RuntimeError("sessionid 未配置")
    return DoubaoChatClient(cookies={"sessionid": sessionid}, ms_token="", captcha_handler=None)


@app.post("/v1/images/generations")
async def images_generations(request: Request, authorization: str = Header(default="")):
    if not _authorized(authorization):
        return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
    payload = await request.json()
    prompt = payload.get("prompt", "")
    n = min(int(payload.get("n") or 4), 4)
    async with _client() as client:
        result = await client.generate_image(prompt)
    urls = [img.raw_url or img.ori_url for img in result.images]
    urls = [u for u in urls if u][:n]
    return {"created": int(time.time()), "data": [{"url": u} for u in urls]}


@app.post("/v1/videos/generations")
async def videos_generations(request: Request, authorization: str = Header(default="")):
    if not _authorized(authorization):
        return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
    payload = await request.json()
    prompt = payload.get("prompt", "")
    async with _client() as client:
        result = await client.generate_video(prompt, timeout=320)
    videos = getattr(result, "videos", [])
    if not videos:
        return JSONResponse({"error": {"message": "豆包视频生成失败"}}, status_code=502)
    v = videos[0]
    return {
        "status": "completed",
        "video_url": getattr(v, "video_url", ""),
        "cover_url": getattr(v, "cover_url", ""),
        "duration": getattr(v, "duration", ""),
        "resolution": getattr(v, "resolution", ""),
    }


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return """<!doctype html><html><head><meta charset="utf-8"><title>豆包适配器</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto">
<h3>豆包网页适配器 · 填入 sessionid</h3>
<form method="post" action="/admin/save">
<input type="password" name="password" placeholder="管理密码" style="width:100%;padding:8px;margin:6px 0">
<textarea name="token" rows="3" placeholder="粘贴 doubao.com cookie 中 sessionid 的值" style="width:100%;padding:8px;margin:6px 0"></textarea>
<button type="submit">保存</button>
</form></body></html>"""


@app.post("/admin/save")
async def admin_save(password: str = Form(...), token: str = Form(...)):
    if not ADMIN_PASSWORD or password != ADMIN_PASSWORD:
        return HTMLResponse("管理密码错误", status_code=403)
    TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    return HTMLResponse("<script>alert('已保存');location.href='/admin';</script>")
