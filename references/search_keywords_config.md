# 自动驾驶晨报 - Web 搜索关键词配置

生成每日晨报时，按以下关键词分组搜索，每轮取前5-10条结果。

---

## 时效性规则

- **默认**：收录近 **3天** 内的新闻
- **重大事件例外**（以下情况放宽到 **7-14天**）：
  - 全球首创/里程碑式法规标准（如ADS GTR全球首个自动驾驶法规）
  - 超百亿元级别融资/IPO
  - 颠覆性技术突破/新范式发布
  - 重要国际会议/标准制定结果公布
  - 标记方式：在 outline 中备注 `[重大例外: 原因]`

---

## 搜索分组 → 版块映射

| 版块 | ID | 含义 | 对应搜索组 |
|------|-----|------|-----------|
| 行业动态 | sec-1 | 企业动向/融资并购/政策法规/商业化/峰会/合作联盟/AI基础设施/半导体突破 | 1+2+3+6+10+12 |
| 产品发布 | sec-2 | 新车型/新功能/新技术/OTA更新/芯片发布 | 1(产品类) |
| 模型发布 | sec-3 | 新模型/模型更新/benchmark/技术架构发布 | 1(模型类) |
| 前沿论文 | sec-4 | arxiv/CVPR/NeurIPS/ICML论文 | 4 |
| 其他消息 | sec-5 | 事故/人事/吐槽/具身智能/观点评论/大厂AI政策(含美国大厂) | 5+8+11 |

**标记规则**：同一条新闻可能属于多个版块，取最核心的1个：
- 企业融资/并购/合作 → 行业动态（不是产品发布）
- OTA更新/新功能上线 → 产品发布（不是模型发布）
- 新模型论文发布 → 前沿论文（优先，即使也是模型）
- 事故/人事/吐槽 → 其他消息（不要分到行业动态）
- 具身智能 → 其他消息（与自动驾驶交叉但非核心）

---

## 第零组：聚合热榜数据源（优先级最高，使用 WebFetch）

按优先级降序 fetch 以下站点首页，提取与智驾相关的标题，再二次搜索溯源原始来源：

### P1 — 日热榜 tophub.today
- URL: https://tophub.today
- 覆盖源：微博热搜/知乎热榜/微信24h热文/百度实时/36氪/IT之家/虎嗅/机器之心/量子位/Readhub/掘金AI/少数派
- 提取关键词：智能驾驶/自动驾驶/robotaxi/Waymo/Tesla/萝卜快跑/小马智行/文远知行/华为智驾/地平线/小鹏XNGP/蔚来/理想/小米智驾/端到端/世界模型/具身智能
- 操作：fetch首页 → 遍历所有板块 → 标题含上述关键词即收录 → 二次搜索标题获取原始URL

### P2 — Buzzing buzzing.cc（海外信息差）
- URL: https://buzzing.cc（主页聚合）或子站：hn.buzzing.cc / tech.buzzing.cc / news.buzzing.cc
- 提取关键词：autonomous driving/robotaxi/Waymo/Tesla FSD/Cruise/self-driving/AV
- 操作：fetch子站 → 提取中文标题和英文标题 → 含关键词即收录 → 点击跳转原文获取URL

### P3 — NewsNow newsnow.busiyi.world（综合新闻补充）
- URL: https://newsnow.busiyi.world
- 覆盖源：百度热搜/知乎/微博/B站/V2EX等社区
- 提取关键词：智能驾驶/自动驾驶/robotaxi/智驾/端到端/世界模型
- 注意：该站综合新闻占比高，科技类较少，需严格过滤非智驾内容

---

## 第一组：头部企业动向（权重最高）

### 核心关键词（通用）
```
智能驾驶 智驾 自动驾驶
Waymo / Tesla FSD Cybercab
百度Apollo 萝卜快跑
小马智行 Pony.ai / 文远知行 WeRide
滴滴 自动驾驶
元戎启行 DeepRoute
华为 智能驾驶 自动驾驶 智驾 乾崑
大疆 卓驭 智驾
小鹏 XNGP VLA 智驾
理想 AD 智驾 / 蔚来 智驾 / 小米 智驾 智能驾驶
Momenta / 地平线 HSD 征程
英伟达 NVIDIA Drive Thor
Uber robotaxi / Stellantis / Wayve
```

### 技术方向关键词
```
端到端 end-to-end 智驾 E2E
scaling law 智驾 / 泛化性
无图 NOA 智能驾驶 / 激光雷达 LiDAR / 纯视觉 vision-only
车规级 芯片 域控 智驾
```

---

## 第二组：政策法规 → 标记为「行业动态」

```
智能驾驶 政策 法规 标准
L3 L4 准入 国标
智能驾驶 安全 监管
联合国 WP29 法规
工信部 智能驾驶 标准
自动驾驶 技术法规 获批 发布
```

---

