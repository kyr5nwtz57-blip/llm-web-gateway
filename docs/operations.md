# 运维手册（维护、备份、故障）

## 一、容器地图

| 容器 | 作用 | 宿主端口 |
|---|---|---|
| `omni-caddy` | 唯一入口，按路径分发 | **127.0.0.1:3000** |
| `new-api` | 聚合层：渠道/权重/重试/禁用 | 无（内部 3000） |
| `media-router` | 生图/生视频的双平台故障转移 | 无 |
| `ops-console` | 运维台（状态灯/换凭证/渠道管理/自动调权/备份/体检） | 无（经 Caddy 的 `/ops/`） |
| `glm2api` / `deeperseeker` / `kimi2api` / `doubao2api` | 四家网页适配器 | 无 |
| `portainer` | Docker 可视化管理（可选） | 127.0.0.1:9000/9443 |

全部在 Docker 网络 `omni-net` 内以**容器名**互访；宿主只暴露 3000。

## 二、日常巡检（30 秒）

```bash
docker ps --format "{{.Names}}\t{{.Status}}"        # 8 个容器是否都在
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/        # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/ops/    # 200
```

然后打开 `/ops/` 看灯。**不要为了"验证"反复打模型接口** —— 会触发平台风控，用运维台的状态灯判断即可（它读日志和探测，不消耗额度）。

## 三、凭证过期处置（最常见）

症状：运维台某家亮 🔴，或客户端报该家失败。

```
1. 浏览器登录该平台（见 credentials-guide.md 取什么）
2. 打开 http://127.0.0.1:3000/ops/
3. 对应行 → 「更换凭证」→ 粘贴 → 保存（自动重启该适配器）
4. 等 10 秒，点「刷新状态」确认变绿
```

**纪律**：过期就换，**别连点重试**——连续失败会从"过期"升级成风控/封号。

## 四、渠道配置（权重、启停、主备）

数据在 new-api 的 DB：`<new-api数据目录>/one-api.db`，`channels` + `abilities` 两张表。

**实测结论（2026-10-09 死端口探针验证，零上游调用）**：
- `channels`/`abilities` 两表的改动（含 weight、新增/删除渠道）**不用重启**——new-api 的渠道缓存约 60 秒自动从 DB 重载（`CHANNEL_UPDATE_FREQUENCY`）；自动调权与运维台「渠道管理」都靠这条通道；
- `tokens` 表：key 必须存**裸值**（不带 `sk-` 前缀——鉴权时先把 `sk-` 剥掉再查库，存了前缀的 key 永远 Invalid）；裸 key 直插 DB **立即可用**，不用重启；
- 改 **`users` 表（如密码）**仍要停→改→启（登录时会把内存里的行回写 DB，直接改会被覆盖）。

```bash
docker stop new-api
# 改 DB（tokens/users 类）
docker start new-api
```

**关键字段**：
- `channels.status`：1=启用，2=手动禁用，3=自动禁用（失败触发）
- `abilities.enabled` / `abilities.priority` / `abilities.weight`：**new-api 实际按 abilities 路由**，改 channels 必须同步改 abilities，否则不生效
- `priority`：同层（相同值）才按 weight 分流；不同层时高优先级**完全独占**、低优先级不参与
- 五家 free-chat 渠道 priority 均为 0（同层平权），权重由自动调权接管

> 一键重建全部渠道（幂等）：`python scripts/init-new-api.py --db <db路径> --glm-key .. --deepseek-key .. --kimi-key .. --doubao-key .. --container new-api`

## 四之二、自动调权（按实测速度，五家平权起步）

运维台内嵌的控制器，每 5 分钟一轮，**全程零模型调用**（只读 new-api 日志表 + 写权重）：

| 项 | 规则 |
|---|---|
| 数据源 | new-api 的 `logs` 表近 90 分钟 `free-chat` 请求的真实耗时（`use_time` 秒） |
| 起步 | 五家平权，权重各 100 |
| 调整 | 快于池中位数的升权、慢的降权；权重 = 100 × 中位耗时 / 该家耗时，夹在 **20–300** |
| 防抖 | 新旧 50% 平滑 + 最小变化 10 才写；近 90 分钟样本 < 2 的渠道不动 |
| 禁用渠道 | 给最低权重 20（恢复后轻载回池） |
| 生效 | 写 `channels.weight` + `abilities.weight`，约 60 秒经渠道缓存生效（已实测） |
| 面板 | 运维台「自动调权」区：看每家权重/样本数/平均耗时；可点「立即调权一轮」 |
| 停用 | 重建 ops-console 时加 `-e AUTOSCALE_ENABLED=false`；改 `AUTOSCALE_INTERVAL_SEC` / `AUTOSCALE_WINDOW_MIN` 可调周期与窗口 |

> 手动改权重会被下一轮自动调权覆盖——要长期固定权重就先停用自动调权。

## 四之三、渠道管理（中转站 / 官方 API 自助接入，全在运维台）

运维台「渠道管理」区，**不需要命令行**：

