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

# 安装 Python 依赖（关键包）
RUN pip install --no-cache-dir \
    Flask==3.0.0 \
    flask-cors==4.0.0 \
    requests==2.31.0 \
    pandas==2.1.0 \
    numpy==1.26.0 \
    akshare==1.12.0 \
    tushare==1.4.0 \
    flask-sock==0.7.0 \
    gunicorn==21.2.0

# 复制应用代码
COPY stock_screener /app/stock_screener
COPY data /app/data

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV PORT=5188

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT}/api/status || exit 1

# 启动命令：开发用 Flask，生产用 Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5188", "--workers", "2", "--threads", "4", "--timeout", "120", "stock_screener.core.server:app"]

EXPOSE 5188
