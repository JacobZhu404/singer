# 自动驾驶晨报 — 操作手册

所有脚本路径、质量门禁命令、封面流程、推送操作汇总。

---

## 项目路径

- **项目根目录**：`/Users/jacob/personal/ad-newsletter/`
- **数据目录**：`data/` — HTML/封面/链接页全部在此
- **脚本目录**：`scripts/`
- **参考目录**：`references/`
- **资产目录**：`assets/`
- **配置**：`config.json`（微信/D-Chat凭据、板块、路径）
- 环境变量可覆盖 config.json 中的任何值

---

## 脚本一览

| 脚本 | 功能 | 何时运行 |
|------|------|---------|
| `scripts/push_today.py` | 推送今日晨报到微信草稿箱 | 终稿确认后 |
| `scripts/verify_links.py` | Curl所有URL，验证HTTP可达 | 生成HTML后，推送前 |
| `scripts/verify_content.py` | 抓取网页<title>，检查与卡片标题匹配度 | verify_links之后 |
| `scripts/check_url_quality.py` | 检查URL质量(聚合器/登录墙/首页) | 链接验证后 |
| `scripts/check_freshness.py` | 检查内容新鲜度(>30天标记stale) | 质量检查时 |
| `scripts/generate_links_page.py` | 从仪表盘HTML生成格式精美的链接汇总页 | HTML定稿后 |
| `scripts/generate_cover.py` | 一键封面管道（ImageGen→裁剪→字体叠加→900×500） | 推送前 |
| `scripts/update_cover.py` | 更新已有草稿封面图 | 推送后需要更换封面时 |
| `scripts/dchat_push.py` | 推送晨报摘要到D-Chat | 微信推送后 |
| `scripts/push_to_wechat.py` | 微信推送(旧脚本，保留兼容) | 作为备用 |

---

## 质量门禁（严格顺序，不可跳过）

### 1. 链接可达性验证
```bash
python3 scripts/verify_links.py <html文件路径>
```
- 所有URL必须返回200/301/302
- 不可达 > 0 → 禁止推送，需修复或删除对应条目

### 2. 内容真实性校验
```bash
python3 scripts/verify_content.py --html <html文件路径>
```
- 抓取每个URL的 `<title>`，与卡片标题做关键词重叠匹配
- 相似度低于阈值（默认0.25）或HTTP非200 → 标记为可疑
- **注意**：此脚本会产⽣⼀定假阳性（部分站点标题写法不同、反爬、JS加载标题），用于初筛，最终仍需人工复核
- 典型可捕获问题：
  - URL指向完全不相关文章（如游戏新闻）
  - 404/403/登录墙
  - 首页URL冒充文章URL

### 3. URL质量检查
```bash
python3 scripts/check_url_quality.py <html文件路径>
```
- 标记以下类型但不阻止（需人工确认）：
  - 聚合器（papernotes.org等）
  - 登录墙（weibo.com等）
  - 首页（无具体文章路径）

### 4. 内容新鲜度检查
```bash
python3 scripts/check_freshness.py <items.json> --max-age 30
```
- >30天的条目标记为 stale
- 含 `[重大例外]` 标记的不受此限制

---

## 封面图生成

### 正确流程（避免文字拉伸）
1. 使用 `ImageGen` 生成**无文字**的高质量背景图（科技感、自动驾驶主题、深绿渐变）
2. **禁止**让 ImageGen 在图上直接生成文字 → 缩放时会被拉宽/拉扁
3. 用 PIL 按 **900:500 比例**对背景图进行**居中裁剪**（不是非等比拉伸）
4. 缩放为 **900x500** 像素
5. 用 PIL 在 900x500 成品上叠加文字（「自动驾驶晨报」居中上方，日期居中下方）
6. 保存为 `data/cover_YYYYMMDD_fixed.png`
7. `scripts/push_today.py` 会优先使用此封面

### 文字拉伸问题根因
2026-07-06 踩坑：将 1024x1024 生成的带文字图直接 `.resize((900, 500))`，非等比缩放导致字体被压扁。修复：先生成无文字底图，等比裁剪到 900:500 比例，再叠加文字。

### 封面设计要求
- 尺寸：900x500
- 视觉：科技感、未来感，深色背景+品牌绿渐变
- 文件名：`cover_YYYYMMDD_fixed.png`

### 模板复用方案（替代每日ImageGen）
```python
from PIL import Image, ImageDraw, ImageFont
template = Image.open("assets/cover_template.png")
draw = ImageDraw.Draw(template)
draw.text((center_x, date_y), "2026.07.05", fill=(167,243,208), font=font, anchor="mm")
template.save(f"data/cover_YYYYMMDD.png")
```
- 默认：使用模板 + PIL叠加日期
- 例外：每周一或重大节日时，用 ImageGen 生成新主题封面

