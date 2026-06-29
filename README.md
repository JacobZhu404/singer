# 🎯 歌者 — 智能选股系统

基于同花顺/东方财富数据的全自动股票筛选工具，支持多策略并行扫描、综合评分推荐。「歌者」，以三体文明命名——在资本市场的噪声中，捕捉微弱的秩序信号。

---

## 🚀 快速启动

```bash
cd stock_screener
bash start.sh
```

浏览器访问：**http://127.0.0.1:5188**

---

## 📁 项目结构（stock_screener/）

```
stock_screener/
├── main.py                  # 主入口（Web模式 / 命令行模式）
├── start.sh                 # 本地一键启动脚本
├── Dockerfile               # 容器镜像构建
├── docker-compose.yml       # Docker Compose 配置
├── docker-compose.alibaba.yml  # 阿里云 ECS 覆盖配置
├── docker-compose.tencent.yml  # 腾讯云 CVM 覆盖配置
├── deploy-alibaba.sh        # 阿里云一键部署脚本
├── deploy-tencent.sh        # 腾讯云一键部署脚本
├── requirements.txt
│
├── portfolio/               # 持仓管理器
│   └── manager.py           # 虚拟持仓（买入/卖出/统计）
│
├── strategies/              # 策略目录（共 15 个注册策略）
│   ├── base.py              # 策略基类（模板方法：子类只实现 _evaluate_single_stock）
│   ├── registry.py          # 策略注册器
│   ├── macd_bull.py         # MACD 多头排列
│   ├── strong_stock.py      # 强势股选股
│   ├── td_sequential.py     # 神奇九转 (TD Sequential)
│   ├── right_side.py        # 右侧交易
│   ├── rsi_oversold.py      # RSI 超卖
│   ├── bollinger_lower_bounce.py # 布林下轨反弹（mean reversion）
│   ├── bollinger_breakout.py     # 布林收口突破（波动率扩张）
│   ├── volume_breakout.py   # 量价突破
│   ├── chanlun_strict.py    # 缠论严格版（分型/笔/中枢/背驰/三类买点）
│   ├── momentum.py          # 横截面动量
│   ├── golden_cross.py      # 均线金叉（宽松版）
│   ├── rps_breakout.py      # 欧奈尔 RPS 相对强度突破
│   ├── high_tight_flag.py   # 欧奈尔高紧旗形
│   ├── tail_market.py       # 尾盘强势（日线近似）
│   ├── reversal.py          # 横截面反转（超跌反弹）
│   └── limit_up_gene.py     # 涨停基因（v2，按板块涨停阈值 + 真封板判定）
│
├── core/
│   ├── engine.py            # 筛选引擎（调度+合并+推荐）
│   ├── server.py            # Flask Web 服务
│   ├── observability.py     # 全局事件/错误/耗时收集器
│   └── constants.py         # 集中配置常量
│
├── data/
│   ├── fetcher.py           # 数据接入层（多源：新浪/腾讯/东财，三层缓存）
│   ├── local_cache.py       # 本地 CSV K 线缓存
│   ├── data_sources.py      # 多数据源适配
│   ├── tencent_realtime.py  # 腾讯实时分时
│   ├── realtime_merge.py    # 实时行情合并到日线
│   ├── fundamentals.py      # 基本面（新浪 API）
│   └── tdx_offline.py       # 通达信离线 .day 兜底
│
├── backtest/                # 回测框架（T+1 开盘价 + checkpoint 续跑 + 进程并行）
│   ├── backtest_engine.py
│   ├── backtest_quick.py
│   └── pit_scanner.py       # PIT（point-in-time）扫描
│
├── tools/                   # 离线运维/研究工具
│   ├── factor_portfolio_backtest.py  # 含成本因子组合回测
│   ├── ic_validation.py              # IC/IR 验证
│   └── import_tdx_to_cache.py        # TDX .day → cache CSV
│
├── utils/
│   └── indicators.py        # 技术指标库（pandas 自实现，不依赖 TA-Lib）
│
└── web/
    └── templates/
        └── index.html       # 前端页面（深色主题 + 歌者品牌 + 诊断面板）
```

---

## 📊 选股策略（共 15 个，按语义簇分组）

> 完整列表与详细审计见 `docs/strategies_index.md`；互补性矩阵（Jaccard + 嵌套率）见 `docs/strategies_phase_b.md`；全策略 α 对照见 `docs/backtest_report.md`。

