# 部署参数备忘（占位/示例值，真实 key 在运维台查看）

网络：`docker network create omni-net`

| 容器 | 镜像 | 网络端口 | 宿主端口 |
|---|---|---|---|
| omni-caddy | caddy:2-alpine | 80 | 127.0.0.1:3000 |
| new-api | calciumion/new-api:latest | 3000 | 无 |
| ops-console | ops-console:latest | 8000 | 无 |
| media-router | media-router:latest | 8000 | 无 |
| glm2api | glm2api:latest | 8000 | 无 |
| deeperseeker | deeperseeker:latest | 4000 | 无 |
| kimi2api | kimi2api:latest | 8000 | 无 |
| doubao2api | doubao2api:latest | 8000 | 无 |

关键环境变量（值不写入仓库）：
- glm2api：`GLM_API_KEY`（外部服务 key）、`GLM_ADMIN_PASSWORD`
- doubao2api：`DOUBAO_API_KEY`、`DOUBAO_ADMIN_PASSWORD`
- kimi2api：`OPENAI_API_KEY`、`ADMIN_PASSWORD`、`SESSION_SECRET`、`SECURE_COOKIES=false`
- deeperseeker：`DEEPSEEKER_API_KEY`、`DEEPSEEKER_ADMIN_USER`、`DEEPSEEKER_ADMIN_PASSWORD`
- ops-console：`OPS_ADMIN_PASSWORD`、`GATEWAY_KEY`（new-api 网关 key，用于页面展示）；自动调权可选 `AUTOSCALE_ENABLED` / `AUTOSCALE_INTERVAL_SEC` / `AUTOSCALE_WINDOW_MIN`（默认开、300s、90 分钟）
- media-router：`SERVICE_TOKEN`（与服务 token 同值）
- omni-caddy：无（纯路径路由；媒体转发已移到 media-router）
