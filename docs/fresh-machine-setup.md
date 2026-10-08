# 新机器部署清单（从空机器到四家全绿）

> 目标：另一台 Windows/macOS 机器上，用本仓库 + 上游项目，重建出完整的
> 「单入口（127.0.0.1:3000）→ new-api → 四家网页版适配器」网关。
> 全程约 30–50 分钟，其中凭证获取占一半时间（都要你在浏览器里手动操作）。

## 0. 前置条件

- Docker Desktop（Windows 需 WSL2 后端）已安装并运行
- GitHub CLI（`gh`）已登录（用于拉私人仓库）
- 浏览器可正常访问四家平台（豆包 / DeepSeek / 智谱清言 / Kimi）

## 1. 拉取源码

```bash
gh repo clone kyr5nwtz57-blip/llm-web-gateway
cd llm-web-gateway
```

再拉两个不支持容器化发布的上游适配器（版本以当时 HEAD 为准）：

```bash
git clone --depth 1 https://github.com/AmanCode22/deeperseeker.git deeperseeker
git clone --depth 1 https://github.com/chopper1026/kimi2api.git kimi2api
```

**deeperseeker 必须打补丁**（上游原版 Dockerfile 直接 build 会失败：README 缺失导致
hatchling 元数据生成失败、venv 不在 PATH）：

```bash
cd deeperseeker && git apply ../upstream-patches/deeperseeker-dockerfile-fix.patch && cd ..
```

## 2. 建网络与数据目录

```bash
docker network create omni-net
mkdir -p data/{glm2api,doubao2api,deeperseeker,kimi2api,ops}
```

## 3. 构建 5 个镜像

```bash
docker build -t glm2api:latest       glm2api
docker build -t doubao2api:latest    doubao2api
docker build -t ops-console:latest   ops-console
docker build -t deeperseeker:latest  deeperseeker     # 已打补丁
docker build -t kimi2api:latest      kimi2api
```

## 4. 生成服务端密钥（替换下面所有 <占位>）

```bash
python -c "import secrets;print('GLM_API_KEY=sk-glm-'+secrets.token_urlsafe(24))"
# 同理生成：DOUBAO_API_KEY、KIMI 的 OPENAI_API_KEY / SESSION_SECRET、
# DEEPSEEKER_API_KEY、OPS_ADMIN_PASSWORD、GLM_ADMIN_PASSWORD、DOUBAO_ADMIN_PASSWORD、
# DEEPSEEKER_ADMIN_PASSWORD、以及 new-api 网关 token（后面第 6 步建）
```

把生成值记到本机一个临时文件（**不要**放进仓库）。

## 5. 启动 7 个容器

```bash
# 5.1 四家适配器（全部无宿主端口，走 omni-net）
docker run -d --name glm2api --restart unless-stopped --network omni-net \
  -v "$PWD/data/glm2api:/app/data" \
  -e GLM_API_KEY=<GLM_API_KEY> -e GLM_ADMIN_PASSWORD=<GLM_ADMIN_PASSWORD> \
  glm2api:latest

docker run -d --name doubao2api --restart unless-stopped --network omni-net \
  -v "$PWD/data/doubao2api:/app/data" \
  -e DOUBAO_API_KEY=<DOUBAO_API_KEY> -e DOUBAO_ADMIN_PASSWORD=<DOUBAO_ADMIN_PASSWORD> \
  doubao2api:latest

docker run -d --name kimi2api --restart unless-stopped --network omni-net \
  -v "$PWD/data/kimi2api:/app/data" \
  -e ADMIN_PASSWORD=<KIMI_ADMIN_PASSWORD> -e OPENAI_API_KEY=<KIMI_API_KEY> \
  -e SESSION_SECRET=<KIMI_SESSION_SECRET> -e SECURE_COOKIES=false \
  kimi2api:latest

docker run -d --name deeperseeker --restart unless-stopped --network omni-net \
  -v "$PWD/data/deeperseeker:/app/data" \
  -e DEEPSEEKER_API_KEY=<DS_API_KEY> -e DEEPSEEKER_ADMIN_USER=admin \
  -e DEEPSEEKER_ADMIN_PASSWORD=<DS_ADMIN_PASSWORD> -e HOST=0.0.0.0 -e PORT=4000 \
  deeperseeker:latest

# 5.2 new-api（挂宿主数据目录，无宿主端口）
docker run -d --name new-api --restart unless-stopped --network omni-net \
  -v <宿主绝对路径>/new-api-data:/data calciumion/new-api:latest

# 5.3 运维台（挂四家数据卷 + docker.sock，无宿主端口）
docker run -d --name ops-console --restart unless-stopped --network omni-net \
  -v "$PWD/data/glm2api:/data/glm" -v "$PWD/data/doubao2api:/data/doubao" \
  -v "$PWD/data/deeperseeker:/data/deepseek" -v "$PWD/data/kimi2api:/data/kimi" \
  -v //var/run/docker.sock:/var/run/docker.sock \
  -e OPS_ADMIN_PASSWORD=<OPS_ADMIN_PASSWORD> -e GATEWAY_KEY=<新网关token> \
  ops-console:latest

# 5.4 Caddy（唯一宿主端口）
docker run -d --name omni-caddy --restart unless-stopped --network omni-net \
  -p 127.0.0.1:3000:80 -e GLM_ADAPTER_KEY=<GLM_API_KEY即适配器外部key> \
  -v "$PWD/caddy/Caddyfile:/etc/caddy/Caddyfile" caddy:2-alpine
```