| 动作 | 说明 |
|---|---|
| 添加渠道 | 填名称 / Base URL / API Key，选类型（OpenAI 兼容或 Anthropic 兼容），选用途：<br>① **并入 free-chat 轮换**——需填上游真实模型名（如 `gpt-4o-mini`），客户端仍只写 `free-chat`，参与自动调权；<br>② **仅作免费全挂时的备用**——同①但低优先级，只在池内全挂时才被使用（适合按量付费的中转站）；<br>③ **独立模型名**——渠道的模型名照客户端写（如 `claude-sonnet-5`），单独使用不参与轮换 |
| 测试 | 只查该渠道的 `/v1/models`（不消耗额度）；401/403 = key 无效 |
| 编辑 | 改名称 / Base URL / 换 key（GLM 官方 key 换代也在这里做） |
| 停用 / 启用 / 删除 | 即时生效同上（约 60 秒进入路由） |
| 生图渠道 | 独立模型名添加后，`/v1/images/generations` 会按模型名自动转给 new-api 路由（内置名仍走豆包→GLM→new-api 兜底链） |

- Base URL 填 API 根地址（如 `https://api.xxx.com`，不带 `/v1/chat/completions`；带版本号的完整前缀可照抄服务商文档）。
- 删除/误删内置渠道后要恢复：用「添加渠道」按适配器地址（`kimi2api:8000` / `deeperseeker:4000` / `glm2api:8000` / `doubao2api:8000`，类型 OpenAI 兼容）或重跑 `scripts/init-new-api.py`。

## 五、自愈参数（已在库）

| 参数 | 值 | 作用 |
|---|---|---|
| `RetryTimes` | 3 | 失败自动重试次数 |
| `AutomaticDisableChannelEnabled` | true | 失败自动禁用渠道 |
| `AutomaticEnableChannelEnabled` | true | 恢复后自动启用 |
| `ChannelDisableThreshold` | 5 | 连续失败几次禁用 |
| `AutomaticDisableStatusCodes` | 401,403,429 | 触发禁用的状态码 |

## 六、备份与恢复

**自动备份（2026-10-09 起，运维台内嵌）**：每 24 小时用 SQLite 在线备份 API 把 `one-api.db` 备份到 `<new-api数据目录>/backups/`（WAL 安全、**不用停容器**），保留最近 10 份，只裁剪自己命名的旧件。面板「备份」区可：看列表 / 点「立即备份」/ 下载任何一份。

**要备份的只有两样**（代码在 Git，凭证可重取）：
1. **new-api 的 `one-api.db`** —— 含全部渠道/权重/token 配置（已有自动备份）；
2. **各适配器的 `data/` 目录** —— 含四家登录态（可选，能重取）。

```bash
# 备份
docker stop new-api glm2api deeperseeker kimi2api doubao2api
tar czf backup-$(date +%Y%m%d).tgz <new-api数据目录> free-adapters/data
docker start new-api glm2api deeperseeker kimi2api doubao2api
```

**恢复**：把 DB 放回 new-api 数据目录、`data/` 放回原位，重启全部容器即可。

> 从零重建（新机器）：照 `docs/fresh-machine-setup.md`；只想复刻渠道配置：把旧机 `one-api.db` 拷过去即可。

## 七、故障处置速查

| 现象 | 定位 | 处置 |
|---|---|---|
| 运维台整个打不开 | Docker Desktop 是否启动 | 启动后约 1 分钟全部容器自恢复 |
| 不知道哪里坏了 | 面板「一键体检」 | 21 项全本机探测（不打模型接口），照着红叉修 |
| 整个网关不通 | 面板「基础组件」/`docker ps` | 面板点「重启」，或 `docker start <名>` |
| 只有某家不行 | 运维台看该家灯 | 🔴换凭证 / 🟡等限流 / ⚪等额度；可点「看日志」 |
| 全部报 401 | 网关 key 不对 | 跑体检看「单 key 全链路一致」行，比对「连接信息」表 |
| 502/上游错误 | 某适配器挂了 | 面板该行点「看日志」（最近 80 行） |
| 响应普遍变慢 | 正常现象 | 网页逆向固有延迟；自动调权几轮后会压慢家抬快家 |
| 要加中转站 / 换渠道 key | 面板「渠道管理」 | 添加/编辑即可，约 60 秒生效，不用命令行 |
| 生视频报"积分不足" | GLM 每日额度 | 等恢复，非故障 |
| 豆包视频不能用 | 技能协议已变 | 已知项，见 README「已知限制」 |

**看日志**：`docker logs <容器名> --tail 100`

## 八、改代码后怎么上线

```bash
cd <修改的目录>            # 如 glm2api/
docker build -t glm2api:latest .
docker rm -f glm2api && docker run -d --name glm2api ...（参数见 fresh-machine-setup.md 第 5 步）
```

**注意**：`docker restart` **不带新代码**，必须 `build` 后重建容器。改完记得 `git add/commit/push` 同步到仓库。

## 九、Docker 开机自启

已开启（`settings-store.json` 的 `AutoStart=true`）。Windows 上 Docker Desktop 是**登录后自启**，即关机再开机、你登录进桌面后约 1 分钟全部容器自动起来。

## 十、安全边界

- 所有服务只绑定 `127.0.0.1`，**局域网/公网都碰不到**；
- `ops-console` 挂了 docker.sock（用于重启/看日志）与 new-api 数据目录（渠道管理与自动调权读写配置、每日备份）——它是本机工具，别对外暴露；
- 仓库不含任何凭证；`data/` 已 gitignore。