## 第三组：行业动态与融资 → 标记为「行业动态」

```
robotaxi 融资 投资
智能驾驶 量产 商业化
智驾 合作 联盟
智驾 芯片 域控制器
```

---

## 第四组：前沿论文 → 标记为「前沿论文」

⚠️ 必须搜索原始论文链接(arxiv.org / openaccess.thecvf.com / openreview.net)，禁止使用聚合器(如 papernotes.org)作为最终链接。

```
# 英文搜索（原始来源）
CVPR 2026 autonomous driving paper site:openaccess.thecvf.com
end-to-end autonomous driving paper site:arxiv.org 2026
world model driving paper site:arxiv.org 2026
autonomous driving dataset benchmark site:arxiv.org 2026
NeurIPS autonomous driving paper site:openreview.net 2026
ICML autonomous driving paper site:openreview.net 2026
VLA vision language action driving site:arxiv.org 2026
MOE mixture of experts autonomous driving site:arxiv.org 2026
world model autonomous driving site:arxiv.org 2026

# 中文搜索补充
CVPR 2026 自动驾驶 论文
端到端 自动驾驶 论文 2026
世界模型 驾驶 论文 2026
```

---

## 第五组：事故舆情与人事 + 国内大厂AI动态 → 标记为「其他消息」

每日精选头部热点即可，不必每类都覆盖。

### 5A — 事故舆情与人事
```
智驾 事故 召回 投诉
智驾 维权 舆情
智能驾驶 事故 撞车
智驾 离职 人事变动
智驾 裁员 扩招
车企 智驾 高管 调整
自动驾驶 团队 跳槽 创业
```

### 5B — 国内大厂AI内部动态（精选）
来源：36氪/虎嗅/晚点/智东西/Tech星球/硅星人/极客公园/APPSO
收录标准：头部大厂(BAT/字节/美团/华为/京东/小米)的AI战略/内部工具政策/大模型应用

```
# --- 美团 ---
美团 龙猫 LongCat 大模型 内部
美团 全面使用 LongCat 禁用 豆包 通义

# --- 阿里 ---
阿里 禁用 Claude Code 内部 IDE
阿里 通义灵码 Qoder 内部推广

# --- 字节 ---
字节 豆包 大模型 内部 战略
字节跳动 AI 智能体 应用 内部

# --- 腾讯 ---
腾讯 混元 大模型 应用 落地
腾讯 AI 战略 内部 2026

# --- 百度 ---
百度 文心 大模型 内部 应用

# --- 华为 ---
华为 盘古 大模型 智驾 应用
华为 内部 AI 工具 政策

# --- 京东 / 小米 ---
京东 言犀 大模型 内部 应用
小米 大模型 内部 AI 应用

# --- 综合 ---
国内 互联网 大厂 AI 内部政策 2026
AI 编程助手 国内 大厂 内部
大模型 国内 互联网 落地 应用 2026
大厂 AI 内部 政策 变动 2026
```

---

## 第六组：行业峰会 → 标记为「行业动态」

```
MOVE 智能驾驶 / CES 自动驾驶
车展 亮相 自动驾驶 智能驾驶
智能驾驶 峰会 论坛
百人会 智能驾驶 / 中国电动汽车百人会
```

---

## 第七组：重点媒体定向搜索

按媒体名称定向搜索，结果按内容归入对应版块。

### 智驾垂直媒体
```
智东西 智能驾驶 / 高工智能汽车
懂车帝 智驾 / APPSO 智驾
智驾最前沿 / 量子位 智能驾驶
机器之心 自动驾驶 / 雷锋网 智能驾驶
RoboX / 芝能汽车 智驾
智驾具身数据挖掘 / 车东西 智驾
新智驾 / 深蓝AI 智能驾驶
智车科技 智能驾驶 / 小马智行
```

### 科技商业媒体（覆盖大厂AI动态/大模型/融资）
```
36氪 智能驾驶 / 36氪 AI 大模型 / 36氪 大厂 AI 内部
虎嗅 智能驾驶 / 虎嗅 AI 大模型 / 虎嗅 大厂 AI 内部
钛媒体 智能驾驶 / 钛媒体 AI 大模型
晚点 LatePost 智能驾驶 / 晚点 AI 大模型
硅星人 智能驾驶 / 硅星人 AI 大模型
极客公园 智能驾驶 / 极客公园 AI 大模型
Tech星球 智能驾驶 / Tech星球 AI 大模型
```

### 公众号/科技自媒体
```
新浪科技 智能驾驶 / 网易科技 智能驾驶
少数派 智驾 / 腾讯新闻 智能驾驶 自动驾驶
观察者网 科技前沿 智能驾驶 / 晚点Auto 智驾
APPSO 智能驾驶 / 新智驾 / 智车科技 智能驾驶
自动驾驶之心
```

---

## 第八组：具身智能 → 标记为「其他消息」

