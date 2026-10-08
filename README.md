# LLM Web Gateway（本机自用）

把四家国产大模型**网页版**（豆包 / DeepSeek / 智谱清言 / Kimi）的会话能力，转成 OpenAI / Anthropic 兼容 API，
经 new-api 聚合为**单 key 单端点**，供本机 Claude Code 等 harness 使用。

> 仅供个人学习研究、本机自用。自动化访问网页版违反各平台用户条款，有封号风险；请勿用于商业或对外服务。

## 文档导航

| 我要… | 看这份 |
|---|---|
| **日常使用**（客户端怎么配、能力怎么调、出问题看哪） | [docs/usage.md](docs/usage.md) |
| **维护运维**（巡检、换凭证、备份、故障处置、改码上线） | [docs/operations.md](docs/operations.md) |
| **取凭证**（去哪拿、拿什么、换电脑怎么办） | [docs/credentials-guide.md](docs/credentials-guide.md) |
| **新机器部署**（从空机器到跑起来的分步清单） | [docs/fresh-machine-setup.md](docs/fresh-machine-setup.md) |
| **部署参数备忘**（容器/端口/环境变量） | [docs/deploy-notes.md](docs/deploy-notes.md) |

## 架构

```
harness / CLI（一个 key，模型名统一 free-chat）
        │  http://127.0.0.1:3000
        ▼
   Caddy（唯一入口，回环）
        ├─ /ops/*                        → ops-console（统一运维台）
        ├─ /v1/images/generations        → media-router（豆包优先→GLM兜底）
        ├─ /v1/videos/generations        → media-router（GLM优先→豆包兜底）
        └─ 其余                           → new-api
                                        ├─ glm2api        智谱清言（对话/工具/检索/生图/生视频）
                                        ├─ deeperseeker   DeepSeek 网页版（对话/工具/推理）
                                        ├─ kimi2api       Kimi 网页版（对话/检索）
                                        └─ doubao2api     豆包网页版（对话/生图）
```

调用哪个能力（聊天 / 生图 / 生视频）由 LLM 按任务自动选择端点，
用户侧只认一个模型名 `free-chat`（历史别名 `free-image` 兼容保留）。

- 全部容器在 Docker 网络 `omni-net` 内以容器名互访；宿主只暴露 `127.0.0.1:3000`。
- new-api 侧：五家**同层参与** `free-chat`，按权重分流 —— GLM-4.7-Flash 官方(50) > 智谱网页 GLM-5.3(40) ≈ DeepSeek 网页(40) > 豆包网页(35) ≈ Kimi 网页(35)；
  某家失败自动禁用、恢复后自动回池（RetryTimes=3，401/403/429 触发）。
- 生图/生视频走 `media-router`：生图豆包优先→GLM 兜底；生视频 GLM 优先→豆包兜底。

## 组件来源

| 目录 | 内容 | 来源 |
|---|---|---|
| `glm2api/` | 智谱清言适配器（自写，协议参考 HelloGML） | 本仓库 |
| `doubao2api/` | 豆包适配器（自写壳）+ `doubao2api/` 内嵌客户端包 | 壳：本仓库；客户端包：fork 自 [wangchuxiaoji-oss/doubao2api](https://github.com/wangchuxiaoji-oss/doubao2api)（Apache-2.0，LICENSE 随附） |
| `ops-console/` | 统一运维台（状态灯/换凭证/连接信息） | 本仓库 |
| `caddy/` | 入口路由 | 本仓库 |
| `deeperseeker` / `kimi2api` 镜像 | DeepSeek / Kimi 适配器 | 上游开源项目，见部署章节 |

## 部署

1. 构建镜像：`docker build` 三个目录（`glm2api` / `doubao2api` / `ops-console`），
   `deeperseeker` 与 `kimi2api` 按各自上游 README 构建。
2. 建网络 `docker network create omni-net`，按 `docs/deploy-notes.md` 中的 docker run 参数启动全部容器。
3. 凭证（各家网页登录态，**只在运维台里粘贴**，落盘在本机 `data/` 卷，不入库）：
   - 智谱：`chatglm.cn` cookie `chatglm_refresh_token`
   - DeepSeek：`chat.deepseek.com` localStorage `userToken`（JSON 取 `.value`）
   - Kimi：`kimi.com` localStorage `refresh_token`
   - 豆包：`doubao.com` cookie `sessionid`
4. 打开 `http://127.0.0.1:3000/ops/` 查看状态灯；四家全绿即可用。

## 已知限制

- 各家登录态会过期（豆包 sessionid 约 7–14 天）——过期时运维台亮红灯，重新取凭证粘贴即可。
- 联网检索仅 GLM/Kimi 支持；`free-chat` 随机路由，DeepSeek 无检索。
- GLM 生视频有每日积分上限。
- 豆包生视频技能路由已变，适配器暂未接通（备用项，不影响主用）。

## 免责声明

本项目不含任何破解或绕过付费机制的功能，仅将个人账号网页端的既有能力转为本机接口。
使用前请确认符合各平台服务条款；因使用产生的任何后果由使用者自行承担。
