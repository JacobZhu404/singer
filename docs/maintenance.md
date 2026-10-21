# 维护记录

后续修复在本文件按日期追加，记录原因、改动、验证及限制；具体差异通过 Git 提交追溯。

## 2026-10-21 — 部署配置与 Web 烟测隔离

### 原因与改动

- Dockerfile 原先维护另一套旧依赖且漏装 Baostock，改为安装 `requirements.txt`，另外安装 Gunicorn（>=23,<24）。
- 筛选任务状态、停止事件及 SSE 订阅均为进程内对象。默认 Gunicorn 从 2 worker 改为 1 worker、4 线程，避免请求落入不同进程后看到不同任务状态。多副本仍需共享状态架构。
- 应用把账户和行情数据写入 `/app/stock_screener/data`，原卷却挂在 `/app/data`。对齐卷挂载路径，并移除对构建上下文外层 `data` 目录的 COPY 依赖。
- Web 烟测改用临时账户及固定行情，覆盖有持仓时的账户估值，不再读取个人账户或请求实时行情。

### 升级提示

重建已有容器前备份 `/app/stock_screener/data` 与旧数据卷，并核对、恢复数据；此前写入容器可写层的持仓不会因修改卷路径自动迁移。见 README 的 Docker 说明。

### 验证

- `.venv/bin/python -m pytest tests/ -q`：151 项全部通过，耗时 7.57 秒。
- `git diff --check`：通过。
- 当前环境没有 Docker 命令，未执行镜像构建或容器运行验证。

### 范围

未修改策略、评分模型或历史回测结果。工作区原有 `core/observability.py`、`data/tencent_batch.py`、`utils/fundamental_flags.py` 改动保留，不纳入本次提交。
