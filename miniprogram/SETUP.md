# 自动驾驶晨报 · 小程序接入与部署

本目录是一个**原生微信小程序**，把 `ad-newsletter` 每天产出的 `outline_*.json` 渲染成可读、可转发、可复制原文链接的晨报。

> ⚠️ **与公众号共用数据源、但流水线分叉**：公众号走 `scripts/push_today.py` 草稿推送，小程序走 `scripts/export_mini.py` → 本目录 `data/` → CDN。两者都基于 `data/outline_*.json`，但互不影响、需分别执行。用户说「启动项目」时，**必须先确认是只要公众号、只要小程序、还是 both**，否则容易漏跑一侧或改错管道（详见根目录 `README.md` 与 `SKILL.md` 的「启动项目必问目标」）。

## 目录结构
```
miniprogram/
├── app.json / app.js / app.wxss      # 全局配置与样式
├── project.config.json               # 开发者工具配置（appid 已填 wx4bd59eaceaddd0cf）
├── config.js                         # 运行配置：CDN 地址 + USE_LOCAL 开关
├── sitemap.json
├── data/                             # 由 scripts/export_mini.py 生成
│   ├── index.json                    #   往期清单（日期/标题/条数）
│   ├── outline_YYYYMMDD.json         #   每期内容
│   └── local.js                       #   随包打包映射（开发期用）
├── utils/request.js                  # 数据访问层（本地/网络双模式）
├── components/news-card/             # 卡片组件（点击复制原文链接）
└── pages/
    ├── issue/                        # 今日 + 单期详情（?date=YYYYMMDD）
    └── archive/                      # 往期列表
```

## 本地跑通（无需 CDN）
1. 微信开发者工具 → 导入项目 → 目录选 `ad-newsletter/miniprogram`
2. `project.config.json` 里 `appid` 已是 `wx4bd59eaceaddd0cf`（真实小程序号，非测试号），导入即可预览
3. `config.js` 中 `USE_LOCAL: true`（默认），会直接读 `data/local.js`，无需联网即可看内容
4. 若提示域名校验，开发者工具右上「详情 → 本地设置」勾选「不校验合法域名」（`project.config.json` 已设 `urlCheck:false`）

## 每日更新数据
```bash
python3 scripts/export_mini.py            # 导出全部 outline_*.json
python3 scripts/export_mini.py --date 20260708   # 只导出某期
```
会刷新 `data/` 下三件套。开发期（`USE_LOCAL:true`）刷新后即生效；网络模式需重新上传 CDN。

## 部署到真实环境（3 步）
1. **拿 AppID**：用 `zhuzhenxia@163.com` 登录 mp.weixin.qq.com（已在用就直接登录，忘了走「忘记密码」）。若这个邮箱对应的是**公众号**而非小程序，则需另用邮箱注册一个小程序账号。`appid` 已填好为 `wx4bd59eaceaddd0cf`，无需再替换。若该 AppID 对应的不是你要用的小程序账号（例如该邮箱下是公众号），请在微信公众平台换成正确小程序号的 AppID。
2. **托管 JSON**：把 `miniprogram/data/` 整个目录上传到任意 HTTPS 静态托管（GitHub Pages / 阿里云 OSS / 腾讯云 COS 均可），得到基础地址，例如 `https://<user>.github.io/ad-newsletter`。
3. **改配置 + 加域名白名单**：
   - `config.js`：`DATA_BASE_URL` 改成上面的地址，`USE_LOCAL` 改为 `false`。
   - 小程序后台「开发 → 开发管理 → 开发设置 → 服务器域名」：把该域名加进 **request 合法域名**。
   - 开发者工具「上传」→ 提交审核 → 发布。

## 能力说明（MVP）
- ✅ 今日自动加载最新一期；往期列表可按日期回看
- ✅ 卡片点击复制原文链接（`wx.setClipboardData`）
- ✅ 转发给好友 / 分享到朋友圈（`onShareAppMessage` / `onShareTimeline`）
- ⏳ 二期：新刊订阅消息、历史搜索

## 已知约束
- 小程序正文不能放可点击外链，故采用「卡片点击复制 URL」方案，读者长按粘贴即可访问原文。
- 个人主体小程序不支持 `<web-view>` 业务域名与微信支付，本项目用原生 WXML 渲染，刚好不踩这两个坑。
