#!/usr/bin/env bash
# ============================================================
# 歌者 — 腾讯云 CVM 一键部署脚本
#
# 使用方式：
#   1. 上传代码到腾讯云 CVM（或使用 Coding/Git 拉取）
#   2. 在服务器上执行：
#      bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USER/gezhe/main/deploy-tencent.sh)
#
# 腾讯云特性：
#   - 使用腾讯云容器镜像服务 CCR（免费）存储镜像
#   - 支持腾讯云 TKE 集群（可选）
#   - 云服务器 CVM 单节点最简单
#
# 前置条件：
#   - Tencent Cloud Linux 3 / Ubuntu 22.04
#   - 已安装 Docker: https://cloud.tencent.com/document/product/1207/91550
#   - 安全组已开放 5188 端口
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}═══════════════════════════════════════════════"
echo -e "  歌者 智能选股系统 — 腾讯云 CVM 一键部署"
echo -e "═══════════════════════════════════════════════${NC}"

# ── 检测 Docker ───────────────────────────────────────────
if command -v docker &> /dev/null; then
  echo -e "${GREEN}✓ Docker 已安装: $(docker --version)${NC}"
else
  echo -e "${YELLOW}→ 正在安装 Docker...${NC}"
  curl -fsSL https://get.docker.com | sh
  systemctl start docker
  systemctl enable docker
  echo -e "${GREEN}✓ Docker 安装完成${NC}"
fi

# ── 获取代码目录 ──────────────────────────────────────────
APP_DIR="/opt/gezhe"
read -p "代码目录路径 [默认 /opt/gezhe]: " INPUT_DIR
APP_DIR="${INPUT_DIR:-$APP_DIR}"

if [ ! -d "$APP_DIR/stock_screener" ]; then
  echo -e "${YELLOW}→ 代码目录不存在，请先上传代码到 $APP_DIR${NC}"
  echo "  推荐方式："
  echo "  1. Git:   git clone https://github.com/YOUR_USER/gezhe.git $APP_DIR"
  echo "  2. Rsync: rsync -avz ./ $USER@YOUR_CVM_IP:$APP_DIR/"
  exit 1
fi

cd "$APP_DIR"

# ── 腾讯云 CCR 镜像构建（可选）─────────────────────────────
build_image() {
  read -p "是否使用腾讯云 CCR 镜像仓库? (y/N): " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}→ 腾讯云 CCR 镜像仓库配置${NC}"
    read -p "  CCR 实例 ID（如 tcr-xxxxxxxx）: " CCR_ID
    read -p "  地域（如 ap-shanghai）: " REGION
    read -p "  命名空间: " NAMESPACE
    IMAGE_TAG="${REGION}.ccr.tencentyun.com/${NAMESPACE}/gezhe:latest"

    echo -e "${YELLOW}→ 构建镜像并推送到 CCR...${NC}"
    docker build -t gezhe:latest -f stock_screener/Dockerfile .
    docker tag gezhe:latest "$IMAGE_TAG"
    docker login --username=ccr --password="${CCR_TOKEN}" "${REGION}.ccr.tencentyun.com" 2>/dev/null || true
    docker push "$IMAGE_TAG"
    echo -e "${GREEN}✓ 镜像已推送: $IMAGE_TAG${NC}"
    echo "  → 在 docker-compose.yml 中替换 image: gezhe:latest 为 $IMAGE_TAG"
  else
    echo -e "${YELLOW}→ 使用本地构建（默认）${NC}"
  fi
}

# ── 启动服务 ──────────────────────────────────────────────
deploy() {
  echo -e "${YELLOW}→ 构建并启动服务...${NC}"
  docker compose -f stock_screener/docker-compose.yml \
                 -f stock_screener/docker-compose.tencent.yml up -d --build

  echo -e "${YELLOW}→ 等待服务启动...${NC}"
  sleep 8

  for i in {1..10}; do
    if curl -sf http://localhost:5188/api/status > /dev/null 2>&1; then
      echo -e "${GREEN}✓ 服务启动成功！${NC}"
      return 0
    fi
    echo "  等待服务响应... ($i/10)"
    sleep 3
  done
  echo -e "${RED}✗ 服务启动超时，请检查日志${NC}"
  docker compose -f stock_screener/docker-compose.yml logs
  return 1
}

# ── 开机自启 ─────────────────────────────────────────────
setup_boot() {
  read -p "配置腾讯云自动化助手（云监控）开机自启? (Y/n): " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    cat > /etc/systemd/system/gezhe.service << EOF
[Unit]
Description=Gezhe Stock Screener
After=network.target docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${APP_DIR}
ExecStart=/usr/bin/docker compose -f stock_screener/docker-compose.yml -f stock_screener/docker-compose.tencent.yml up -d
ExecStop=/usr/bin/docker compose -f stock_screener/docker-compose.yml -f stock_screener/docker-compose.tencent.yml down
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable gezhe
    echo -e "${GREEN}✓ 已配置 systemd 开机自启${NC}"
  fi
}

# ── 主流程 ────────────────────────────────────────────────
main() {
  build_image
  deploy
  setup_boot

  IP=$(curl -s ifconfig.me 2>/dev/null || curl -s api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
  echo ""
  echo -e "${CYAN}═══════════════════════════════════════════════"
  echo -e "  🎉 部署完成！"
  echo -e "  访问地址: http://${IP}:5188"
  echo -e "  API健康检查: http://${IP}:5188/api/status"
  echo -e "═══════════════════════════════════════════════${NC}"
  echo ""
  echo "常用命令："
  echo "  查看日志:   docker compose -f $APP_DIR/stock_screener/docker-compose.yml logs -f"
  echo "  重启服务:   docker compose -f $APP_DIR/stock_screener/docker-compose.yml restart"
  echo "  停止服务:   docker compose -f $APP_DIR/stock_screener/docker-compose.yml down"
  echo "  腾讯云CVM安全组: 前往控制台 → 云服务器 → 安全组 → 添加入站规则 TCP:5188"
}

main "$@"
