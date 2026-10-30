#!/bin/sh
# 只把网页容器转出去。令牌来自环境变量，不要写进镜像。
set -eu
if [ -z "${NGROK_AUTHTOKEN:-}" ]; then
  echo "NGROK_AUTHTOKEN missing" >&2
  exit 1
fi
if [ -n "${NGROK_DOMAIN:-}" ]; then
  exec ngrok http web:80 --url "$NGROK_DOMAIN" --log stdout
fi
exec ngrok http web:80 --log stdout