---

## 微信推送

### 运行
```bash
python3 /Users/jacob/personal/ad-newsletter/scripts/push_today.py
```
跳过质量门禁：
```bash
python3 scripts/push_today.py --skip-qa
```
覆盖配置：
```bash
export WX_APP_SECRET="<新密钥>"
export WX_LINKS_URL="<CloudStudio部署URL>"
python3 scripts/push_today.py
```

### 关键编码要求（严格）
- JSON序列化：**必须** `json.dumps(..., ensure_ascii=False).encode("utf-8")`
- Content-Type：`application/json; charset=utf-8`
- 正文HTML保持原始中文，**不要**转成HTML数字实体
- 标题/摘要保持原始中文，**不要**用 `ensure_ascii=True`

### 常见编码错误
- `ensure_ascii=True` → 标题/摘要显示为 `\u81ea\u52a8...` 字面量
- HTML数字实体 → 正文显示为 `&#128203;` 字面量

### 公众号脚本行为
1. 从 config.json 加载配置（环境变量可覆盖）
2. 质量门禁检查
3. 读取HTML → 解析标题/摘要/正文
4. 生成链接汇总页到 data/ 目录
5. 上传封面图到微信素材库
6. 按5个版块生成公众号兼容HTML正文（不用 `<a>` 标签）
7. 概览区用4列 `<table>`（编号|标题|板块|来源）
8. 设置 `content_source_url` 为 CloudStudio 链接汇总页
9. 调用 draft/add API 创建草稿

---

## D-Chat 推送

```bash
python3 scripts/dchat_push.py
```

### 关键注意事项
1. 必须用 `curl --noproxy '*'` 发送（Python urllib 会走系统代理超时）
2. `text` 和 `form` 必须在消息体顶层，不要包裹在 `interactive` 字段
3. 文本消息 ≤300 字，超长用交互式卡片
4. BotUser 模式不支持 markdown msg_type

---

## 链接汇总页生成

```bash
python3 scripts/generate_links_page.py --html data/自动驾驶晨报_YYYYMMDD.html
```

- 从仪表盘 HTML 中**精确提取**所有卡片（保证链接页与仪表盘内容完全一致）
- 使用与仪表盘相同的视觉风格（header、section、card）
- 输出到 `data/links_YYYYMMDD.html`
- `push_today.py` 已复用此模块，未来推送生成的链接页自动采用新格式

---

## 微信排版限制

1. 正文内 `<a href="外部URL">` 会被微信编辑器过滤成纯文本 → 改用 `content_source_url` 指向链接汇总页
2. CSS Grid/flex 会被公众号编辑器过滤 → 概览用 `<table>` + inline style
3. 微信原生支持 `<p> <section> <table> <span>` 等标签
4. 不支持 `<script> <style>` 和外链CSS
5. `table-layout:fixed` 和 `<colgroup>` 在微信端不一定生效

---

## Python 陷阱

1. `OrderedDict.setdefault()` 在 Python 3.7 不存在 → 用 dict + index 列表代替
2. Python for 循环变量会泄露到外层作用域 → 循环变量名避免与已有变量冲突
3. `requests.post(json=...)` 会对中文字符做 ASCII 转义 → 微信 draft/add 接口**必须**手动编码
4. 不要用 HTML 数字实体替代中文，微信编辑器会原样显示
5. `re.S` 标记让 `.` 匹配换行符，跨行匹配时小心非贪婪 `.*?` 的边界

---

## 链接汇总页

- 从HTML中提取所有卡片URL和标题，生成独立链接汇总页 `data/links_YYYYMMDD.html`
- 部署到 CloudStudio：`workbuddy_cloudstudio_deploy` 工具
- 部署后设置 `WX_LINKS_URL` 环境变量
- CloudStudio 沙箱是临时环境（链接会失效），后续建议迁移到永久存储（OSS/GitHub Pages）

---

## 技能迭代记录

#### 搜索规则迭代
- 2026-07-06: 新增第11组「美国科技企业AI管理动向」、第12组「半导体/芯片理论突破」
- 2026-07-06: 新增「重大事件例外」时效性规则（放宽到7-14天）
- 2026-07-06: 第10组AI基础设施扩展为覆盖中国+美国大厂AI战略

#### 链接与内容质量
- 2026-07-06: 发现7月5日报表存在3条问题链接（小米OneVL错链、华为兜底404、L2国标错链）→ 修复并新增 `verify_content.py`
- 2026-07-06: 新增 `generate_links_page.py`，链接页从仪表盘精确提取并复用仪表盘视觉风格

#### SKILL.md结构优化
- 2026-07-06: 三层重构 — 核心原则(~60行) + 搜索配置(references/) + 操作细节(references/)
