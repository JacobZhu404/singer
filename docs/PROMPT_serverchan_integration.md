# Prompt: 如何在任意 Python 项目中集成 Server 酱微信推送

以下是一个完整的 Prompt，可以直接发给大模型（如 TRAE、Claude、GPT 等），让它参考此模板在**任何 Python 项目**中集成 Server 酱微信推送功能。

---

## Prompt（直接复制使用）

```
你是一个 Python 工程师。请在当前项目中集成 Server 酱（ServerChan）微信推送功能。

## 背景

Server 酱（ServerChan）是一个免费的微信消息推送服务，通过 HTTP API 将消息发送到个人微信。
官方地址：https://sct.ftqq.com/
API 端点：POST https://sctapi.ftqq.com/{send_key}.send

## 现有配置

- SendKey：SCT375956TM4YqALr4gggIeBXBx2vXAOIp
- API 调用方式：POST JSON，字段为 title（标题）和 desp（描述，支持 Markdown）
- API 返回格式：{"code":0,"message":"success","data":{"error":"SUCCESS",...}}

## 要求

### 1. 创建推送模块

在项目合适位置创建推送模块文件（如 `core/serverchan.py`），包含：

- `ServerChanPusher` 类：
  - `__init__(self, send_key)`：初始化，校验 SendKey 格式（以 SCT 开头）
  - `_send(self, title, desp)`：底层 HTTP 调用，返回 `{"success": bool, "error": str|None}`
  - `push_text(self, title, content)`：推送自定义文本（支持 Markdown）
  - `push_xxx_result(self, result_dict, top_n=20)`：推送项目特定的结果数据，格式化为 Markdown

- `push_result_from_config(result, config_path="serverchan_config.json")` 便捷函数：
  - 从 JSON 配置文件读取 SendKey
  - 调用 pusher 推送结果

### 2. 创建配置文件

在项目根目录创建 `serverchan_config.json`：

```json
{
  "send_key": "SCT375956TM4YqALr4gggIeBXBx2vXAOIp"
}
```

### 3. 推送消息格式要求

- 标题格式：`🎯 {项目名} | {市场/类别} | {日期}`
- 正文使用 Markdown 格式：
  - 每条数据用 `##` 二级标题分隔
  - 关键数值加粗
  - 每条数据之间空一行
- 字段展示：根据业务需要选取（评分、价格、涨跌幅、量比、PE/PB 等）

### 4. 编写说明文档

在 `docs/` 目录下创建 `serverchan.md`，包含：
- 功能概述
- 获取 SendKey 的步骤
- 配置方法（配置文件 vs 代码直接传入）
- 命令行 / Python API 调用示例
- 推送内容格式说明
- 注意事项（免费额度、网络要求、SendKey 安全）
- 故障排查表

### 5. Git 提交

完成所有文件后，执行 git add + commit + push。

## 参考实现

以下是一个已完成的参考项目中的实现，可直接参考：

### serverchan_config.json
```json
{
  "send_key": "SCT375956TM4YqALr4gggIeBXBx2vXAOIp"
}
```

### core/serverchan.py 核心结构
```python
import json, requests
from typing import Dict, Any

class ServerChanPusher:
    API_URL = "https://sctapi.ftqq.com/{send_key}.send"

    def __init__(self, send_key: str):
        if not send_key or not send_key.startswith("SCT"):
            raise ValueError("SendKey 格式错误，应以 SCT 开头")
        self.send_key = send_key
        self.api_url = self.API_URL.format(send_key=send_key)

    def _send(self, title: str, desp: str) -> Dict[str, Any]:
        try:
            resp = requests.post(self.api_url, json={"title": title, "desp": desp}, timeout=15)
            data = resp.json()
            if data.get("code") != 0:
                return {"success": False, "error": data.get("message", "未知错误")}
            return {"success": True, "data": data.get("data", {})}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def push_screen_result(self, result: Dict[str, Any], top_n: int = 20, market_label: str = "全部板块") -> Dict[str, Any]:
        """将业务结果格式化为 Markdown 并推送"""
        picks = result.get("comprehensive_picks", [])[:top_n]
        trade_date = result.get("trade_date", "")
        title = f"🎯 歌者选股 | {market_label} Top{len(picks)} | {trade_date}"
        lines = ["## 筛选参数", f"市场: {market_label}", f"交易日: {trade_date}", "", "## 推荐列表"]
        for i, p in enumerate(picks, 1):
            pct = p.get("pct_chg", 0)
            pct_str = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"
            lines.append(f"## {i}. {p['name']} ({p['ts_code']})")
            lines.append(f"评分: **{p.get('composite_score',0):.0f}** | 价格: {p.get('latest_price')} | 涨幅: {pct_str}")
            lines.append(f"量比: {p.get('volume_ratio',0):.1f}x | PE: {p.get('pe',0):.1f} | PB: {p.get('pb',0):.2f}")
            lines.append("")
        return self._send(title, "\n".join(lines))

    def push_text(self, title: str, content: str) -> Dict[str, Any]:
        return self._send(title, content)

def push_result_from_config(result: Dict[str, Any], config_path: str = "serverchan_config.json") -> Dict[str, Any]:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        send_key = cfg.get("send_key", "")
        if not send_key:
            return {"success": False, "error": f"配置文件中缺少 send_key: {config_path}"}
        pusher = ServerChanPusher(send_key)
        return pusher.push_screen_result(result)
    except FileNotFoundError:
        return {"success": False, "error": f"配置文件不存在: {config_path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

### 微信消息排版效果

推送后微信收到如下格式：

```
标题：🎯 歌者选股 | 全部板块 Top20 | 20260710

正文（Markdown 渲染）：

## 筛选参数
市场: 全部板块
交易日: 20260710

## 推荐列表

## 1. 艾艾精工 (603580)
评分: **158** | 价格: 30.68 | 涨幅: +0.00%
量比: 2.3x | PE: 45.2 | PB: 3.15

## 2. 青龙管业 (002457)
评分: **158** | 价格: 12.35 | 涨幅: +6.54%
量比: 3.1x | PE: 28.7 | PB: 2.08

...
```

## 注意事项

1. 不同项目只需修改 `push_xxx_result()` 方法中的数据字段映射
2. SendKey 已提供，直接写入 `serverchan_config.json` 即可
3. 消息正文（desp）支持 Markdown，但 Server 酱不支持所有 Markdown 语法，建议只用 `##`、`**`、`-` 等基础语法
4. 免费版每天限 20 条消息
```

---

## 使用方法

1. 复制上面的 Prompt 内容
2. 粘贴到任何大模型对话框（TRAE、Claude、GPT 等）
3. 大模型会根据你项目的实际情况，自动适配字段和格式
4. 无需额外修改，SendKey 和 API 细节都已包含在 Prompt 中
