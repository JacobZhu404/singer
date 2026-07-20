# Server 酱微信推送功能

## 功能概述

将「歌者」选股结果自动推送到个人微信，通过 [Server 酱](https://sct.ftqq.com/)（ServerChan）服务实现。

## 快速开始

### 1. 获取 SendKey

1. 访问 https://sct.ftqq.com/
2. 使用微信扫码登录
3. 复制页面上的 **SendKey**（格式如 `SCT1234567890...`）

### 2. 配置 SendKey

**方式一：配置文件（推荐）**

在项目根目录创建 `serverchan_config.json`：

```json
{
  "send_key": "SCT1234567890abcdef"
}
```

> 该文件已被加入 `.gitignore`，不会提交到 Git，避免泄露密钥。

**方式二：代码中直接传入**

```python
from core.serverchan import ServerChanPusher

pusher = ServerChanPusher(send_key="SCT1234567890abcdef")
pusher.push_screen_result(result_dict)
```

### 3. 命令行推送

在 `main.py screen` 命令后追加 `--push` 参数：

```bash
python main.py screen -m 主板 -n 20 --push
```

或全板块：

```bash
python main.py screen -m all -n 20 --push
```

### 4. Python API 调用

```python
from core.engine import ScreenEngine
from core.serverchan import push_result_from_config

engine = ScreenEngine(market='主板', top_n=20)
result = engine.get_recommendation(strategies=['macd_bull', 'strong_stock'])

# 从配置文件读取 SendKey 并推送
push_result_from_config(result, config_path="serverchan_config.json")
```

## 推送内容格式

微信消息包含以下信息：

- **标题**：`🎯 歌者选股 | {市场} Top{N} | {日期}`
- **每只股票的字段**：
  - 综合评分
  - 最新价
  - 涨跌幅
  - 量比
  - PE（市盈率）
  - PB（市净率）

## 注意事项

1. **免费额度**：Server 酱免费版每天可推送 20 条消息，选股结果推送 1 次算 1 条。
2. **网络要求**：推送需要访问 `https://sctapi.ftqq.com/`，请确保网络可达。
3. **SendKey 安全**：不要将 SendKey 硬编码在代码中或提交到 Git，使用配置文件方式管理。

## 故障排查

| 问题 | 原因 | 解决 |
|---|---|---|
| 推送失败，提示 `SendKey 格式错误` | SendKey 不以 SCT 开头 | 检查 SendKey 是否正确复制 |
| 推送失败，提示 `配置文件不存在` | `serverchan_config.json` 未创建 | 在项目根目录创建配置文件 |
| 微信收不到消息 | Server 酱服务异常或网络问题 | 检查 https://sct.ftqq.com/ 状态 |
| 量比显示为 0 | 离线数据缺少成交量均值 | 需要合并实时行情后才能计算 |

## 更新日志

- **2026-07-20** 初版上线，支持选股结果自动推送微信
