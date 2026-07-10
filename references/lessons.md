# 踩坑记录

## 1. 标题变量被覆盖(Python for 循环泄露)
- 根因：`for num, title, src in cards:` 中 `title` 覆盖了外层 `title` 变量
- 修复：循环变量改名 `for num, card_title_tmp, src in cards:`
- 教训：Python 3 的 for 循环变量会泄露到外层作用域

## 2. OrderedDict.setdefault() 在 Python 3.7 不存在
- 根因：沙箱默认用 Python 3.7.5，OrderedDict 的 setdefault() 是 3.9 才加的
- 修复：改用 dict + list index 实现分组

## 3. requests 发送微信 JSON POST 导致中文乱码
- 根因（阶段一）：`requests.post(json=...)` 会自动做 `json.dumps(ensure_ascii=True)`，中文变 `\uXXXX`
- 根因（阶段二，2026-07-09 发现）：即便改为 `requests.post(data=json.dumps(obj, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json; charset=utf-8"})`，当前环境下 WeChat 服务器仍会把 UTF-8 中文按 Latin-1 解码，标题/正文变成 `Ã¨Â¿Â...` 类乱码；而 curl / `urllib.request` 发送完全相同的字节和 Content-Type 则正常。
- 修复：JSON POST 统一改用 `urllib.request.Request` + `urllib.request.urlopen`，multipart 图片上传可保留 requests。
- 结论：`ensure_ascii=False` + `utf-8` + `charset=utf-8` 只是必要条件，HTTP 客户端也必须用 `urllib`。

## 4. CSS Grid 在微信编辑器被过滤
- 根因：微信编辑器只保留 inline style 的基本属性，`display:grid` 被删
- 修复：概览区改用 `<table>` + inline style

## 5. table 列宽在微信端不可控
- 根因：微信编辑器对 `<colgroup>` 和 `table-layout:fixed` 的解析不稳定
- 修复：放弃精确列宽控制，改用最简单的 `<table>` + 每列设置 `width`

## 6. `<a href="外部URL">` 在微信正文不可点击
- 根因：微信安全策略会过滤外部链接
- 修复：正文不放 `<a>` 标签，用文章级 `content_source_url` 指向链接汇总页

## 7. 正文内图片 base64 被过滤
- 不在正文内嵌入 base64 图片，所有图片走微信素材库 media_id

## 8. HTML title 被覆盖
- 根因：解析 overview 时某个正则误匹配了 HTML 标题
- 修复：正则用更精确的模式匹配

## 9. 封面图必须上传到素材库
- 微信 draft/add API 不接受图片URL，必须先上传到素材库拿到 media_id
- 上传接口：`/cgi-bin/material/add_material?type=image`
- 没有封面图时 draft/add 会报错 `invalid media_id`

## 10. 微信草稿标题限制
- 最长 64 字节(utf-8)，中文约 20 字
- 超出报错 `errcode: 45003`
- digest 最长 54 字节，约 18 个汉字以内
- 超出报错 `errcode: 45004`

## 11. WeChat API IP 白名单
- 微信 API 要求调用方 IP 在公众号后台的白名单中
- 不在白名单会报: `{"errcode":40164,"errmsg":"invalid ip xxx, not in whitelist"}`
- WorkBuddy 沙箱环境 IP 会变化，每次需要先试，失败后告知用户添加白名单
- mp.weixin.qq.com → 设置 → 安全中心 → IP白名单

## 12. 过期/重复新闻处理原则
- 过期新闻可直接删除，不必找替补 — 精简优先于完整
- 与往期报告去重：按 URL 精确匹配 + 标题模糊匹配（dedup.py）
- 去重数据源：data/outline_*.json（结构化，优先） + HTML 卡片提取（兜底）

## 13. 搜索关键词优化原则
- 小赛道/头部企业直接搜品牌名即可，不必每搜都加"自动驾驶 2026"
- 通用关键词用"智能驾驶"、"智驾"替代"自动驾驶"，覆盖面更广
- 新增聚合门户站点批量扫站(tophub/maomu/huxiu/leiphone等)，溯源原始来源
- 过滤广告和购物导向的导航内容

## 14. 脚本爬虫 date 全空 = 漏报的深层根因
- 现象：07-10 晨报漏掉 小鹏Robotaxi内测 / Optimus Gen3量产 / 第六镜融资 等**已在媒体源出现**的新闻
- 根因：`collect_news.py` 所有 `Candidate(...)` 构造未传 `date` → 300 条脚本候选 date 全空 → 无法进入新鲜度优先队列，LLM 精编阶段根本没见到这些候选（它们本就在新浪tag/36氪/IT之家源里）
- 修复：`collect_news.py` 新增 `_parse_rss_date`(RSS pubDate/updated) + `_parse_date_from_url`(URL路径日期兜底)；RSS 候选日期填充率 0% → ~90%
- 配套：`search_matrix.yaml` v3.2 增补 Robotaxi 业务视角(内测/全员会) / 具身融资量产(Optimus Gen3·第六镜·宇树智元) / NVIDIA机器人开源(GR00T·LeRobot·Hugging Face) / 主流大模型发布雷达(5D, 受限范围)
- 教训：脚本爬虫是"覆盖兜底"，**若候选无日期则形同虚设**；采集阶段必须带日期才能参与新鲜度排序，否则等于没爬

## 15. Robotaxi 漏报 = 查询设计(非数据源) + 爬虫源覆盖有限
- 调查（point 2）：定向搜"小鹏 Robotaxi 内测 何小鹏"→ 5条全中(新浪/腾讯/北京日报/凤凰)，证明 **WebSearch 数据源没问题**；07-10 用的泛搜"Robotaxi 最新进展 商业化"只返回特斯拉迈阿密(7/7)、虎嗅(6/23)等旧/聚合内容，把1天前的小鹏内测埋了
- 结论：漏报根因是「LLM 搜索缺品牌级 Robotaxi 词(query design) + 脚本爬虫 date 曾全空」，二者已分别由 search_matrix v3.2 关键词 与 日期修复 解决。**不是数据源问题**
- 爬虫源覆盖局限：collect_news.py 的 RSS/媒体源列表(Techmeme/36氪/IT之家等)不含腾讯/新浪的 Robotaxi 原文页，其关键词过滤器(TIER1/2)也未命中对这些词 → 小鹏Robotaxi/Optimus/第六镜/NVIDIA×HF 等仍主要靠 **LLM 搜索**(现已靠 v3.2 补词)捞到，爬虫本身不会自动抓。未来若要爬虫自动覆盖，需扩充源列表+关键词，而非仅修日期
- 补充旧报操作：用 `push_today.py --update <media_id>` 走 draft/update（复用 urllib），**勿新建草稿**造成重复；封面复用已上传的 thumb_media_id
- 新增板块：头部大模型公司进展并入新板块"大模型进展"(sec-6)，需同步改 config.json newsletter.sections + generate_html.py SEC_COLORS/SEC_BADGE_COLORS + parse_cards.py SEC_META；generate_wechat.py 对未知板块有 fallback(用 outline 的 name/icon)，不强制改 config 也能渲染
