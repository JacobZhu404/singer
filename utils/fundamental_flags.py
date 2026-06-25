"""
基本面风险标签 + 综合分调整

基于 PE/PB 给出**业绩雷/估值过高**的风险提示与小幅分数惩罚——本项目
不是做价值投资选股，加这一维是为了避开「技术面好看但基本面踩雷」
（巨亏、几百倍 PE）。规则保守，不替代价值因子。

阈值（启发式，与一般行业参考一致）：
  PE < 0           danger  亏损公司
  PE > 100         danger  极高估值
  50 <= PE <= 100  warn    偏高估值
  PB > 10          warn    PB 极高
  PB < 0           warn    净资产为负
  流通市值 < 20 亿 warn    小市值流动性差（可选，目前仅信息提示）
"""

from typing import List, Tuple


def compute_fundamental_flags(pe, pb) -> List[dict]:
    """根据 PE/PB 输出风险标签列表（与 indicators.calc_risk_flags 同结构）"""
    flags: List[dict] = []
    if pe is not None:
        if pe < 0:
            flags.append({
                "type": "pe_negative",
                "label": "亏损公司",
                "level": "danger",
                "desc": f"PE={pe:.1f}，公司处于亏损状态，业绩风险高",
            })
        elif pe > 100:
            flags.append({
                "type": "pe_extreme",
                "label": "PE极高",
                "level": "danger",
                "desc": f"PE={pe:.0f}，估值显著偏离合理区间，业绩兑现压力大",
            })
        elif pe >= 50:
            flags.append({
                "type": "pe_high",
                "label": "PE偏高",
                "level": "warn",
                "desc": f"PE={pe:.0f}，估值偏高，关注业绩支撑",
            })
    if pb is not None:
        if pb < 0:
            flags.append({
                "type": "pb_negative",
                "label": "净资产为负",
                "level": "warn",
                "desc": f"PB={pb:.2f}，净资产为负，财务结构异常",
            })
        elif pb > 10:
            flags.append({
                "type": "pb_high",
                "label": "PB极高",
                "level": "warn",
                "desc": f"PB={pb:.1f}，市净率远高于行业常态",
            })
    return flags


def fundamental_score_adjustment(pe, pb) -> Tuple[float, List[str]]:
    """
    根据 PE/PB 计算综合分调整量（负数为惩罚，正数为加成）。
    返回 (adjustment, reasons)。规模 ≤ ±8 分，避免压过技术面主因子。

    设计意图：避免「技术好看但基本面踩雷」，不是做价值因子——
    所以**只罚不奖**：亏损/极高 PE 给惩罚；正常 PE 不加分。
    """
    adj = 0.0
    reasons: List[str] = []
    if pe is not None:
        if pe < 0:
            adj -= 8.0
            reasons.append(f"亏损公司(PE={pe:.1f})")
        elif pe > 100:
            adj -= 6.0
            reasons.append(f"PE极高({pe:.0f})")
        elif pe >= 50:
            adj -= 3.0
            reasons.append(f"PE偏高({pe:.0f})")
    if pb is not None:
        if pb < 0:
            adj -= 3.0
            reasons.append(f"净资产为负(PB={pb:.2f})")
        elif pb > 10:
            adj -= 2.0
            reasons.append(f"PB极高({pb:.1f})")
    return adj, reasons
