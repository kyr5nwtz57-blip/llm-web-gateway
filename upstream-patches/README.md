# 上游版本锚点

本网关依赖的两个适配器来自上游开源项目（不入库），部署时用以下 commit 以确保行为一致：

| 上游 | commit | 拉取命令 |
|---|---|---|
| AmanCode22/deeperseeker | `af802422f4b2d061a7d81aeb3733ea3201eea8b2` | `git clone https://github.com/AmanCode22/deeperseeker.git && git checkout af802422f4b2d061a7d81aeb3733ea3201eea8b2` |
| chopper1026/kimi2api | `7f046d8627f275432f82788a6547bc905038738c` | `git clone https://github.com/chopper1026/kimi2api.git && git checkout 7f046d8627f275432f82788a6547bc905038738c` |

deeperseeker 需要应用 `deeperseeker-dockerfile-fix.patch`（本目录），否则 build 失败。