### G1 · 趋势顺势（均线多头 + MACD 健康 + 顺势确认）

| ID | 中文名 | 核心逻辑 |
|---|---|---|
| 📈 `macd_bull` | MACD 多头排列 | DIF/DEA 同时在零轴上，MACD 金叉 + 均线多头 |
| ⚡ `right_side` | 右侧交易 | 突破关键阻力 + 均线金叉 + MA60 + RSI 健康区 |
| 💪 `strong_stock` | 强势股 | 放量 + 红肥绿瘦 + 小阳/缺口 + MACD 零轴 |
| ✨ `golden_cross` | 均线金叉（宽松） | 3 线多头 + RSI |
| 🌅 `tail_market` | 尾盘强势 | 温和涨幅 + 量能 + 均线多头 + 收盘创新高 |
| 🚀 `momentum` | 横截面动量 | 排名前 10% + 量能确认 |

### G2 · 反转超跌（触底 / 超卖 / 均值回归）

| ID | 中文名 | 核心逻辑 |
|---|---|---|
| 🔮 `td_sequential` | 神奇九转 | TD Sequential 买入九转 |
| 📉 `rsi_oversold` | RSI 超卖 | RSI<30 + 回升 + 跌破 MA20 超跌 |
| 📊 `bollinger_lower_bounce` | 布林下轨反弹 | 触及下轨 + 缩量止跌 |
| 🔄 `reversal` | 横截面反转 | 近 5 日跌幅排名 + 当日企稳 |

### G3 · 突破·创新高（量价突破 / RPS / 旗形 / 涨停基因）

| ID | 中文名 | 核心逻辑 |
|---|---|---|
| 🚀 `volume_breakout` | 量价突破 | 量比 ≥2 + 突破 30 日高 |
| 📊 `bollinger_breakout` | 布林收口突破 | 10 日最窄带宽 + 放量突破中轨/逼近上轨 |
| 🏆 `rps_breakout` | RPS 相对强度突破 | 多周期 RPS 加权 + 创阶段新高 + 放量 |
| 🚩 `high_tight_flag` | 高紧旗形 | 旗杆暴涨 + 高位窄幅缩量整理 |
| 🔥 `limit_up_gene` | 涨停基因 v2 | 近期真封板 (`close==high`) + 回撤甜区 + 量价拐头 |

### G4 · 形态独立（与全部其他策略 Jaccard < 8%）

| ID | 中文名 | 核心逻辑 |
|---|---|---|
| 📐 `chanlun_strict` | 缠论严格版 | 分型→笔→中枢→背驰→三类买点 |

⚠️ 涨停次日可能无法实盘买入。回测中已用次日开盘价模拟入场，但仍可能不可得。

---

## 🔢 胜率计算模型

```
基础胜率（各策略历史先验）
  + 评分加成：(score - 50) / 100 × 20%
  + 信号叠加加成：min(信号数 × 3%, 15%)
  + 多策略共识加成：min((命中策略数-1) × 5%, 15%)
= 综合预期胜率（上限90%）
```

---

## 🖥️ API 文档

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web UI |
| `/api/strategies` | GET | 获取策略列表 |
| `/api/screen` | POST | 异步启动筛选 |
| `/api/screen/progress` | GET | 获取筛选进度 |
| `/api/screen/stop` | POST | 停止筛选 |
| `/api/result` | GET | 获取筛选结果 |
| `/api/status` | GET | 系统状态 |
| `/api/portfolio` | GET | 持仓概览 |
| `/api/portfolio/buy` | POST | 虚拟买入 |
| `/api/portfolio/sell` | POST | 虚拟卖出 |
| `/api/portfolio/trades` | GET | 交易历史 |
| `/api/quick_screen` | POST | 同步单策略筛选 |

---

## 🔧 命令行使用

```bash
# 列出所有策略
python main.py list

# 运行所有策略
python main.py screen

# 运行指定策略
python main.py screen -s macd_bull strong_stock

# 创业板 Top5
python main.py screen -m 创业板 -n 5

# 输出 JSON
python main.py screen -s macd_bull --json
```

---

## ➕ 扩展新策略

1. 在 `strategies/` 目录创建新文件，继承 `BaseStrategy`
2. 实现 `_evaluate_single_stock()`（基类已用模板方法封装并发/兜底/计数）
3. 在 `registry.py` 中注册

