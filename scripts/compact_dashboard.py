#!/usr/bin/env python3.11
"""紧凑版仪表盘 CSS 优化"""
from pathlib import Path

HTML_PATH = Path("/Users/jacob/WorkBuddy/2026-07-01-09-18-45/dashboard_assets/index.html")

html = HTML_PATH.read_text("utf-8")

replacements = [
    # Hero 头部压缩
    ('.hero{padding:60px 24px 50px', '.hero{padding:32px 16px 28px'),
    ('.hero h1{font-size:clamp(28px,5vw,48px)', '.hero h1{font-size:clamp(20px,5vw,32px)'),
    ('.hero-date{font-size:18px', '.hero-date{font-size:14px'),
    ('.hero-total{font-size:64px', '.hero-total{font-size:40px'),
    ('.hero-total-label{font-size:14px', '.hero-total-label{font-size:12px'),
    ('.hero-stats{gap:12px;margin-top:24px', '.hero-stats{gap:6px;margin-top:14px'),
    ('.hero-stat{padding:8px 16px;font-size:13px', '.hero-stat{padding:4px 10px;font-size:11px'),
    
    # 导航压缩
    ('.nav-bar{padding:12px 0', '.nav-bar{padding:6px 0'),
    ('.nav-link{padding:6px 14px;font-size:13px', '.nav-link{padding:4px 10px;font-size:12px'),
    
    # 主内容区
    ('.main{padding:40px 0', '.main{padding:16px 0'),
    ('.section{margin-bottom:48px', '.section{margin-bottom:24px'),
    ('.section-title{font-size:22px;margin-bottom:20px', '.section-title{font-size:17px;margin-bottom:12px'),
    ('.section-icon{width:36px;height:36px;border-radius:10px;font-size:18px', '.section-icon{width:28px;height:28px;border-radius:8px;font-size:14px'),
    ('.section-count{font-size:13px;padding:2px 10px', '.section-count{font-size:11px;padding:2px 8px'),
    ('.container{padding:0 24px}', '.container{padding:0 12px}'),
    
    # 卡片压缩
    ('.card-grid{gap:16px}', '.card-grid{gap:8px}'),
    ('.card-number{min-width:48px;padding:18px 12px;font-size:18px', '.card-number{min-width:36px;padding:12px 8px;font-size:14px'),
    ('.card-content{padding:16px 16px 16px 12px', '.card-content{padding:10px 12px 10px 8px'),
    ('.card-title{font-size:15px;margin-bottom:6px;line-height:1.4}', '.card-title{font-size:13px;margin-bottom:4px;line-height:1.4}'),
    ('.card-summary{font-size:13px;margin-bottom:8px;line-height:1.5}', '.card-summary{font-size:12px;margin-bottom:6px;line-height:1.5}'),
    ('.card-relevance{font-size:12px;padding:6px 10px;margin-bottom:8px', '.card-relevance{font-size:11px;padding:4px 8px;margin-bottom:6px'),
    
    # 概述区压缩
    ('.card-source{font-size:11px;padding:2px 8px', '.card-source{font-size:10px;padding:2px 6px'),
    ('.card-date{font-size:11px;padding:2px 8px', '.card-date{font-size:10px;padding:2px 6px'),
    ('.card-tag{font-size:11px;padding:2px 8px', '.card-tag{font-size:10px;padding:2px 6px'),
    
    # Footer 压缩
    ('footer{padding:40px 24px', 'footer{padding:20px 16px'),
    ('footer .footer-stat-num{font-size:32px', 'footer .footer-stat-num{font-size:24px'),
    ('footer .footer-note{font-size:13px', 'footer .footer-note{font-size:11px'),
    ('footer .footer-stats{gap:40px', 'footer .footer-stats{gap:16px'),
    
    # 左侧编号条颜色压缩
    ('.card-number{background:#6366f1;', '.card-number{background:#6366f1;'),
]

for old, new in replacements:
    if old in html:
        html = html.replace(old, new)
        print(f"  ✅ {old[:40]}...")
    else:
        print(f"  ⚠️ 未找到: {old[:40]}...")

HTML_PATH.write_text(html, encoding="utf-8")
print(f"\n✅ 仪表盘已紧凑化")
