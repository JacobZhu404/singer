# 自驾车早 - 自动驾驶晨报公众号自动化项目

## 目录结构

```
/Users/jacob/personal/ad-newsletter/
├── config.json          # 核心配置(微信凭据、D-Chat、板块定义、路径)
├── SKILL.md             # WorkBuddy Skill 定义(触发词、8阶段流程)
├── scripts/
│   ├── push_to_wechat.py     # 微信公众号推送(含质量门禁)
│   ├── dchat_push.py         # D-Chat 群聊推送
│   ├── update_cover.py       # 封面图更新
│   ├── verify_links.py       # 链接可达性验证
│   ├── check_url_quality.py  # URL质量检查(登录墙/聚合器/首页)
│   └── check_freshness.py    # 内容新鲜度检查
├── references/
│   ├── search_keywords_config.md  # 8组搜索关键词配置
│   ├── formatting.md              # 排版规范(品牌色、卡片模板、微信兼容标签)
│   ├── lessons.md                 # 10条踩坑记录
│   ├── dchat_push_format.md       # D-Chat API 格式参考
├── assets/
│   ├── template_links.html        # 链接汇总页模板
├── data/                           # 每日产出(HTML/封面/链接页)
│   └── (运行后生成: 自动驾驶晨报_YYYYMMDD.html, cover_YYYYMMDD.png, links_YYYYMMDD.html)
```

## 快速使用

1. 确保 `config.json` 中的 `app_secret` 正确
2. 在 WorkBuddy 中说"生成自动驾驶晨报"触发 SKILL
3. 或手动运行:
```bash
# 推送到微信草稿箱
python3 scripts/push_to_wechat.py

# 跳过质量门禁
python3 scripts/push_to_wechat.py --skip-qa

# 推送到 D-Chat
python3 scripts/dchat_push.py
```

## 配置说明

所有凭据和路径集中在 `config.json`:
- 微信 AppID/AppSecret → `wechat` 字段
- D-Chat 配置 → `dchat` 字段
- 板块定义 → `newsletter.sections`
- 路径 → `paths` 字段

环境变量可覆盖 config.json 中的任何值(WX_APP_ID, WX_APP_SECRET, WX_HTML_FILE 等)