```python
# strategies/my_strategy.py
from .base import BaseStrategy, StockSignal

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "我的自定义策略"
    base_win_rate = 0.55

    def _evaluate_single_stock(self, code, scanner, name_map, trade_date):
        # 评分逻辑；命中则返回 StockSignal(...)，否则 return None
        if score >= 50:
            return StockSignal(...)
        return None
```

```python
# registry.py 中添加
"my_strategy": {
    "cls": MyStrategy,
    "name": "我的策略",
    "description": "...",
    "tags": ["自定义"],
    "icon": "🎯",
    "weight": 1.0,
}
```

---

## 🚀 云端部署

### Docker 本地开发

```bash
cd stock_screener
docker compose up --build
# 访问 http://localhost:5188
```

### 阿里云 ECS 一键部署

```bash
# 1. 上传代码到阿里云 ECS
rsync -avz ./ root@YOUR_ECS_IP:/opt/gezhe/

# 2. 在 ECS 上执行
bash /opt/gezhe/stock_screener/deploy-alibaba.sh
```

> ⚠️ 部署前需在**阿里云安全组**中手动开放 TCP 5188 端口。

### 腾讯云 CVM 一键部署

```bash
# 1. 上传代码到腾讯云 CVM
rsync -avz ./ root@YOUR_CVM_IP:/opt/gezhe/

# 2. 在 CVM 上执行
bash /opt/gezhe/stock_screener/deploy-tencent.sh
```

> ⚠️ 部署前需在**腾讯云安全组**中手动开放 TCP 5188 端口。

两个脚本均支持：自动安装 Docker、构建镜像、systemd 开机自启、健康检查 + 日志轮转。

---

## 📦 数据源说明

**无需 Token，无 TA-Lib 依赖**，多源接入 + 三层缓存（内存→本地 CSV→网络）：

| 来源 | 用途 | 模块 |
|---|---|---|
| 新浪财经 / 东方财富 | 日 K 线、实时报价 | `data/fetcher.py`、`data/data_sources.py` |
| 腾讯财经 | 实时分时（合并到日 K） | `data/tencent_realtime.py`、`data/realtime_merge.py` |
| 新浪 API | 基本面（PE/PB/市值） | `data/fundamentals.py` |
| baostock | 交易日历兜底 | `data/market_calendar.py` |
| 通达信 .day 离线 | 历史数据兜底（运维一次性导入） | `data/tdx_offline.py`、`tools/import_tdx_to_cache.py` |

> 网络层有限流，`MarketScanner` 实现了并发降级 + 卡死撒手 + 失败兜底；
> 全局事件/错误/耗时通过 `core/observability.py` 收集，Web 端有诊断面板可查。

---

## 🧪 回测 & 因子工具

```bash
# 单策略 / 多策略回测（T+1 开盘入场、含成本、checkpoint 续跑、进程池并行）
.venv/bin/python backtest/backtest_quick.py

# 因子 IC/IR 验证
.venv/bin/python tools/ic_validation.py

# 含成本因子组合回测
.venv/bin/python tools/factor_portfolio_backtest.py
```

回测细节：
- 入场价：信号次日开盘价（T+1）；卖出按 horizon (2/5/10/30 日) close
- 成本：双边滑点 0.02%×2 + 佣金 万0.854×2 + 过户/规费 万1×2 + 印花税 0.05%(仅卖) ≈ **0.127% / 笔**（2026-06-26 实盘费率）
- 基准：中证 1000 (`000852`)，因策略偏中小盘
- 进程池：`spawn` context，避免 fork 状态污染

### 📊 最新回测结果

**全策略 52 周 alpha 对照表见 [`docs/backtest_report.md`](docs/backtest_report.md)**，含：

- 16 策略 × 4 个持有期（2/5/10/30 日）的 alpha 矩阵（全 universe 5052 只 × 51 交易日权威口径）
- 短打型 / 长持型 / 均衡型 画像分类
- 阶段 C 优先级（全量下 tail_market/right_side/rps 居 30d 前三；bollinger_lower_bounce 全场最弱待去留；chanlun_strict 中游、弃用案作废）

`backtest/results/*.json` 是每次回测的原始落盘，按 `.gitignore` 不入库；
人工策展的报告（`docs/backtest_report.md`）随项目演进更新。
