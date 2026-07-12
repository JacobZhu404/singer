# 已核实来源（人工核验 · 可直接注入 outline）

> 用途：调试 / 手动补稿时，把经人工核验、来源可达且事实准确的条目直接写进
> `data/outline_YYYYMMDD.json`，无需等待自动采集命中。
> 规则：所有 URL 均经 `curl -I` 验证 HTTP 200（知乎等反爬站点会 403，改用 arXiv / 官方媒体）。

更新：2026-07-09 — 由用户提供的 4 条线索逐条核验后入库。

---

## 1. VLA「言行一致性」/ Pinocchio 论文 ⚠️ 注意归因

- **真实来源（权威）**：https://arxiv.org/abs/2607.04681 （HTTP 200）
- 中文可读摘要：https://www.alphaxiv.org/zh/overview/2607.04681v1 （HTTP 200）
- 标题：*Do Vision-Language-Action Models Mean What They Say? On the Role of Faithfulness in Embodied Reasoning*，提交于 2026-07-06。
- **关键事实修正**：此论文 **并非「英伟达官方成果」**。作者为学术团队（Matthew Foutter、Matteo Cercola、Lena Wild、Yunshan Wang、Michelle Li、Daniele Gammelli、**Marco Pavone**）；Marco Pavone 为 Stanford / NVIDIA Research 背景，但论文本身归属学术机构。社交平台（知乎）标题「英伟达最新的 Pinocchio 解析」属引流误读，写入晨报时务必按真实归属表述。
- 核心贡献：区分「功能性推理」与「忠实性推理」，提出学习型评论家 **Pinocchio** 对 VLA 的观察接地 / 步骤连贯性打分，用作 RL 稠密奖励；在自动驾驶基准上忠实性提升 4%~18%。
- 适用版块：前沿论文 / 技术解读。

## 2. 工信部 NVDB 预警：Claude Code 安全后门

- **真实来源（权威媒体，HTTP 200）**：
  - 新浪科技：https://finance.sina.com.cn/tech/roll/2026-07-08/doc-inihapna8067924.shtml
  - 腾讯新闻：https://news.qq.com/rain/a/20260708A071WU00
- 原始发布方：**工业和信息化部网络安全威胁和漏洞信息共享平台（NVDB）**，风险提示发布于 2026-07-08。
- **关键事实**：监测发现 Anthropic 旗下 AI 编程工具 **Claude Code（2.1.91–2.1.196 版本）** 内置监控机制，可在未授权情况下向远程服务器回传用户地域、身份标识等敏感信息；建议立即卸载受影响版本。阿里此前（7/3）已将其列入高风险软件名单，自 7/10 起禁用。
- 适用版块：其他消息（维度建议「🛡️ AI安全」）。与自动驾驶属「泛 AI 生态」相关，用户明确要求收录。

## 3. Grok 4.5 向公众开放（SpaceXAI）

- **真实来源（HTTP 200）**：
  - IT之家：https://www.ithome.com/0/974/035.htm
  - 腾讯新闻：https://news.qq.com/rain/a/20260708A0B3CE00
- **关键事实修正**：新闻中发布主体写作 **「SpaceXAI」**（非 xAI、非 SpaceX 本体）。马斯克确认于 **2026-07-09** 向公众开放 Grok 4.5，称达 **Claude Opus 级别**，但速度更快、Token 效率更高、成本更低；基于 1.5T 参数 V9 架构，并引入 Cursor 编程数据强化工程 / 智能体能力。
- 适用版块：模型发布。

## 4. 全球首个小时级世界模型 · 蚂蚁灵波 LingBot-World 2.0（中国造，已开源）

- **真实来源（HTTP 200）**：
  - ITBear：https://www.itbear.com.cn/html/2026-07/1435968.html
  - 网易：https://www.163.com/dy/article/L1DA18770511ABV6.html
- **关键事实**：蚂蚁灵波科技于 **2026-07-09** 开源新一代实时交互世界模型 **LingBot-World 2.0**（LingBot-World-Infinity），支持 **小时级实时生成**、720p/60fps 输出，业界首次将 Agent 机制引入世界模型；提供 **14B 主模型 + 轻量版** 两个开源版本。对自动驾驶仿真 / 具身智能训练具参考价值。
- 适用版块：模型发布 / 前沿论文（世界模型方向）。

---

### 注入记录

| 日期 | 条目 | 来源 | 版块 |
|------|------|------|------|
| 2026-07-09 | VLA 言行一致 / Pinocchio | arXiv:2607.04681 | 前沿论文 |
| 2026-07-09 | 工信部 NVDB Claude Code 后门预警 | 新浪科技 | 其他消息 |
| 2026-07-09 | Grok 4.5 开放（SpaceXAI） | IT之家 | 模型发布 |
| 2026-07-09 | LingBot-World 2.0 小时级世界模型 | ITBear | 模型发布 |
