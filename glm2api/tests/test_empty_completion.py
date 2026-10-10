# 同形基准：kimi2api/tests/test_kimi_client.py 的
#   test_sync_chat_rejects_done_only_empty_stream / test_stream_chat_rejects_done_only_empty_stream
# 形态：上游"发个 finish 就关流、零正文"（软限流）。断言适配器按失败上报。
# 打补丁前：这两条应【红】（当前返回 200 空成功 / 发 [DONE]）。
# 打补丁后：应【绿】。

import json
import pytest

import app as glm   # glm2api/app.py 顶层导入（从 glm2api/ 目录运行）


# ---------- 生产形状替身 ----------
class _FakeSSEResponse:
    def __init__(self, lines):
        self._lines = list(lines)
    async def aiter_lines(self):
        for ln in self._lines:
            yield ln
    async def aclose(self):
        pass


class _FakeClient:
    async def aclose(self):
        pass


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload
    async def json(self):
        return self._payload


def _sse_lines(*events):
    """编成 _iter_events 认的 `data: {...}` + 空行。"""
    out = []
    for ev in events:
        out.append("data: " + json.dumps(ev, ensure_ascii=False))
        out.append("")
    return out


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setattr(glm, "SERVICE_KEY", "test-key")  # 过 _authorized


def _stub_upstream(monkeypatch, lines):
    async def fake_collect_events(messages, assistant_id=None):
        return _FakeClient(), _FakeSSEResponse(lines)
    monkeypatch.setattr(glm, "_collect_events", fake_collect_events)


async def _read_stream_body(resp):
    chunks = []
    async for c in resp.body_iterator:
        chunks.append(c.decode("utf-8") if isinstance(c, bytes) else c)
    return "".join(chunks)


def _req(stream):
    return _FakeRequest({"messages": [{"role": "user", "content": "hi"}], "stream": stream})


# ---------- 夹具自检（防"应红变假绿"）----------
@pytest.mark.asyncio
async def test_fixture_yields_done_only_shape(monkeypatch):
    # 证夹具确实产出"零正文 + finish"，否则下面的反例是空的。
    lines = _sse_lines({"status": "finish", "parts": []})
    fake = _FakeSSEResponse(lines)
    got = [ev async for ev in glm._iter_events(fake)]
    assert got == [{"status": "finish", "parts": []}]


# ---------- 反例：按失败上报 ----------
@pytest.mark.asyncio
async def test_nonstream_rejects_done_only_empty_stream(monkeypatch):
    _stub_upstream(monkeypatch, _sse_lines({"status": "finish", "parts": []}))
    resp = await glm.chat(_req(False), authorization="Bearer test-key")
    assert resp.status_code == 502, "空回复必须按失败上报，不能返 200 空成功"


@pytest.mark.asyncio
async def test_stream_rejects_done_only_empty_stream(monkeypatch):
    _stub_upstream(monkeypatch, _sse_lines({"status": "finish", "parts": []}))
    resp = await glm.chat(_req(True), authorization="Bearer test-key")
    body = await _read_stream_body(resp)
    assert '"error"' in body      # 有错误帧
    assert "[DONE]" not in body   # 且不得发 [DONE]（否则污染成成功空回复）


@pytest.mark.asyncio
async def test_stream_rejects_closed_without_finish(monkeypatch):
    # 零事件直接关流（连 finish 都没有）——对应 H2b 尾部守卫。
    _stub_upstream(monkeypatch, [])
    resp = await glm.chat(_req(True), authorization="Bearer test-key")
    body = await _read_stream_body(resp)
    assert '"error"' in body
    assert "[DONE]" not in body


# ---------- 正控：守卫不得误伤正常回答 ----------
@pytest.mark.asyncio
async def test_nonstream_positive_control(monkeypatch):
    _stub_upstream(monkeypatch, _sse_lines(
        {"status": "finish", "parts": [{"content": [{"type": "text", "text": "你好"}]}]}))
    resp = await glm.chat(_req(False), authorization="Bearer test-key")
    assert resp["choices"][0]["message"]["content"] == "你好"


@pytest.mark.asyncio
async def test_stream_positive_control(monkeypatch):
    _stub_upstream(monkeypatch, _sse_lines(
        {"status": "finish", "parts": [{"content": [{"type": "text", "text": "你好"}]}]}))
    resp = await glm.chat(_req(True), authorization="Bearer test-key")
    body = await _read_stream_body(resp)
    assert "你好" in body and "[DONE]" in body
