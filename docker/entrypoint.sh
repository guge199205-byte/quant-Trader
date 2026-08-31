#!/bin/sh
# 容器启动兜底：agent/api 依赖的运行时文件若缺失会直接崩溃。
# Docker 对不存在的 bind-mount 源会自动创建"目录"（无法打开），
# 因此 compose 不挂载这些文件，由本脚本在容器内初始化；已存在的用户配置不动。
set -e

# runtime_env*.json：agent/api 的运行环境配置（无凭据，.example 为占位模板）
for f in runtime_env.json runtime_env_cn.json runtime_env_hk.json; do
  if [ ! -f "/app/$f" ]; then
    if [ -f "/app/${f}.example" ]; then
      cp "/app/${f}.example" "/app/$f"
      echo "[entrypoint] $f 缺失 → 已从 example 初始化"
    else
      echo "[entrypoint] ⚠️  $f 缺失且无 .example，请检查镜像"
    fi
  fi
done

# trade_cache.sqlite：交易记录懒构建索引（position.jsonl 派生，可重建）。
# sqlite 打开"目录"会报错，缺失时创建空文件（首次查询自动建表+索引）
if [ ! -f /app/trade_cache.sqlite ]; then
  : > /app/trade_cache.sqlite
  echo "[entrypoint] trade_cache.sqlite 缺失 → 已创建空文件"
fi

exec "$@"
