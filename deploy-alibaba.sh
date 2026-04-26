#!/usr/bin/env bash
# ============================================================
# 歌者 — 阿里云 ECS 一键部署脚本
#
# 使用方式：
#   1. 在本地准备好代码（或使用 git clone / rsync 上传）
#   2. 在阿里云 ECS 上运行此脚本：
#      curl -fsSL https://your-server/deploy-gezhe.sh | bash
#   或直接在服务器上：
#      bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USER/gezhe/main/deploy-alibaba.sh)
#
# 前置条件：
#   - Ubuntu 22.04 / CentOS 7+ / Alibaba Cloud Linux 3
#   - 已安装 Docker: https://docs.docker.com/engine/install/
#   - 已开放 5188 端口（安全组规则）
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}═══════════════════════════════════════════════"
echo -e "  歌者 智能选股系统 — 阿里云 ECS 一键部署"
echo -e "═══════════════════════════════════════════════${NC}"

# ── 检测系统 ──────────────────────────────────────────────
detect_os() {
  if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
  else
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
  fi
  echo "检测到操作系统: $OS"
}

# ── 安装 Docker（如未安装）─────────────────────────────────
install_docker() {
  if command -v docker &> /dev/null; then
    echo -e "${GREEN}✓ Docker 已安装: $(docker --version)${NC}"
    return
  fi
  echo -e "${YELLOW}→ 正在安装 Docker...${NC}"

  if [ "$OS" == "ubuntu" ] || [ "$OS" == "debian" ]; then
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg lsb-release
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
  elif [ "$OS" == "centos" ] || [ "$OS" == "almalinux" ] || [ "$OS" == "rocky" ]; then
    yum install -y -q yum-utils
    yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    yum install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
  else
    echo -e "${RED}✗ 不支持的操作系统: $OS${NC}"
    echo "  请手动安装 Docker: https://docs.docker.com/engine/install/"
    exit 1
  fi

  systemctl start docker
  systemctl enable docker
  echo -e "${GREEN}✓ Docker 安装完成${NC}"
}

# ── 拉取最新代码 ──────────────────────────────────────────
pull_code() {
  APP_DIR="/opt/gezhe"
  if [ -d "$APP_DIR/.git" ]; then
    echo -e "${YELLOW}→ 发现已有代码，更新中...${NC}"
    cd "$APP_DIR" && git pull origin main
  else
    echo -e "${YELLOW}→ 克隆代码仓库...${NC}"
    mkdir -p /opt
    # ⚠️ 请替换为你自己的仓库地址
    # git clone https://github.com/YOUR_USER/gezhe.git "$APP_DIR"
    echo -e "${RED}⚠️ 请先将代码上传到 /opt/gezhe，例如："
    echo "   git clone https://github.com/YOUR_USER/gezhe.git /opt/gezhe"
    echo "   或者用 rsync 从本地上传${NC}"
  fi
}

# ── 启动服务 ──────────────────────────────────────────────
deploy_service() {
  APP_DIR="/opt/gezhe"
  if [ ! -d "$APP_DIR/stock_screener" ]; then
    echo -e "${RED}✗ 代码目录不存在: $APP_DIR/stock_screener${NC}"
    exit 1
  fi

  cd "$APP_DIR"

  echo -e "${YELLOW}→ 构建并启动 Docker 容器...${NC}"
  docker compose -f stock_screener/docker-compose.yml \
                 -f stock_screener/docker-compose.alibaba.yml pull
  docker compose -f stock_screener/docker-compose.yml \
                 -f stock_screener/docker-compose.alibaba.yml up -d --build

  echo -e "${YELLOW}→ 等待服务启动...${NC}"
  sleep 8

  # 健康检查
  for i in {1..10}; do
    if curl -sf http://localhost:5188/api/status > /dev/null 2>&1; then
      echo -e "${GREEN}✓ 服务启动成功！${NC}"
      break
    fi
    echo "  等待服务响应... ($i/10)"
    sleep 3
  done
}

# ── 防火墙端口 ────────────────────────────────────────────
open_firewall() {
  echo -e "${YELLOW}→ 检查端口 5188...${NC}"
  if command -v ufw &> /dev/null; then
    ufw allow 5188/tcp 2>/dev/null || true
  fi
  if command -v firewall-cmd &> /dev/null; then
    firewall-cmd --permanent --add-port=5188/tcp 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
  fi
  echo -e "${YELLOW}→ 请确保在阿里云安全组中手动开放 TCP 5188 端口！${NC}"
}

# ── 开机自启 systemd 服务（可选）──────────────────────────
setup_systemd() {
  echo -e "${YELLOW}→ 配置 systemd 开机自启...${NC}"
  cat > /etc/systemd/system/gezhe.service << 'EOF'
[Unit]
Description=Gezhe Stock Screener
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/gezhe
ExecStart=/usr/bin/docker compose -f stock_screener/docker-compose.yml -f stock_screener/docker-compose.alibaba.yml up -d
ExecStop=/usr/bin/docker compose -f stock_screener/docker-compose.yml -f stock_screener/docker-compose.alibaba.yml down
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable gezhe
  echo -e "${GREEN}✓ systemd 服务已配置，开机自启已启用${NC}"
}

# ── 主流程 ────────────────────────────────────────────────
main() {
  detect_os
  install_docker
  open_firewall

  read -p "是否将代码克隆到 /opt/gezhe? (y/N): " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    pull_code
  fi

  deploy_service

  read -p "是否配置 systemd 开机自启? (Y/n): " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Nn]$ ]]; then
    setup_systemd
  fi

  IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
  echo ""
  echo -e "${GREEN}═══════════════════════════════════════════════"
  echo -e "  🎉 部署完成！"
  echo -e "  访问地址: http://${IP}:5188"
  echo -e "  API健康检查: http://${IP}:5188/api/status"
  echo -e "═══════════════════════════════════════════════${NC}"
  echo ""
  echo "常用命令："
  echo "  查看日志:   docker compose -f /opt/gezhe/stock_screener/docker-compose.yml logs -f"
  echo "  重启服务:   docker compose -f /opt/gezhe/stock_screener/docker-compose.yml restart"
  echo "  停止服务:   docker compose -f /opt/gezhe/stock_screener/docker-compose.yml down"
  echo "  更新部署:   bash /opt/gezhe/stock_screener/deploy-alibaba.sh"
}

main "$@"
