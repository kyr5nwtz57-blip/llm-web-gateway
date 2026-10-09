#!/usr/bin/env python3
"""一键初始化 new-api：渠道 / 权重 / 网关 token / 自愈参数。

用法（新机器部署第 6 步）：
  python scripts/init-new-api.py --db <one-api.db 路径> \
      --glm-key <glm2api 的 GLM_API_KEY> \
      --deepseek-key <deeperseeker 的 DEEPSEEKER_API_KEY> \
      --kimi-key <kimi2api 的 OPENAI_API_KEY> \
      --doubao-key <doubao2api 的 DOUBAO_API_KEY> \
      [--container new-api] [--gateway-token sk-...]

行为：
- 幂等：重跑会先清掉本脚本管理的渠道（按名称匹配）与 free-chat/free-image 路由，再重建；
- 安全：写入前自动备份 DB 到 <db>.bak-<时间戳>；
- 重要：new-api 在运行时会把它内存里的整行回写 DB，直接改会被覆盖——
  传了 --container 时脚本自动「停→改→启」；没传则要求你自行确保容器已停止。
"""
import argparse
import json
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PRIMARY_PRIORITY = 0
BACKUP_PRIORITY = 0
WEIGHT = 100

ADAPTER_HOSTS = ("glm2api", "deeperseeker", "kimi2api", "doubao2api")

