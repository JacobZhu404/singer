# 自动驾驶晨报 - Web 搜索关键词配置
# 生成每日晨报时,按以下关键词分组搜索,每轮取前5-10条结果

## 搜索分组→版块映射

搜索结果按以下5个版块标记(搜索时的维度标记):

| 版块 | ID | 含义 | 对应搜索组 |
|------|-----|------|-----------|
| 行业动态 | sec-1 | 企业动向/融资并购/政策法规/商业化/峰会/合作联盟 | 第1组+第2组+第3组+第6组 |
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
智能驾驶 智驾
Autonomous Vehicle robotaxi
Robotaxi autonomous vehicle
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
Stellantis Wayve

# 技术方向关键词(与头部企业交叉命中)
端到端 end-to-end 智驾
E2E autonomous driving
scaling law 智驾
泛化性 智驾
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

## 第五组:事故舆情与人事 → 标记为「其他消息」
智驾 事故 召回 投诉
智驾 维权 舆情
智能驾驶 事故 撞车
智驾 离职 人事变动
智驾 裁员 扩招
车企 智驾 高管 调整

## 第六组:行业峰会 → 标记为「行业动态」
MOVE 智能驾驶
CES 自动驾驶
智能驾驶 峰会 论坛
百人会 智能驾驶
中国电动汽车百人会

## 第七组:重点媒体定向搜索(按公众号/媒体名称定向搜索,结果按内容归入对应版块)
智东西 智能驾驶
高工智能汽车
懂车帝 智驾
小马智行
APPSO 智驾
智驾最前沿
量子位 智能驾驶
机器之心 自动驾驶
雷锋网 智能驾驶 雷锋网
晚点新闻 自动驾驶
RoboX
芝能汽车 智驾
智驾具身数据挖掘
车东西 智驾
新智驾
深蓝AI 智能驾驶
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

## 第九组:聚合门户站点搜索(批量扫站,提取热点,过滤广告/购物导向)
# 用法: 遍历这些站点首页/科技频道,提取与智驾相关的标题,再溯源到原始来源
# 重点看: 这些站点引用的来源（如36氪/界面/晚点等）,作为补充线索
site:tophub.today 智能驾驶
site:maomu.com 智能驾驶
site:guancha.cn 智能驾驶
site:huxiu.com 智能驾驶
site:leiphone.com 智能驾驶
site:tags.news.sina.com.cn 无人驾驶
# 新浪科技/网易科技/腾讯新闻/少数派 直接用搜索,不需要site限定
