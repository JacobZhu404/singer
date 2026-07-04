---
name: daily-ad-newsletter
description: 生成「自驾车早」公众号每日自动驾驶晨报。包含搜索采集、质量门禁、HTML生成、封面设计、微信草稿推送、D-Chat推送全流程。触发词：自动驾驶晨报、生成晨报、今日推送、每日日报。
agent_created: true
---

# 自动驾驶晨报公众号自动化

**项目目录**: `/workspace/ad-newsletter/ad-newsletter/`

所有脚本、配置、参考文件都在此目录下，不受 WorkBuddy 临时工作区清理影响。

在 WorkBuddy 中用作独立主理人(solo mode)，不创建团队即可直接开始。流程分8个阶段执行。

## 配置管理

核心配置集中在 `config.json`：

```json
{
  "wechat": { "app_id": "...", "app_secret": "..." },
  "dchat": { "username": "...", "app_id": "...", "api_url": "..." },
  "paths": { "base_dir": "/workspace/ad-newsletter/ad-newsletter", "output_dir": "..." },
  "newsletter": { "sections": [...], "max_title_bytes": 60, "max_digest_bytes": 54 }
}
```

- **凭据**: config.json 中的值作为默认，环境变量可覆盖
- **路径**: 所有脚本从 config.json 读取路径，或从自身位置推断 `_BASE_DIR`
- **板块配置**: sections 数组定义板块名称、ID、图标

### Git 仓库
- 仓库地址: `https://github.com/JacobZhu404/ad-newsletter` (私有)
- 本地路径: `/workspace/ad-newsletter/ad-newsletter/`
- push 方式: 沙箱内可直接 `cd /workspace/ad-newsletter/ad-newsletter && git push`
- 修改后记得 commit + push 保持同步

## 阶段1：数据采集

读取 `references/search_keywords_config.md` 获取完整关键词配置，按以下10组并行搜索：

1. **头部企业动向** → 标记为「行业动态」或「产品发布」或「模型发布」(按内容区分)
2. **政策法规** → 标记为「行业动态」
3. **行业动态与融资** → 标记为「行业动态」
4. **前沿论文** → 标记为「前沿论文」(⚠️ 必须搜索原始论文链接如 arxiv.org/openaccess.thecvf.com，不要使用聚合器如 papernotes.org)
5. **事故舆情与人事** → 标记为「其他消息」
6. **行业峰会** → 标记为「行业动态」
7. **重点媒体定向搜索** → 按内容归入对应版块
8. **具身智能** → 标记为「其他消息」
9. **聚合门户站点搜索** → 批量扫站提取热点
10. **AI基础设施** → 标记为「行业动态」(AI大模型/算力对智驾产业的影响)

搜索工具：`WebSearch`，每组取前10条结果，去重后汇总。英文关键词用英文搜，中文关键词用中文搜。

搜索结果按以下5个维度标记(对应5个版块):
- 行业动态(企业动向/融资/政策法规/商业化/峰会/AI基础设施) / 产品发布(新车型/新功能/新技术) / 模型发布(新模型/模型更新/benchmark) / 前沿论文(arxiv/CVPR等) / 其他消息(事故/人事/观点/吐槽/具身智能)

### ⚠️ 论文链接规则
- 前沿论文条目的 URL 必须是原始论文链接(arxiv.org/openaccess.thecvf.com/openreview.net)
- papernotes.org 等聚合器链接会被 `check_url_quality.py` 标记为 reject
- 如果搜索结果只有聚合器链接，需要二次搜索原始论文标题+site:arxiv.org

## 阶段2：质量门禁

在生成 HTML 和推送前，运行质量检查脚本：

```bash
# 链接可达性验证
python3 /workspace/ad-newsletter/ad-newsletter/scripts/verify_links.py <dashboard.html>

# URL质量检查(登录墙/聚合器/首页)
python3 /workspace/ad-newsletter/ad-newsletter/scripts/check_url_quality.py <dashboard.html>

# 内容新鲜度检查(可选)
python3 /workspace/ad-newsletter/ad-newsletter/scripts/check_freshness.py <items.json> --max-age 30
```

