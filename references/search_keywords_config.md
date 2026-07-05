# 自动驾驶晨报 - Web 搜索关键词配置
# 生成每日晨报时,按以下关键词分组搜索,每轮取前5-10条结果

## 搜索分组→版块映射

搜索结果按以下5个版块标记(搜索时的维度标记):

| 版块 | ID | 含义 | 对应搜索组 |
|------|-----|------|-----------|
| 行业动态 | sec-1 | 企业动向/融资并购/政策法规/商业化/峰会/合作联盟/AI基础设施 | 第1组+第2组+第3组+第6组+第10组 |
| 产品发布 | sec-2 | 新车型/新功能/新技术/OTA更新/芯片发布 | 第1组中的产品类 |
| 模型发布 | sec-3 | 新模型/模型更新/benchmark/技术架构发布 | 第1组中的模型类 |
| 前沿论文 | sec-4 | arxiv/CVPR/NeurIPS/ICML论文 | 第4组 |
| 其他消息 | sec-5 | 事故/召回/人事/吐槽/具身智能/观点评论 | 第5组+第8组 |

**标记规则**: 同一条新闻可能属于多个版块,取最核心的1个:
- 企业融资/并购/合作 → 行业动态(不是产品发布)
- OTA更新/新功能上线 → 产品发布(不是模型发布)
- 新模型论文发布 → 前沿论文(优先,即使也是模型)
- 事故/人事/吐槽 → 其他消息(不要分到行业动态)
- 具身智能 → 其他消息(与自动驾驶交叉但非核心)

## 第一组:头部企业动向 (权重最高)
# 核心关键词（通用）
智能驾驶 智驾 自动驾驶
Autonomous Vehicle 
robotaxi
Waymo
Tesla FSD Cybercab
百度Apollo 萝卜快跑
小马智行 Pony.ai
文远知行 WeRide
滴滴 自动驾驶
元戎启行 DeepRoute
华为 智能驾驶 自动驾驶 智驾 乾崑
大疆 卓驭 智驾
小鹏 XNGP VLA 智驾
理想 AD 智驾
蔚来 智驾
小米 智驾 智能驾驶
Momenta
地平线 HSD 征程
英伟达 NVIDIA Drive Thor
Uber robotaxi
Stellantis
Wayve (英国自动驾驶公司)

# 技术方向关键词(与头部企业交叉命中)
端到端 end-to-end 智驾 E2E
scaling law 智驾
泛化性 
无图 NOA 智能驾驶
激光雷达 LiDAR
纯视觉 vision-only
车规级 芯片 域控 智驾

## 第二组:政策法规 → 标记为「行业动态」
智能驾驶 政策 法规 标准
L3 L4 准入 国标
智能驾驶 安全 监管
联合国 WP29 法规
工信部 智能驾驶 标准

## 第三组:行业动态与融资 → 标记为「行业动态」
robotaxi 融资 投资
智能驾驶 量产 商业化
智驾 合作 联盟
智驾 芯片 域控制器
Wayve 融资 自动驾驶 英国

## 第四组:前沿论文 → 标记为「前沿论文」(⚠️ 必须搜索原始论文链接,不要聚合器)
# 优先搜索原始来源: arxiv.org, openaccess.thecvf.com, papers.nips.cc, openreview.net
# 禁止使用聚合器链接(如 papernotes.org)作为最终链接
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

## 第五组:事故舆情与人事/国内互联网AI动态 → 标记为「其他消息」
# 说明: 本组覆盖两类内容，均归入「其他消息」版块，每日精选头部热点即可

### 5A - 事故舆情与人事
智驾 事故 召回 投诉
智驾 维权 舆情
智能驾驶 事故 撞车
智驾 离职 人事变动
智驾 裁员 扩招
车企 智驾 高管 调整
自动驾驶 团队 跳槽 创业

