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

| `fly143-mailcx-domains-api-key.patch` | 修 mail.cx 域名拉取失败：`/v1/config`（system_domains 来源）**新版要求鉴权**，上游请求它时未带 key → 读到 `authentication_required` → 域名列表空 → 面板"获取不到域名"。改为带 `x-api-token`，并过滤 `pro_only` 域名（免费账号选到会注册失败）。另：mail.cx 同 key 同时仅允许一个活跃连接，繁忙返回 **HTTP 429 `{"error":"token_busy"}`**，原先被当成"无验证码"静默 None，批量注册会大面积假失败；改为抛 `MailcxTokenBusy` 并在 `_read_code_loop` 退避重试。注册 worker 本身单线程串行，天然匹配该约束 |

> **重放校验（2026-10-15，对官方 zip 原版）**：解压官方 `deepseek-free-api-2.4.2.zip`
> 后按上表顺序 `git apply` 以下 5 个补丁，`git apply --check` 全部通过：
> `fly143-anthropic-context-compress` → `fly143-context-estimate-tool-blocks` →
> `fly143-token-refresh-on-invalid-token` → `fly143-compression-mode-truncate-alias` →
> `fly143-mailcx-domains-api-key`。
> 即：**官方 v2.4.2 + 这 5 个补丁 == 现网运行代码**。注意 `token-refresh` 改的是
> `proxy.py` 的 `cfg` 构造与 `_do_chat`/`do_stream`，`estimate-tool-blocks` 改的是
> `context_compressor._content_text`（被 `context_manager` 复用），两者有先后依赖。

| `fly143-mailcx-domain-no-pro-filter.patch` | 覆盖上一个 mailcx 补丁的域名部分：不过滤 `pro_only` 域名——mail.cx 的 API key **只有付费用户能签发**，有 key 即默认有 pro 权限，过滤反而砍掉用户的选择；真无权限时由注册阶段自然报错。同时 `default` 域名排列表最前。（含 `/v1/config` 需带 key 与 `token_busy` 退避两项修复） |