OPTIONS = {
    "RetryTimes": "3",
    "AutomaticDisableChannelEnabled": "true",
    "AutomaticEnableChannelEnabled": "true",
    "ChannelDisableThreshold": "5",
    "AutomaticDisableStatusCodes": "401,403,429",
}


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser(description="初始化 new-api 网关配置")
    ap.add_argument("--db", required=True, help="one-api.db 路径")
    ap.add_argument("--glm-key", required=True)
    ap.add_argument("--deepseek-key", required=True)
    ap.add_argument("--kimi-key", required=True)
    ap.add_argument("--doubao-key", required=True)
    ap.add_argument("--container", default=None, help="new-api 容器名（给了就自动停/启）")
    ap.add_argument("--gateway-token", default=None, help="不传则新生成")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        sys.exit(f"[x] DB 不存在: {db}（先启动过一次 new-api 生成再执行）")

    stopped = False
    if args.container:
        r = docker("stop", args.container)
        if r.returncode != 0:
            sys.exit(f"[x] 停止容器失败: {r.stderr.strip()}")
        stopped = True
        print(f"[*] 已停止容器 {args.container}")
    else:
        print("[!] 未传 --container：请自行确保 new-api 已停止，否则改动会被其内存缓存覆盖")

    backup = db.with_name(db.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copyfile(db, backup)
    print(f"[*] 已备份: {backup.name}")

    con = sqlite3.connect(db)
    now = int(time.time())

    row = con.execute(
        "select id from users where role=100 order by id limit 1"
    ).fetchone()
    if not row:
        sys.exit("[x] users 表里没有管理员——先启动一次 new-api 完成初始化")
    admin_id = row[0]

    # 清理本脚本管理的渠道与路由（幂等重建；按 base_url 指向适配器容器名匹配，
    # 不依赖渠道名称——名称在网页版手工建渠道时会各不相同）
    host_filter = " or ".join(f"base_url like '%{h}%'" for h in ADAPTER_HOSTS)
    old_ids = [r[0] for r in con.execute(f"select id from channels where {host_filter}")]
    if old_ids:
        con.execute(
            f"delete from abilities where channel_id in ({','.join(map(str, old_ids))})"
        )
        con.execute(f"delete from channels where id in ({','.join(map(str, old_ids))})")
        print(f"[*] 清理既有适配器渠道 {len(old_ids)} 条（幂等重建）")
    con.execute("delete from abilities where model in ('free-chat','free-image')")

    def add_channel(name, key, base_url, models, mapping, priority, test_model):
        con.execute(
            """insert into channels(type,key,status,name,weight,created_time,base_url,
               models,model_mapping,'group',auto_ban,priority,test_model)
               values(1,?,1,?,?,?,?,?,?,'default',1,?,?)""",
            (
                key,
                name,
                WEIGHT,
                now,
                base_url,
                models,
                json.dumps(mapping, ensure_ascii=False) if mapping else None,
                priority,
                test_model,
            ),
        )
        cid = con.execute("select id from channels order by id desc limit 1").fetchone()[0]
        con.execute(
            "insert into abilities values('default',?,?,1,?,?,'')",
            (models, cid, priority, WEIGHT),
        )
        return cid

    ids = {}
    for slug, name, url, model, prio, key in [
        ("glm", "智谱网页版(GLM)", "http://glm2api:8000", "glm", PRIMARY_PRIORITY, args.glm_key),
        ("deepseek", "DeepSeek网页版", "http://deeperseeker:4000", "v4.1flash", PRIMARY_PRIORITY, args.deepseek_key),
        ("kimi", "Kimi网页版", "http://kimi2api:8000", "kimi-k2.6", BACKUP_PRIORITY, args.kimi_key),
        ("doubao", "豆包网页版", "http://doubao2api:8000", "doubao", BACKUP_PRIORITY, args.doubao_key),
    ]:
        ids[slug] = add_channel(
            name, key, url, "free-chat", {"free-chat": model}, prio, model
        )
    for slug, name, url, prio, key in [
        ("glm", "GLM生图(CogView)", "http://glm2api:8000", PRIMARY_PRIORITY, args.glm_key),
        ("doubao", "豆包生图(Seedream)", "http://doubao2api:8000", BACKUP_PRIORITY, args.doubao_key),
    ]:
        ids[f"img-{slug}"] = add_channel(name, key, url, "free-image", None, prio, "free-image")

    gw_token = args.gateway_token
    existing = con.execute(
        "select key from tokens where name='free-chat-gateway' and deleted_at is null"
    ).fetchone()
    if existing and not gw_token:
        gw_token = existing[0]
        print("[*] 复用现有网关 token")
    if not gw_token:
        gw_token = "sk-" + secrets.token_hex(24)
        con.execute(
            """insert into tokens(user_id,key,status,name,created_time,accessed_time,
               expired_time,remain_quota,unlimited_quota,used_quota,'group')
               values(?,?,1,'free-chat-gateway',?,?,-1,0,1,0,'default')""",
            (admin_id, gw_token, now, now),
        )
    else:
        con.execute(
            """insert into tokens(user_id,key,status,name,created_time,accessed_time,
               expired_time,remain_quota,unlimited_quota,used_quota,'group')
               select ?,?,1,'free-chat-gateway',?,?,-1,0,1,0,'default'
               where not exists (select 1 from tokens where name='free-chat-gateway')""",
            (admin_id, gw_token, now, now),
        )

    for k, v in OPTIONS.items():
        con.execute(
            "insert into options(key,value) values(?,?) "
            "on conflict(key) do update set value=excluded.value",
            (k, v),
        )

    con.commit()
    con.execute("PRAGMA wal_checkpoint(FULL)")
    con.close()

    print("[*] 渠道创建完成（五家平权：priority=0、权重 100；权重由运维台「自动调权」按实测速度接管）")
    print(f"[*] 自愈参数已写入: {', '.join(OPTIONS)}")
    print()
    print(f"GATEWAY_TOKEN={gw_token}")
    print()
    print("[*] 把这个 token 填给客户端（模型 free-chat / free-image），")
    print("[*] 同时作为 ops-console 容器的 GATEWAY_KEY 环境变量值（页面展示用）")

    if stopped:
        r = docker("start", args.container)
        if r.returncode != 0:
            sys.exit(f"[x] 启动容器失败，请手动 docker start {args.container}: {r.stderr.strip()}")
        print(f"[*] 已启动容器 {args.container}")


if __name__ == "__main__":
    main()