```
具身智能 embodied AI 智能驾驶
人形机器人 humanoid robot 智驾
具身智能 感知 规划 通用技术
机器人 世界模型 具身

Tesla Optimus 智驾 / 小鹏 IRON 机器人 具身
小米 CyberOne 机器人 具身 / 华为 具身智能 机器人
优必选 具身 人形机器人

Figure AI robot embodied / 1X Technologies embodied AI
Physical Intelligence embodied / Skild AI embodied

具身智能 融资 投资
具身智能 人才 离职 加入
SimReal sim-to-real 具身 智能驾驶
```

---

## 第九组：聚合门户站点搜索

遍历以下站点科技频道，提取智驾相关标题，再溯源到原始来源。

```
site:guancha.cn 智能驾驶
site:huxiu.com 智能驾驶
site:leiphone.com 智能驾驶
site:tags.news.sina.com.cn 无人驾驶
```

---

## 第十组：AI基础设施 → 标记为「行业动态」

**说明**：AI基础设施直接影响自动驾驶的算法训练、车端部署和商业化节奏。
**纳入范围**：
- 中国大厂AI战略/投入（原有，保持）
- **美国大厂AI战略**：Tesla/Google/Amazon/Meta/OpenAI/Anthropic 的AI基础设施投入、模型发布、成本管控策略
- 典型事件：Amazon禁Claude、Google内部AI工具政策、大模型训练成本变化、AI芯片竞争等
- 这类新闻能反映技术演进路径和商业思考，即使不直接讲智驾也值得收录

```
大模型 自动驾驶 应用 落地
AI 算力 智能驾驶 芯片
大模型 智驾 车端 部署
国产芯片 自动驾驶 训练 推理
AI Agent 自动驾驶 智驾
Scaling Law 自动驾驶 端到端 2026
大模型 量产 智驾 2026
AI基础设施 自动驾驶 数据 训练

# 美国大厂AI
OpenAI Anthropic AI 基础设施 投入
Google Meta Amazon AI 内部 政策 工具
Tesla AI 内部 员工 政策
人工智能 企业 禁用 内部 政策 2026
美国 科技 企业 AI 管控 成本
```

---

## 第十一组：美国科技企业AI管理动向 → 标记为「其他消息」

**新增组（2026-07-06复盘后加入）**：
覆盖美国头部科技企业的AI内部管理政策，与5B组（中国大厂）形成对照。

```
Tesla AI 内部 员工 政策 限制 花销
Tesla AI 使用 花费 上限 2026
Google AI 内部 使用 员工 限制
Amazon AI 内部 政策 员工 工具
Meta AI 内部 员工 大模型 花销
美国 科技 企业 AI 支出 控制 2026
硅谷 AI 花销 成本 管控 限制
```

**收录标准**：
- 必须是头部美国科技企业（Tesla/Google/Meta/Amazon/Apple/Microsoft/Oracle/OpenAI/Anthropic）
- 重点关注：AI成本管控政策、内部AI工具禁用/推广、员工AI使用限制
- 每期精选1-3条最热门的即可

---

## 第十二组：半导体/芯片理论突破 → 标记为「行业动态」或「其他消息」

**新增组（2026-07-06复盘后加入）**：
覆盖半导体架构创新、芯片理论新范式，这些虽不直接讲智驾，但决定了智驾芯片的演进路线。

```
# 中文
芯片 架构 突破 新范式 2026
半导体 理论 新定律 论文
EDA 工具 芯片 设计 新方法
先进制程 突破 3nm 2nm 1nm
chiplet 先进封装 突破

# English
chip architecture breakthrough 2026
new chip paradigm scaling law
chiplet advanced packaging breakthrough 2026

# 具体企业
华为 半导体 芯片 理论 突破 论文
台积电 制程 突破 2026
英伟达 芯片 架构 下一代
```

**收录标准**：
- 里程碑级理论/架构突破（新定律发布、颠覆性架构）
- 企业级芯片新品发布对智驾有潜在影响
- 每期精选0-2条最关键的即可

---

## 覆盖企业列表

Waymo, Tesla, 百度Apollo, 小马智行(Pony.ai), 文远知行(WeRide), 滴滴自动驾驶, 元戎启行, 华为, 大疆(卓驭), 小鹏, 理想, 蔚来, 小米, Momenta, 地平线, 英伟达(NVIDIA), Uber, Stellantis, Wayve, Google, Amazon, Meta, Microsoft, OpenAI, Anthropic, 台积电(TSMC)

## 重点关注公众号

智东西, 高工智能汽车, 懂车帝, 小马智行公众号, APPSO, 智驾最前沿, 量子位, 机器之心, 雷锋网, 晚点新闻, RoboX, 芝能汽车, 智驾具身数据挖掘, 车东西, 新智驾, 深蓝AI, 智车科技, 自动驾驶之心, 晚点Auto