### 门禁标准
- **链接验证**: 所有URL必须可达(200/301/302)；不可达>0条 → 阻止推送
- **URL质量**: 聚合器/登录墙/首页 → 标记但不阻止(需人工确认)
- **新鲜度**: 超过30天的条目 → 标记为 stale(不阻止)

如果门禁未通过，`push_to_wechat.py` 会拒绝推送。可用 `--skip-qa` 强制推送。

## 阶段3：HTML 仪表盘生成

生成 `data/自动驾驶晨报_YYYYMMDD.html`（输出到项目 data/ 目录）。

### 推文结构(固化)

```
Hero 头部(渐变背景 + 日期 + 总条数)
  → 📋 今日概览(4列table: 编号 | 标题 | 所属板块 | 来源)
  → 5大板块详情:
    1. 行业动态(企业动向/融资并购/政策法规/商业化/行业峰会)
       ⚠️ 政策法规类包括: 新规发布/征求意见/标准制定/立法进展/责任认定规则
    2. 产品发布(新车型/新功能/新技术/OTA更新/新芯片传感器)
    3. 模型发布(新模型/模型更新/benchmark)
    4. 前沿论文(arxiv/CVPR/NeurIPS)
    5. 其他消息(具体事故案例/召回/投诉/人事变动/具身智能/观点评论)
       ⚠️ 仅限"具体事故/召回事件",不包括"事故相关新规/法规/标准"
```

### HTML 排版规则
- 纯单文件 HTML/CSS/JS，零外部资源
- 响应式布局，移动端适配
- 每条卡片含：序号(全局连续)、标题、来源chip、≤60字中文摘要
- 卡片可点击跳转原文(target="_blank" rel="noopener noreferrer")
- 导航栏 sticky 定位 + IntersectionObserver 自动高亮
- 回到顶部按钮
- 品牌色：深绿 #064e3b → 翠绿 #059669 → 亮绿 #10b981

## 阶段4：封面图生成

封面图直接影响文章打开率，**不能用 PIL 简单占位图**。按以下流程生成：

1. 使用 `ImageGen` 工具生成高质量封面(科技感、自动驾驶主题、品牌绿色渐变)
2. 提示词应包含：自动驾驶视觉元素、绿色渐变、日期、中文标题
3. 生成后通过 PIL 裁剪/缩放为 **900x500** 像素
4. 保存到 `data/cover_YYYYMMDD_fixed.png` 或保留 ImageGen 原始文件名
5. `scripts/push_today.py` 会优先使用这些预生成封面，找不到时才降级为占位图

### 封面设计要求
- 尺寸：900x500 (公众号封面图比例)
- 视觉：科技感、未来感，深色背景+品牌绿渐变
- 文字：自动驾驶晨报、当天日期 YYYY.MM.DD
- 5个版块名称标注(行业动态/产品发布/模型发布/前沿论文/其他消息)
- 文件名：`cover_YYYYMMDD_fixed.png` 或 `data/A_futuristic_*_YYYY-MM-DD.png`

### 问题记录
- 2026-07-04曾因图省事用PIL占位图导致封面回退，已修复：push_today.py优先使用ImageGen预生成封面。
- 先 spawn image-creator / ImageGen，封面生成后立刻进入阶段5。

## 阶段5：链接汇总页

从 HTML 中提取所有卡片URL，生成独立链接汇总页 `data/links_YYYYMMDD.html`，部署到 CloudStudio。

```python
# 提取逻辑：抓取 <a href="https://..." class="card"> 中的所有URL和标题
# 部署：workbuddy_cloudstudio_deploy(directory, entry="links_YYYYMMDD.html")
```

部署后，将 CloudStudio URL 设置为环境变量 `WX_LINKS_URL`，供后续推送使用。