## 6. 配置 new-api（渠道/token/自愈参数）——跑脚本

前提：new-api 已至少启动过一次（生成 `one-api.db` 与管理员账号）。

```bash
python scripts/init-new-api.py \
  --db <new-api 数据目录>/one-api.db \
  --glm-key      <glm2api 的 GLM_API_KEY> \
  --deepseek-key <deeperseeker 的 DEEPSEEKER_API_KEY> \
  --kimi-key     <kimi2api 的 OPENAI_API_KEY> \
  --doubao-key   <doubao2api 的 DOUBAO_API_KEY> \
  --container new-api
```

脚本会自动完成：备份 DB → 停容器 → **幂等清理旧适配器渠道**（按 base_url 匹配，
不依赖渠道名）→ 建 4 个 free-chat 渠道（GLM/DeepSeek 主用 priority=10、
Kimi/豆包备用 -1）+ 2 个 free-image 渠道 → 写入自愈参数（RetryTimes=3、
自动禁用/启用、阈值 5、码 401/403/429）→ 打印**网关 token** → 启容器。

把打印出的 `GATEWAY_TOKEN` 记好：它是客户端用的 key，也是 ops-console
的 `GATEWAY_KEY` 环境变量值。**无需进 new-api 网页后台手点**。
（若想复用旧机器配置：直接把旧机 `one-api.db` 拷来当第 6 步产物，
自查 base_url 是容器名、渠道 key 对应即可。）

## 7. 粘贴四家网页登录态（在运维台完成）

打开 `http://127.0.0.1:3000/ops/` → 每行「更换凭证」：

| 平台 | 从哪取 | 取什么 |
|---|---|---|
| 智谱 | chatglm.cn → F12 → Cookies | `chatglm_refresh_token` 的值 |
| DeepSeek | chat.deepseek.com → Console → `copy(JSON.parse(localStorage.getItem('userToken')).value)` | 剪贴板 |
| Kimi | kimi.com → Console → `copy(localStorage.getItem('refresh_token'))` | 剪贴板 |
| 豆包 | doubao.com → F12 → Cookies | `sessionid` 的值 |

保存后运维台自动重启对应适配器。

## 8. 验证（四步全过才算成）

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/            # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/ops/        # 200
# 运维台状态四家全 ok；
curl -s http://127.0.0.1:3000/v1/chat/completions \
  -H "Authorization: Bearer <新网关token>" -H "Content-Type: application/json" \
  -d '{"model":"free-chat","messages":[{"role":"user","content":"回复OK"}]}'   # 应返回内容
```

## 已知坑（都在本清单里处理过了）

1. deeperseeker 上游 Dockerfile 必须打补丁（第 1 步）；
2. Caddyfile 里的 GLM key 用环境变量注入（`GLM_ADAPTER_KEY`），不要硬编码提交；
3. 运维台挂 docker.sock 才能重启适配器——权限足够即可；
4. 所有内部件**不要发布宿主端口**（除 3000）；
5. 登录态会过期：豆包约 7–14 天，其余平台密码变更/登出也会失效——运维台亮红灯时回到第 7 步。