### 5B - 国内互联网大模型/AI智能应用头部热点(精选)
# 来源: 36氪/虎嗅/晚点/智东西/Tech星球/硅星人/极客公园/.APPSO 等
# 关注: 大厂内部AI工具政策/大模型发布/AI智能体应用/编程助手/AI基础设施
# ⚠️ 收录标准: 必须是头部大厂( BAT/字节/美团/华为/京东/小米 )的AI战略/产品/政策动向
# ⚠️ 不收录: 纯技术论文、非头部公司、重复旧闻

# --- 美团 ---
美团 龙猫 LongCat 大模型 内部
美团 龙珠 AI 大模型 2026
美团 全面使用 LongCat 禁用 豆包 通义

# --- 阿里 ---
阿里 禁用 Claude Code 内部 IDE
阿里巴巴 内部 AI 编程助手 政策
阿里 通义灵码 Qoder 内部推广
阿里 大模型 内部 战略 2026

# --- 字节 ---
字节 豆包 大模型 内部 战略
字节跳动 AI 智能体 应用 内部
字节 火山引擎 大模型 企业

# --- 腾讯 ---
腾讯 混元 大模型 应用 落地
腾讯 AI 战略 内部 2026

# --- 百度 ---
百度 文心 大模型 内部 应用
百度 AI 战略 自动驾驶 落地

# --- 华为 ---
华为 盘古 大模型 智驾 应用
华为 内部 AI 工具 政策

# --- 京东 ---
京东 言犀 大模型 内部 应用
京东 AI 战略 2026

# --- 小米 ---
小米 大模型 内部 AI 应用
小米 AI 战略 2026

# --- 综合/通用 ---
国内 互联网 大厂 AI 内部政策 2026
国内 互联网 大厂 大模型 内部使用
AI 编程助手 国内 大厂 内部
AI 智能体 国内 互联网 应用
大模型 国内 互联网 落地 应用 2026
大厂 AI 内部 政策 变动 2026

## 第六组:行业峰会 → 标记为「行业动态」
MOVE 智能驾驶
CES 自动驾驶
车展 亮相 自动驾驶 智能驾驶
智能驾驶 峰会 论坛
百人会 智能驾驶
中国电动汽车百人会

## 第七组:重点媒体定向搜索(按公众号/媒体名称定向搜索,结果按内容归入对应版块)
# 说明: 以下媒体是国内智驾/AI领域最稳定的信息源，建议每日定向搜索

### 智驾垂直媒体
智东西 智能驾驶
高工智能汽车
懂车帝 智驾
APPSO 智驾
智驾最前沿
量子位 智能驾驶
机器之心 自动驾驶
雷锋网 智能驾驶
RoboX
芝能汽车 智驾
智驾具身数据挖掘
车东西 智驾
新智驾
深蓝AI 智能驾驶
智车科技 智能驾驶
小马智行

### 科技商业媒体(覆盖大厂AI动态/大模型/融资)
36氪 智能驾驶
36氪 AI 大模型
36氪 大厂 AI 内部 政策
36氪 美团 龙猫
36氪 阿里 Claude Code
虎嗅 智能驾驶
虎嗅 AI 大模型
虎嗅 大厂 AI 内部
钛媒体 智能驾驶
钛媒体 AI 大模型
钛媒体 大厂 AI 工具
晚点 LatePost 智能驾驶
晚点 LatePost AI 大模型
晚点 LatePost 大厂 AI
硅星人 智能驾驶
硅星人 AI 大模型
硅星人 大厂 AI 政策
极客公园 智能驾驶
极客公园 AI 大模型
Tech星球 智能驾驶
Tech星球 AI 大模型
Tech星球 大厂 AI 内部

### 公众号/科技自媒体
APPSO 智能驾驶
新智驾
智车科技 智能驾驶
自动驾驶之心
晚点Auto 智驾
新浪科技 智能驾驶
网易科技 智能驾驶
少数派 智驾
虎嗅 智能驾驶
腾讯新闻 智能驾驶 自动驾驶
观察者网 科技前沿 智能驾驶