## 阶段6：推送到微信草稿箱

运行 `scripts/push_to_wechat.py` 或 `scripts/push_today.py`

### 关键编码要求
- JSON 序列化：**必须**使用 `ensure_ascii=False`
- 请求体：`json.dumps(..., ensure_ascii=False).encode("utf-8")`
- Content-Type：`application/json; charset=utf-8`
- 正文 HTML 保持原始中文，**不要**转成 HTML 数字实体（会导致编辑器显示 `&#xxxxx;`）
- 标题/摘要保持原始中文，**不要**用 `\uXXXX` 转义（会被微信原样显示）

### 常见错误
- `ensure_ascii=True` → 标题/摘要显示为 `\u81ea\u52a8...` 字面量
- HTML 数字实体 → 正文显示为 `&#128203;` 字面量
- PIL 占位图 → 封面回退

### 公众号脚本行为
1. 从 config.json 加载配置(环境变量可覆盖)
2. 质量门禁检查(verify_links + check_url_quality)
3. 读取 HTML → 解析标题/摘要/正文
4. 生成链接汇总页到 data/ 目录(如果未部署)
5. 上传封面图到微信素材库
6. 按5个版块生成公众号兼容的HTML正文(不用 `<a>` 标签)
7. 概览区用4列 `<table>`(编号|标题|板块|来源)，板块枚举值: 行业动态/产品发布/模型发布/前沿论文/其他消息
8. 设置 `content_source_url` 为 CloudStudio 链接汇总页
9. 调用 draft/add API 创建草稿

### 运行命令
```bash
python3 /workspace/ad-newsletter/ad-newsletter/scripts/push_to_wechat.py
# 跳过质量门禁:
python3 /workspace/ad-newsletter/ad-newsletter/scripts/push_to_wechat.py --skip-qa
# 覆盖配置:
export WX_APP_SECRET="<新密钥>"
export WX_LINKS_URL="<CloudStudio部署URL>"
python3 /workspace/ad-newsletter/ad-newsletter/scripts/push_to_wechat.py
```

## 阶段7：D-Chat 推送

将晨报摘要推送到 D-Chat 群聊，通知相关人员。

运行 `scripts/dchat_push.py`

### 前置条件
- 配置: `config.json` 中已填入 dchat 配置
- 环境变量(可选覆盖): `DCHAT_USERNAME`, `DCHAT_APP_ID`, `WX_LINKS_URL`

### 推送内容
- 交互式卡片: 日期+总数+前5条标题+链接入口
- 参考 `references/dchat_push_format.md` 了解 API 格式

### 运行命令
```bash
python3 /workspace/ad-newsletter/ad-newsletter/scripts/dchat_push.py
```

### ⚠️ D-Chat 注意事项
1. 必须用 `curl --noproxy '*'` 发送(Python urllib 会走系统代理超时)
2. `text` 和 `form` 必须在消息体顶层,不要包裹在 `interactive` 字段
3. 文本消息 ≤300 字,超长用交互式卡片
4. BotUser 模式不支持 markdown msg_type

## 阶段8：清理与发布

- 通知用户去公众号后台→草稿箱检查和发布
- 必要时重新推送(删除旧草稿，创建新草稿)
- 更新封面图: `python3 scripts/update_cover.py`(需要 `WX_DRAFT_MEDIA_ID` 环境变量)

## 重要教训(踩坑记录)

### 微信排版限制
1. 正文内 `<a href="外部URL">` 会被微信编辑器过滤成纯文本——改用 `content_source_url` 指向链接汇总页
2. CSS Grid/flex 会被公众号编辑器过滤——概览用 `<table>` + inline style
3. 微信原生支持 `<p> <section> <table> <span>` 等标签
4. 不支持 `<script> <style>` 和外链CSS
5. `table-layout:fixed` 和 `<colgroup>` 在微信端不一定生效
6. HTML实体渲染：`&lt;` 会被正确渲染为 `<`，不要用 unicode

