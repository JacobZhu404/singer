import sys
sys.path.insert(0, "/Users/jacob/personal")

from stock_screener.core.engine import ScreenEngine
from stock_screener.data.fetcher import get_latest_trade_date

date = get_latest_trade_date()
engine = ScreenEngine()

# 测试MACD策略
print("=== MACD多头排列（优化后）===")
result = engine.run_single('macd_bull')
all_signals = result.all_signals if result.all_signals else result.signals
scores = [s.score for s in all_signals]
print("Full hits: %d" % len(scores))
if scores:
    print("Score range: %d - %d" % (min(scores), max(scores)))
    print("Average: %.1f" % (sum(scores)/len(scores)))
    buckets = {}
    for s in scores:
        b = (s // 10) * 10
        if b in buckets:
            buckets[b] += 1
        else:
            buckets[b] = 1
    for b in sorted(buckets.keys()):
        print("  %d-%d: %d" % (b, b+9, buckets[b]))
    print("Top 5:")
    for s in sorted(all_signals, key=lambda x: x.score, reverse=True)[:5]:
        print("  %s(%s): %d points" % (s.name, s.ts_code, s.score))

# 测试右侧交易策略
print("\n=== 右侧交易（优化后）===")
result2 = engine.run_single('right_side')
all_signals2 = result2.all_signals if result2.all_signals else result2.signals
scores2 = [s.score for s in all_signals2]
print("Full hits: %d" % len(scores2))
if scores2:
    print("Score range: %d - %d" % (min(scores2), max(scores2)))
    print("Average: %.1f" % (sum(scores2)/len(scores2)))
    buckets2 = {}
    for s in scores2:
        b = (s // 10) * 10
        if b in buckets2:
            buckets2[b] += 1
        else:
            buckets2[b] = 1
    for b in sorted(buckets2.keys()):
        print("  %d-%d: %d" % (b, b+9, buckets2[b]))
    print("Top 5:")
    for s in sorted(all_signals2, key=lambda x: x.score, reverse=True)[:5]:
        print("  %s(%s): %d points" % (s.name, s.ts_code, s.score))
