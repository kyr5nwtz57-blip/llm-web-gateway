# 凭证手册：去哪拿、拿什么、换电脑怎么办

> 适用：本机 LLM 网关（免费-adapters）。**本手册不含任何真实凭证值**——仓库里永远不存密码。

## 一、四家网页版凭证（登录取值）

通用前提：**必须在已登录状态下取**（未登录取到的是游客凭证，看着像但没用）。

| # | 平台 | 打开 | 从哪取 | 取哪个字段 |
|---|---|---|---|---|
| 1 | 智谱网页版 | https://chatglm.cn | F12 → Application → Cookies → `chatglm.cn` | `chatglm_refresh_token` 的 Value（`eyJ` 开头长串）。若解出 `is_guest: true` = 游客凭证，无效，需重新登录 |
| 2 | DeepSeek | https://chat.deepseek.com | F12 → Console：`copy(JSON.parse(localStorage.getItem('userToken')).value)` | 剪贴板里即为所需（必须取 `.value`，直接复制整个对象会失败） |
| 3 | Kimi | https://www.kimi.com | F12 → Console：`copy(localStorage.getItem('refresh_token'))` | 剪贴板里即为所需。要 **refresh_token**，不要 access_token（后者几小时失效） |
| 4 | 豆包 | https://www.doubao.com | F12 → Application → Cookies → `doubao.com` | `sessionid` 的 Value（一串字母数字）。有效期约 7–14 天，四家中最短 |

### Console 取法被拦了怎么办（DeepSeek / Kimi 必读）

Chrome / Edge 对"往 Console 粘贴代码"有防自伤拦截（首次不给粘贴），站点反调试也可能把 Console 弄卡。按顺序试，**任一条成功即可**：

1. **解除浏览器粘贴锁（最常用）**：在 Console 里**手动敲**（不能复制）——
   `allow pasting` 回车 → 再粘贴原命令。每个浏览器只做一次。
2. **不开 Console，手抄（永远可用）**：F12 → **Application**（应用）→ Storage → Local Storage → 选中对应站点，按下面取值：
   - DeepSeek：找 key `userToken`，双击它的值 → 是 `{"value":"eyJ...","__version":...}` 这样的 JSON → **只复制 `"value":"` 与末尾 `"` 之间那一段**（不要花括号、不要引号、不要 `value` 字样）。
   - Kimi：找 key `refresh_token`，它的值本身就是凭证，整段复制。
3. **Network 抓包（前两条都不行时的兜底）**：F12 → Network → 刷新页面并随便发一条消息 → 找到 `chat/completion` 之类的请求 → Request Headers 里 `Authorization: Bearer` 后面那串（DeepSeek 用这个）。
4. **Console 一打开就卡死/断点乱跳**（站点反调试）：别硬刚——直接用第 2 条 Application 手抄；或用 Firefox（粘贴只弹一次确认，点"允许"即可）。

> GLM 与豆包本来就走 Application → Cookies，不涉及 Console，不受影响。

取到后：打开运维台 `http://127.0.0.1:3000/ops/` → 对应行「更换凭证」→ 粘贴 → 保存（自动重启适配器）。

## 二、智谱官方 API key（免费 GLM-4.7-Flash，渠道 ch11）

| 项 | 内容 |
|---|---|
| 去哪 | https://open.bigmodel.cn → 左侧「API Keys」 |
| 取什么 | 通用 API key，形如 `32位hex.16位字符` |
| ⚠️ 关键 | **不要**用 Coding Plan 专用 key（base_url 带 `/coding/`，只吃套餐额度，欠费即 1113） |
| 放哪 | 运维台「渠道管理」→ 编辑 `GLM-4.7-Flash官方` 渠道 → 粘贴新 key 保存（约 60 秒生效） |

## 三、运维台登录码 & 网关 key（本机自设，不进 Git）

两者都是**部署时自设**的值，故意不随仓库走（`data/` 已 gitignore）。

| 值 | 存哪 | 换电脑时 |
|---|---|---|
| 运维台登录码 | 容器 `ops-console` 的环境变量 `OPS_ADMIN_PASSWORD`；部署机上也落盘于 `data/ops/.generated-creds.txt` | **自己带走**（密码管理器/纸面），或新机部署时重定一个——它只管本机 `/ops/` 页面，无关任何平台账号 |
| 网关 key（客户端用） | new-api `tokens` 表；部署机 `data/ops/.generated-creds.txt` 亦有一份用于展示 | 同上，自己带走；或新机用 `scripts/init-new-api.py` 重新生成 |

查法（在有值的机器上）：
- 文件：`free-adapters/data/ops/.generated-creds.txt`
- 容器：`docker inspect ops-console` 查 `OPS_ADMIN_PASSWORD` / `GATEWAY_KEY`

## 四、换电脑：带什么、重新取什么

| 东西 | 换电脑时 | 说明 |
|---|---|---|
| 运维台登录码 / 网关 key | 自己带走 | 本机自设，不进 Git；丢了可重定（无找回流程） |
| 各家平台账号（手机号+密码） | 你自己知道即可 | 登录用；短信验证码发到绑定手机 |
| 四家网页凭证 | **新电脑上重新取**（第一节） | 跟电脑无关，跟账号走 |
| 智谱官方 API key | 可带可重建 | 控制台随时再建 |
| 短信验证码 | 手机现收 | 随账号，不随电脑 |

新机凭证填法：照 `docs/fresh-machine-setup.md` 起服务 → 运维台粘贴四家凭证 → 官方 key 配 ch11 渠道。

## 五、三条铁律

1. **必须已登录状态取**——未登录取到的是游客凭证，看着像但没用；
2. **优先 Console 的 `copy(...)`；被拦就按「Console 被拦了怎么办」走 Application 手抄**——手抄时只复制值本身，不要拖选大段（会带 `value : "` 多余字符）；
3. **过期来运维台换，别反复重试**——连续失败会触发风控，可能从"过期"升级成"封号"。