### Python 陷阱
1. `OrderedDict.setdefault()` 在 Python 3.7 不存在 → 用 dict + index 列表代替
2. Python for 循环变量会泄露到外层作用域 → 循环变量名避免与已有变量冲突
3. `requests.post(json=...)` 会对中文字符做 ASCII 转义 → 微信 draft/add 接口**必须**用 `json.dumps(..., ensure_ascii=False).encode("utf-8")` + 显式 `Content-Type: application/json; charset=utf-8`
4. 不要用 HTML 数字实体替代中文（如 `&#128203;`），微信编辑器会原样显示字面量
5. 不要用 `ensure_ascii=True` 发送标题/摘要，微信会原样显示 `\uXXXX` 字面量
6. `re.S` 标记让 `.` 匹配换行符，跨行匹配时小心非贪婪 `.*?` 的边界

### 论文链接
- papernotes.org 等聚合器不是原始论文 → 搜索时加 `site:arxiv.org` / `site:openaccess.thecvf.com`
- `check_url_quality.py` 会将聚合器域名标记为 reject

### D-Chat
- 必须用 curl `--noproxy '*'` → Python urllib 继承系统代理超时
- `interactive` 字段不要包裹在额外的 `"interactive": {...}` 层 → 400 错误

### 封面设计
- image-creator 生成的封面可能带「图片由AI生成」水印
- 封面图必须上传到微信素材库拿到 media_id 才能使用

### 链接汇总页
- CloudStudio 沙箱是临时环境，链接会失效
- 后续如果配置了永久存储(OSS/COS/GitHub Pages)，替换 `content_source_url`

## 路径规范(⚠️ 无硬编码)

所有路径从 config.json 或脚本位置推断，不硬编码:
- **项目根目录**: `/workspace/ad-newsletter/ad-newsletter/` (config.json `paths.base_dir`)
- **输出目录**: `data/` — HTML/封面/链接页全部在此
- **脚本目录**: `scripts/` (所有 Python 脚本)
- **参考目录**: `references/` (关键词/排版/教训/D-Chat格式)
- **资产目录**: `assets/` (模板)
- **配置**: `config.json` (微信/D-Chat凭据、板块、路径)
- 环境变量可覆盖 config.json 中的任何值
- 默认 HTML 和封面图均从 `data/` 目录读写，不扫描其他目录

## 参考文件
- `config.json` — 核心配置(微信凭据、D-Chat、板块、路径)
- `references/search_keywords_config.md` — 完整搜索关键词配置(含论文原始链接规则)
- `references/formatting.md` — 排版规范
- `references/lessons.md` — 踩坑记录
- `references/dchat_push_format.md` — D-Chat 推送格式参考
- `scripts/push_to_wechat.py` — 微信推送脚本(含质量门禁)
- `scripts/verify_links.py` — 链接可达性验证
- `scripts/check_url_quality.py` — URL质量检查(聚合器/登录墙/首页)
- `scripts/check_freshness.py` — 内容新鲜度检查
- `scripts/update_cover.py` — 封面图更新脚本
- `scripts/dchat_push.py` — D-Chat 推送脚本
- `assets/template_links.html` — 链接汇总页模板

## 覆盖企业列表
Waymo, Tesla, 百度Apollo, 小马智行(Pony.ai), 文远知行(WeRide), 滴滴自动驾驶, 元戎启行, 华为, 大疆(卓驭), 小鹏, 理想, 蔚来, 小米, Momenta, 地平线, 英伟达(NVIDIA), Uber, Stellantis, Wayve

## 重点关注的公众号
智东西, 高工智能汽车, 懂车帝, 小马智行公众号, APPSO, 智驾最前沿, 量子位, 机器之心, 雷锋网, 晚点新闻, RoboX, 芝能汽车, 智驾具身数据挖掘, 车东西, 新智驾, 深蓝AI, 智车科技, 自动驾驶之心, 晚点Auto
