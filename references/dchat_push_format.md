# D-Chat Push Format Reference

## BotUser Authentication

```
Method: POST
URL: http://10.88.128.40:8000/dichat/snitch_openapi_online_lb/v1/message.create
Auth: Basic (app_id:app_secret)  (可选)
Headers:
  Content-Type: application/json
  X-Bot-Type: bot_user
  X-Bot-Id: "519170"
Proxy: MUST use --noproxy '*' with curl
```

## 消息格式

### 交互式卡片 (推荐,用于晨报推送)

⚠️ CRITICAL: `text` 和 `form` 必须在 body 顶层。不要包裹在 `interactive` 字段中!

```json
{
  "username": "zhuzhenxia",
  "msg_type": "interactive",
  "text": "🚗 自动驾驶晨报 · 7月2日",
  "form": {
    "header": {
      "title": {"text": "🚗 自动驾驶晨报 · 7月2日", "sub_text": "精选 30 条"},
      "background": "normal"
    },
    "actions": [
      {"type": "section", "text": [{"value": "今日精选 30 条自动驾驶动态"}]},
      {"type": "divider"},
      {"type": "section", "text": [{"value": "1. 地平线发布 HSD V2.0"}]},
      {"type": "section", "text": [{"value": "2. 特斯拉 Cybercab 量产版测试"}]},
      {"type": "divider"},
      {"type": "section", "text": [{"value": "🔗 点击公众号「阅读原文」查看所有链接"}]}
    ],
    "version": "2.0"
  }
}
```

### 纯文本消息 (≤300 字)

```json
{
  "username": "zhuzhenxia",
  "msg_type": "text",
  "text": "自动驾驶晨报 7月2日: 精选30条动态..."
}
```

## Known Pitfalls

1. **Proxy**: curl 默认走系统代理会超时 → 必须用 `--noproxy '*'`
2. **Text length**: >300 字会导致服务端断连 → 分多条发送或用交互式卡片
3. **markdown msg_type**: BotUser 模式下不支持 → 用 text 或 interactive
4. **interactive wrapper**: 加了 `"interactive": {...}` 包裹层 → 400 错误
5. **Python urllib**: 继承系统代理 → 用 curl (subprocess)
