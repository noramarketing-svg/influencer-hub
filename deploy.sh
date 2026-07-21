#!/bin/bash
# 一键部署脚本 — 推送到 GitHub 并触发 Render 自动部署
set -e

echo "🚀 正在推送代码到 GitHub..."
git push origin main

echo "📡 触发 Render 部署..."
curl -s -X POST "https://api.render.com/deploy/srv-d9fe7h5aeets73brpsdg?key=lbQCgsw8ry0" > /dev/null

echo "✅ 部署已触发！等待 2-3 分钟即可生效。"
