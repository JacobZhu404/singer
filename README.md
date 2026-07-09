# 自驾车早 - 自动驾驶晨报公众号自动化项目

## 目录结构

```
/Users/jacob/personal/ad-newsletter/
├── config.json          # 核心配置(微信凭据、板块定义、路径)
├── SKILL.md             # WorkBuddy Skill 定义(触发词、流程)
├── scripts/
│   ├── push_today.py         # 微信公众号推送(含质量门禁)
│   ├── generate_html.py      # outline.json → Jinja2 → HTML
│   ├── generate_cover.py     # 封面图生成
│   ├── generate_links_page.py # 链接汇总页生成
│   ├── dedup.py              # 历史去重
│   ├── collect_news.py       # 热榜兜底爬虫
│   ├── search_checklist.py   # 搜索覆盖率追踪
│   ├── verify_links.py       # 链接可达性验证
│   ├── verify_content.py     # 内容真实性校验
│   ├── check_url_quality.py  # URL质量检查
│   ├── check_freshness.py    # 内容新鲜度检查
│   ├── update_cover.py       # 封面图更新
│   └── utils/                # 共享模块 & 备用脚本
│       ├── parse_cards.py         # HTML卡片解析
│       ├── outline_schema.py      # Pydantic outline schema
│       └── (legacy scripts)       # 旧脚本备用
├── references/
│   ├── search_matrix.yaml         # 12组搜索+3热榜配置(单一数据源)
│   ├── operations.md              # 操作手册
│   ├── formatting.md              # 排版规范
│   ├── lessons.md                 # 踩坑记录
├── assets/
│   ├── template_links.html        # 链接汇总页模板
├── data/                           # 每日产出
│   ├── test/                      # 测试产出(隔离)
│   └── (outline_*.json, 自动驾驶晨报_*.html, links_*.html, cover_*)
```

## 快速使用

1. 确保 `config.json` 中的 `app_secret` 正确
2. 在 WorkBuddy 中说"生成自动驾驶晨报"触发 SKILL
3. 或手动运行:
```bash
# 推送到微信草稿箱
python3 scripts/push_today.py

# 跳过质量门禁
python3 scripts/push_today.py --skip-qa
```

## 配置说明

所有凭据和路径集中在 `config.json`:
- 微信 AppID/AppSecret → `wechat` 字段
- 板块定义 → `newsletter.sections`
- 路径 → `paths` 字段

环境变量可覆盖 config.json 中的任何值(WX_APP_ID, WX_APP_SECRET, WX_HTML_FILE 等)
