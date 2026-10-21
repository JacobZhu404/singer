# ============================================================
# 歌者 (Gezhe) - 智能选股系统
# Dockerfile
# ============================================================

FROM python:3.11-slim

# 安装系统依赖（curl 用于健康检查）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY stock_screener/requirements.txt /app/requirements.txt

# 本地与容器共用依赖清单；Gunicorn 仅用于容器服务
RUN pip install --no-cache-dir -r /app/requirements.txt "gunicorn>=23,<24"

# 复制应用代码
COPY stock_screener /app/stock_screener
RUN mkdir -p /app/stock_screener/data

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV PORT=5188

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT}/api/status || exit 1

# 任务状态和 SSE 队列保存在进程内，必须使用单 worker；线程支持并发请求
CMD ["gunicorn", "--bind", "0.0.0.0:5188", "--workers", "1", "--threads", "4", "--timeout", "120", "stock_screener.core.server:app"]

EXPOSE 5188
