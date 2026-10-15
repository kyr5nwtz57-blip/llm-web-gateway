# 上游版本锚点

本网关依赖的两个适配器来自上游开源项目（不入库），部署时用以下 commit 以确保行为一致：

| 上游 | commit | 拉取命令 |
|---|---|---|
| AmanCode22/deeperseeker | `af802422f4b2d061a7d81aeb3733ea3201eea8b2` | `git clone https://github.com/AmanCode22/deeperseeker.git && git checkout af802422f4b2d061a7d81aeb3733ea3201eea8b2` |
| chopper1026/kimi2api | `7f046d8627f275432f82788a6547bc905038738c` | `git clone https://github.com/chopper1026/kimi2api.git && git checkout 7f046d8627f275432f82788a6547bc905038738c` |

deeperseeker 需要按顺序应用本目录下的补丁（缺一不可）：

```bash
cd deeperseeker
git apply ../upstream-patches/deeperseeker-dockerfile-fix.patch   # 否则 build 失败
git apply ../upstream-patches/deeperseeker-count-tokens.patch     # 补齐 Anthropic count_tokens 端点
cd ..
```

| 补丁 | 作用 |
|---|---|
| `deeperseeker-dockerfile-fix.patch` | 修上游 Dockerfile：COPY 顺序与 venv PATH，否则 `uv sync`/hatchling 元数据失败 |
| `deeperseeker-count-tokens.patch` | 新增 `POST /v1/messages/count_tokens`（同 `/messages/count_tokens`）：Claude Code 每轮调用，缺它则退回本地估算（不致命但协议不完整）。计数复用 `count_tok`（DeepSeek tokenizer），与 `/v1/messages` 的 `usage.input_tokens` 口径一致 |

| `fly143-anthropic-context-compress.patch` | Fly143 Anthropic 路径补上下文压缩（上游只接在 OpenAI 路径，Claude 客户端肥 system prompt 裸发被 DeepSeek 网页端 413 拒）。在 `anthropic_messages` 的 prompt 构建前接 `enforce_context_limit` + `should_compress`（同 OpenAI 路径 proxy.py 接法），含 LLM 摘要/滑动窗口双兜底。锚点：Fly143/deepseek-free-api `48be2bf`（v2.4.2） |

| `fly143-context-estimate-tool-blocks.patch` | 修上下文体积漏算：`_msg_text`/`estimate_message_tokens` 只数 `type=="text"` 块，`tool_use`/`tool_result`/`thinking` 全被算成 0——agentic 长任务据此误判"未超限"，裁剪永不触发，真实 prompt 已达数百万字符 → DeepSeek 挂断/风控。改为统一走 `_content_text` 全类型文本化（实测 tool_result 9KB：旧 2 → 新 9009） |
| `fly143-token-refresh-on-invalid-token.patch` | 补 token 自动重登触发条件：DeepSeek 网页端 token 失效是 **HTTP 200 + `{"code":40003}`**，不是 401，而 relogin 只在 401 时触发 → 失效即硬 502。新增 `_is_invalid_token_body()` 嗅探（非流式/流式两条路径）；同时给 `cfg` 带上 `login_type`/`_password`/`_mobile`/`_area_code`/`_email`——缺这些 relogin 直接放弃（此前自动重登 0 次） |
| `fly143-compression-mode-truncate-alias.patch` | `set_compression_mode` 只认 `truncation`，填 `truncate` 被静默回退 `compress` → 面板/配置文件里看着设了截断，实际还是 LLM 摘要（慢）。归一化两种写法。**注意**：调用点判的是 `mode == "compress"`，非此即截断 |
