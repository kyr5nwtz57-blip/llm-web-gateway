"""媒体路由：生图/生视频在 GLM 与豆包之间做请求级故障转移。

- 生图：豆包（Seedream，网页免费额度）优先，失败自动落 GLM（CogView）；
- 生视频：GLM（CogVideoX）优先（豆包视频技能暂不可用），失败自动落豆包（若恢复即自动生效）。
两个后端都是 OpenAI 形状（/v1/images/generations、/v1/videos/generations），
本服务只做转发与失败转移，不解析响应体。
"""
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

GLM = "http://glm2api:8000"
DOUBAO = "http://doubao2api:8000"
SERVICE_TOKEN = os.getenv("SERVICE_TOKEN", "")

ROUTES = {
    "/v1/images/generations": [(DOUBAO, "doubao"), (GLM, "glm")],
    "/v1/videos/generations": [(GLM, "glm"), (DOUBAO, "doubao")],
}

app = FastAPI(title="media-router")


def _authorized(request: Request) -> bool:
    return bool(SERVICE_TOKEN) and request.headers.get(
        "authorization", ""
    ) == f"Bearer {SERVICE_TOKEN}"


@app.get("/health")
async def health():
    return {"status": "ok", "routes": {k: [n for _, n in v] for k, v in ROUTES.items()}}


@app.post("/v1/images/generations")
async def images(request: Request):
    return await _forward(request, "/v1/images/generations")


@app.post("/v1/videos/generations")
async def videos(request: Request):
    return await _forward(request, "/v1/videos/generations")


async def _forward(request: Request, path: str):
    if not _authorized(request):
        return JSONResponse({"error": {"message": "Unauthorized"}}, status_code=401)
    body = await request.body()
    tried = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(360)) as client:
        for base, name in ROUTES[path]:
            try:
                r = await client.post(
                    base + path,
                    content=body,
                    headers={
                        "Authorization": f"Bearer {SERVICE_TOKEN}",
                        "Content-Type": "application/json",
                    },
                )
            except Exception as exc:
                tried.append(f"{name}:{type(exc).__name__}")
                continue
            if r.status_code < 400:
                return Response(
                    content=r.content,
                    status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"),
                )
            tried.append(f"{name}:{r.status_code}")
    return JSONResponse(
        {"error": {"message": f"所有媒体后端均失败（{'，'.join(tried)}）"}},
        status_code=502,
    )
