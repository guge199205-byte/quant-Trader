# 容器编排运维手册（P0-2 自愈与事实源）

## 事实源
- **compose 文件唯一事实源 = 仓库根 `docker-compose.yml`**（BayMax-Trader 下不再维护副本）。
- 所有容器操作统一：
  ```bash
  cd /home/zbox/quant-Trader
  docker compose -p baymax up -d [服务]          # 启动/重建指定服务
  docker compose -p baymax logs -f --tail 100 <svc>
  ```
- 项目名固定 `baymax`（网络 `baymax_default`），与现有容器 label 一致。

## 挂载布局（2026-09-03 事故后修复的现状）
容器 bind 源 = `/home/zbox/BayMax-Trader/*`，其中 4 个必须是指向仓库的符号链接：
```
BayMax-Trader/data    -> /home/zbox/quant-Trader/data      （实盘数据）
BayMax-Trader/logs    -> /home/zbox/quant-Trader/logs      （账本/净值/复盘）
BayMax-Trader/configs -> /home/zbox/quant-Trader/configs
BayMax-Trader/dsh     -> /home/zbox/quant-Trader/dsh       （技能/cordis/root-dsh）
BayMax-Trader/config   = 独立目录（运行时配置 backend.yaml 等，仓库 config/ 镜像一份）
```
> ⚠️ 教训：9/2 一次清理把链接换成空目录 → 任何容器重启即崩（api 缺 backend.yaml、
> dsh 缺 baymax.cordis.yml）。若再出现"容器重启起不来+缺文件"，先跑自愈脚本。

## 一键自愈
```bash
bash scripts/fix_mounts.sh        # 检查并修复上面 4 个 symlink（需 sudo 权限执行）
```

## 端口/服务速查
| 端口 | 服务 | 备注 |
|---|---|---|
| 8000-8003 | quantmind（外部数据平台） | 不要动 |
| 8100-8105 / 8200-8204 / 8300-8304 | mcp-us/cn/hk | 重建会短暂中断 agent 工具 |
| 8091 / 8092 / 8093 | api / arena UI / dsh | 部署走 `scripts/deploy.sh` |

## 重建纪律
1. 先 `bash scripts/fix_mounts.sh` 确认挂载层健康；
2. 需要改代码才 `docker compose -p baymax build <svc>`，纯配置用 up -d；
3. `mcp-*` 重建后必须核对容器内 env（如 JINA/THS key）已注入：
   `docker exec baymax-mcp-us env | grep -c 'JINA_API_KEY=jina_'`
4. dsh 容器重建会丢会话 → 避开盘中，并确认 `/root/.dsh/skills` 挂载后技能齐全
   `docker exec baymax-dsh ls /root/.dsh/skills | wc -l`（≥17）。