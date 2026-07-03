# 格式化与排版参考

## 公众号文章结构(固化,不可改动)

```
Hero 头部(渐变背景 + 日期 + 总条数)
  → 📋 今日概览(4列table: 编号 | 标题 | 所属板块 | 来源)
  → 5大板块详情:
    1. 行业动态(企业动向/融资并购/政策法规/商业化进展/行业峰会)
       ⚠️ 政策法规类包括: 新规发布/征求意见/标准制定/立法进展/责任认定规则
    2. 产品发布(新车型/新功能/新技术发布/OTA更新/新芯片传感器)
    3. 模型发布(新模型/模型更新/benchmark)
    4. 前沿论文(arxiv/CVPR/NeurIPS)
    5. 其他消息(具体事故案例/召回/投诉/人事变动/具身智能/观点评论/吐槽)
       ⚠️ 仅限"具体事故/召回事件",不包括"事故相关新规/法规/标准"
```

## 概览区4列表格规范

| 列 | 内容 | 宽度 | 对齐 |
|----|------|------|------|
| 编号 | 绿色圆底数字(16px) | 20px | 居中 |
| 标题 | 短标题(不超过20字) | 自适应 | 左对齐 |
| 板块 | 5枚举值之一 | 约60px | 左对齐 |
| 来源 | 媒体名称 | 约50px | 左对齐 |

板块枚举值：`行业动态` `产品发布` `模型发布` `前沿论文` `其他消息`

## 卡片模板

每条卡片在微信公众号中用:

```html
<section style="margin:14px 0;padding:14px 16px;border:1px solid #e5e7eb;border-radius:8px;background:#fafafa;">
<p style="margin:0 0 6px;font-size:13px;color:#6b7280;">
  <span style="display:inline-block;min-width:22px;height:22px;line-height:22px;text-align:center;background:#059669;color:#fff;border-radius:50%;font-weight:bold;margin-right:6px;">{序号}</span>
  {来源} · {时间} {维度}
</p>
<p style="margin:6px 0;font-size:16px;font-weight:bold;color:#111827;">{标题}</p>
<p style="margin:6px 0;font-size:14px;color:#374151;line-height:1.7;">{摘要(80-120字)}</p>
</section>
```

## 品牌色系
- 深绿: #064e3b
- 翠绿: #059669
- 亮绿: #10b981
- 浅绿背景: #d1fae5, #ecfdf5, #f0fdf4

## 微信兼容的HTML标签
✅ `<p>` `<section>` `<table>` `<tr>` `<td>` `<span>` `<strong>` `<br>` `<blockquote>`
❌ `<div>` (会被过滤) `<a href="外部URL">` (变纯文本)
❌ `<style>` `<script>` 外链CSS/JS
❌ CSS: `display:grid/flex` `position:fixed/sticky` `transform` `animation` `backdrop-filter` `box-shadow`

## 全局连续编号
跨5个版块统一编号 1~N，不在板块内重新计数。
