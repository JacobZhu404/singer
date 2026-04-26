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
├── strategies/              # 策略目录
│   ├── base.py              # 策略基类
│   ├── registry.py          # 策略注册器
│   ├── macd_bull.py         # MACD多头排列
│   ├── strong_stock.py      # 强势股选股
│   ├── td_sequential.py     # 神奇九转
│   ├── right_side.py        # 右侧交易
│   ├── rsi_oversold.py      # RSI超卖
│   ├── bollinger_bands.py   # 布林带反弹
│   └── volume_breakout.py   # 量价突破
│
├── core/
│   ├── engine.py            # 筛选引擎（调度+合并+推荐）
│   └── server.py            # Flask Web 服务
│
├── data/
│   └── fetcher.py           # 数据接入层（同花顺/东财 API）
│
├── utils/
│   └── indicators.py        # 技术指标库（MACD/KDJ/RSI/布林/TD等）
│
└── web/
    └── templates/
        └── index.html       # 前端页面（深色主题 + 歌者品牌）
```

---

## 📊 选股策略（共8个）

### 📈 策略1: 趋势共振策略（MACD + 均线金叉）
- MA5 上穿 MA10 当日金叉
- MA5 > MA10 > MA20 > MA60 均线多头排列
- MACD 零轴上方（ DIF > 0 且 DEA > 0）
- DIF > DEA（持续金叉）
- MACD 柱连续3日放大

### 💪 策略2: 强势股选股
- 放量上涨（量比 > 1.5）
- 红肥绿瘦：涨时量大、跌时量小（近10日统计）
- 五连小阳（连续5日小阳线，涨幅均≤3%）
- 跳空缺口：今日最低 > 昨日最高
- MACD 零轴以上

### 🔮 策略3: 神奇九转 (TD Sequential)
- 连续9天收盘价 < 4天前（买入九转完成）
- 价格上穿近2日高点确认
- 成交量放大确认
- MACD 金叉辅助

### ⚡ 策略4: 右侧交易
- 突破近20日高点
- 突破时放量（量比 > 1.5）
- MA5 上穿 MA20（均线金叉）
- 股价站上 MA60
- RSI 在 50~72 健康区间

### 📉 策略5: RSI 超卖策略
- RSI(14) < 30 超卖区
- RSI 从超卖区回升至 30~40
- 价格 < 20日均线超跌

### 📊 策略6: 布林带反弹策略
- 价格触及布林下轨（±3%内）
- 布林下轨反弹
- 缩量止跌（量比<0.6）

### 🚀 策略7: 量价突破策略
- 量比 ≥ 2倍（明显放量）
- 价格突破30日高点
- 量价齐升共振

### 🔥 策略8: 涨停基因（高风险）
- 近30日内有涨停记录
- 涨停后最大回撤 < 15%
- ⚠️ 注意：涨停次日可能无法买入

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
2. 实现 `screen()` 方法
3. 在 `registry.py` 中注册

```python
# strategies/my_strategy.py
from .base import BaseStrategy, ScreenResult

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "我的自定义策略"
    base_win_rate = 0.55

    def screen(self, stock_list, scanner=None) -> ScreenResult:
        # 实现筛选逻辑
        ...
```

```python
# registry.py 中添加
"my_strategy": {
    "cls": MyStrategy,
    "name": "我的策略",
    "description": "...",
    "tags": ["自定义"],
    "icon": "🎯",
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

数据通过 **Tushare Pro API** 接入，涵盖：
- 同花顺口径：`moneyflow_ths`、`limit_list_ths`
- 东方财富口径：`moneyflow_dc`
- 原始行情：`daily`、`daily_basic`
- 打板数据：`limit_step`、`hm_detail`
