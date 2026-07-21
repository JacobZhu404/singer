# Server 酱微信推送功能

## 功能概述

将「歌者」选股结果自动推送到个人微信，通过 [Server 酱](https://sct.ftqq.com/)（ServerChan）服务实现。

## 快速开始

### 1. 获取 SendKey

1. 访问 https://sct.ftqq.com/
2. 使用微信扫码登录
3. 复制页面上的 **SendKey**（格式如 `SCT1234567890...`）

### 2. 配置 SendKey

**方式一：配置文件（推荐，且已随仓库提交）**

项目根目录的 `serverchan_config.json` **已随仓库提交到 Git**，多台机器 `git pull` 即可直接复用，无需每台重新配置：

```json
{
  "send_key": "SCT375956TM4YqALr4gggIeBXBx2vXAOIp"
}
```

如需换成自己的 SendKey，直接改这个文件（或本机用下方「方式二/方式三」临时覆盖，不改动仓库）。

> 该文件**有意提交到仓库**，便于跨机器复用 —— 这是个人项目的主动选择。若仓库对外可见，任何拿到 key 的人只能触发你的微信通知（消耗免费 20 条/日额度），不构成数据泄露；如怀疑滥用，到 https://sct.ftqq.com/ 重新生成 SendKey 即可让旧 key 失效。

**方式二：代码中直接传入**

```python
from core.serverchan import ServerChanPusher

pusher = ServerChanPusher(send_key="SCT1234567890abcdef")
pusher.push_screen_result(result_dict)
```

### 2.5 配置优先级（多个来源同时存在时）

SendKey 按以下顺序解析，**靠前的优先**：

1. 命令行参数 `--send-key`（临时覆盖，最优先）
2. 环境变量 `SERVERCHAN_SENDKEY`
3. 根目录配置文件 `serverchan_config.json`（仓库已自带，默认走这个）

即：仓库里的 `serverchan_config.json` 是默认来源；临时想把结果推到别的号，用 `--send-key SCTxxxx` 或 `SERVERCHAN_SENDKEY=...` 即可，不改仓库文件。

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
3. **SendKey 管理**：`serverchan_config.json` 已随仓库提交（个人项目主动选择，便于多机复用）。不要再把 key 硬编码进 `core/*.py` 等代码；写代码时统一从配置/参数读取。如 key 泄露或想换号，到 https://sct.ftqq.com/ 重新生成，旧 key 立即失效。

## 密钥管理（为什么提交到仓库）

`serverchan_config.json` 包含 Server 酱的 SendKey，**本仓库选择把它提交进版本库**，原因与边界如下：

- **目的**：个人多机协作（家里 / 公司 / 云服务器）`git pull` 后即可直接用，无需每台手动配置。
- **影响面**：SendKey 只能触发你本人的微信通知（消耗免费 20 条/日额度），拿到的第三方无法读取仓库或任何数据，风险仅限于「被刷通知」。
- **如果仓库对外可见**：任何人都能用这个 key 给你发微信通知。介意的话有两个选项：① 本机用 `--send-key` 或 `SERVERCHAN_SENDKEY` 临时覆盖、并把 `serverchan_config.json` 改回自己的 key 后 `git update-index --assume-unchanged` 忽略本地改动；② 或把仓库设为私有。
- **轮换**：怀疑滥用时，登录 https://sct.ftqq.com/ 点「重新生成 SendKey」，旧 key 立即作废，再把新 key 写回 `serverchan_config.json` 提交即可。

> 不要把 key 写死在 `core/serverchan.py` 等代码里——代码里只负责「读配置 / 读参数」，key 只存在于 `serverchan_config.json` 这一处。

## 故障排查

| 问题 | 原因 | 解决 |
|---|---|---|
| 推送失败，提示 `SendKey 格式错误` | SendKey 不以 SCT 开头 | 检查 SendKey 是否正确复制 |
| 推送失败，提示 `配置文件不存在` | `serverchan_config.json` 未创建 | 在项目根目录创建配置文件 |
| 微信收不到消息 | Server 酱服务异常或网络问题 | 检查 https://sct.ftqq.com/ 状态 |
| 量比显示为 0 | 离线数据缺少成交量均值 | 需要合并实时行情后才能计算 |

## 更新日志

- **2026-07-21** `serverchan_config.json` 改为随仓库提交（不再 gitignore），便于多机复用；新增配置优先级与密钥管理说明
- **2026-07-20** 初版上线，支持选股结果自动推送微信
