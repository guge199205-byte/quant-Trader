#!/bin/bash
# ============================================================
# 容器挂载层自愈（P0-2）：把 BayMax-Trader 的数据挂载恢复为仓库 symlink。
# 用法：bash scripts/fix_mounts.sh   （需要 sudo 权限；会提示输入密码）
# ============================================================
set -e
SRC=/home/zbox/quant-Trader
DST=/home/zbox/BayMax-Trader
echo "==> 挂载层检查（$SRC ↔ $DST）"

need_sudo=0
for d in data logs configs dsh; do
  if [ -L "$DST/$d" ] && [ "$(readlink "$DST/$d")" = "$SRC/$d" ]; then
    echo "   ✅ $d 链接正常"
  else
    echo "   ⚠️  $d 不是指向仓库的链接 → 需要修复"
    need_sudo=1
  fi
done
[ "$need_sudo" = 1 ] || { echo "==> 无需修复"; exit 0; }

echo "==> 需要 root 权限修复（请输入 sudo 密码）"
read -r -s -p "sudo 密码: " PW; echo
for d in data logs configs dsh; do
  if [ -L "$DST/$d" ]; then
    echo "$PW" | sudo -S rm -f "$DST/$d"
  elif [ -d "$DST/$d" ] && [ ! -L "$DST/$d" ]; then
    # 真实目录：先检查是否为空壳（docker 重建产物）；非空且非仓库镜像则备份
    if [ "$(find "$DST/$d" -mindepth 1 | head -1)" = "" ]; then
      echo "$PW" | sudo -S rmdir "$DST/$d"
    else
      mv "$DST/$d" "$DST/$d.stale-$(date +%s)"
    fi
  fi
  echo "$PW" | sudo -S ln -s "$SRC/$d" "$DST/$d"
  echo "   🔧 $d → symlink"
done
# config 目录：确保 backend.yaml 存在（镜像运行时配置）
if [ ! -f "$DST/config/backend.yaml" ]; then
  echo "$PW" | sudo -S cp "$SRC/config/backend.yaml" "$DST/config/backend.yaml" 2>/dev/null \
    || echo "   ⚠️ backend.yaml 缺失且仓库无该文件（请人工提供）"
fi
echo "==> 完成。请重启受影响容器：docker compose -p baymax restart <svc>"