## 第八组:具身智能 → 标记为「其他消息」(与自动驾驶交叉领域)
具身智能 embodied AI 智能驾驶
人形机器人 humanoid robot 智驾
具身智能 感知 规划 通用技术
机器人 世界模型 具身
Tesla Optimus 智驾
小鹏 IRON 机器人 具身
小米 CyberOne 机器人 具身
华为 具身智能 机器人
优必选 具身 人形机器人
Figure AI robot embodied
1X Technologies embodied AI
Physical Intelligence embodied
Skild AI embodied
具身智能 融资 投资
具身智能 人才 离职 加入
SimReal sim-to-real 具身 智能驾驶

## 第零组:聚合热榜数据源(优先级降序,fetch首页提取智驾相关标题)
# 用法: 使用 WebFetch 获取首页内容,提取与智驾/自动驾驶/robotaxi 相关的标题和链接
# 再对每个标题做二次搜索,溯源到原始来源(如36氪/界面/晚点/量子位等)

### P1 - 日热榜 tophub.today (最高优先级)
# URL: https://tophub.today
# 覆盖源: 微博热搜/知乎热榜/微信24h热文/百度实时/36氪/IT之家/虎嗅/机器之心/量子位/Readhub/掘金AI/少数派
# 提取关键词: 智能驾驶/自动驾驶/robotaxi/Waymo/Tesla/萝卜快跑/小马智行/文远知行/华为智驾/地平线/小鹏XNGP/蔚来/理想/小米智驾/端到端/世界模型/具身智能
# 操作: fetch首页 → 遍历所有板块 → 标题含上述关键词即收录 → 二次搜索标题获取原始URL

### P2 - Buzzing buzzing.cc (中优先级,海外信息差)
# URL: https://buzzing.cc (主页聚合) 或子站:
#   https://hn.buzzing.cc - Hacker News热门
#   https://tech.buzzing.cc - 科技板块
#   https://news.buzzing.cc - 国外新闻头条
# 提取关键词: autonomous driving/robotaxi/Waymo/Tesla FSD/Cruise/self-driving/AV
# 操作: fetch子站 → 提取中文标题和英文标题 → 含关键词即收录 → 点击跳转原文获取URL

### P3 - NewsNow newsnow.busiyi.world (低优先级,综合新闻补充)
# URL: https://newsnow.busiyi.world
# 覆盖源: 百度热搜/知乎/微博/B站/V2EX等社区
# 提取关键词: 智能驾驶/自动驾驶/robotaxi/智驾/端到端/世界模型
# 注意: 该站综合新闻占比高,科技类较少,需严格过滤非智驾内容
# 操作: fetch首页 → 提取含关键词标题 → 二次搜索获取原始来源

## 第九组:聚合门户站点搜索(批量扫站,提取热点,过滤广告/购物导向)
# 用法: 遍历这些站点首页/科技频道,提取与智驾相关的标题,再溯源到原始来源
# 重点看: 这些站点引用的来源（如36氪/界面/晚点等）,作为补充线索
# 注: tophub.today 已移至第零组(P1),此处保留其他站点
site:maomu.com 智能驾驶
site:guancha.cn 智能驾驶
site:huxiu.com 智能驾驶
site:leiphone.com 智能驾驶
site:tags.news.sina.com.cn 无人驾驶
# 新浪科技/网易科技/腾讯新闻/少数派 直接用搜索,不需要site限定

## 第十组:AI基础设施 → 标记为「行业动态」(AI大模型/算力对智驾产业的影响)
# 说明：AI基础设施直接影响自动驾驶的算法训练、车端部署和商业化节奏
# 典型的纯AI新闻（如某公司发布通用大模型）除非与智驾有明确关联，否则不收录
# 重点捕捉：AI大模型在智驾中的应用落地、国产算力对智驾芯片的影响、AI Agent与自动驾驶的交叉
大模型 自动驾驶 应用 落地
AI 算力 智能驾驶 芯片
大模型 智驾 车端 部署
国产芯片 自动驾驶 训练 推理
AI Agent 自动驾驶 智驾
Scaling Law 自动驾驶 端到端 2026
大模型 量产 智驾 2026
AI基础设施 自动驾驶 数据 训练
