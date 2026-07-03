#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D-Chat 晨报推送脚本
将晨报摘要推送到指定 D-Chat 群聊

配置来源: config.json (优先), 环境变量 (可覆盖)

使用方法:
  python3 dchat_push.py

环境变量覆盖 (可选):
  DCHAT_USERNAME, DCHAT_APP_ID, DCHAT_APP_SECRET, WX_LINKS_URL, WX_WORK_DIR, WX_OUTPUT_DIR

依赖: 无 (使用 subprocess + curl)
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime

# ==================== 配置加载 ====================
_SCRIPT_DIR = Path(__file__).parent.resolve()
_BASE_DIR = _SCRIPT_DIR.parent.resolve()
_CONFIG_PATH = _BASE_DIR / "config.json"
_DATE_STR = datetime.now().strftime("%Y%m%d")

CONFIG = {}
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)

DCHAT_CONFIG = CONFIG.get("dchat", {})
PATH_CONFIG = CONFIG.get("paths", {})
NL_CONFIG = CONFIG.get("newsletter", {})

# 凭据: 环境变量 > config.json
DCHAT_USERNAME = os.environ.get("DCHAT_USERNAME", DCHAT_CONFIG.get("username", "zhuzhenxia"))
DCHAT_APP_ID = os.environ.get("DCHAT_APP_ID", DCHAT_CONFIG.get("app_id", "519170"))
DCHAT_APP_SECRET = os.environ.get("DCHAT_APP_SECRET", DCHAT_CONFIG.get("app_secret", ""))
DCHAT_API = DCHAT_CONFIG.get("api_url", "http://10.88.128.40:8000/dichat/snitch_openapi_online_lb/v1/message.create")

# 输出目录: 环境变量 > config.json > data/
OUTPUT_DIR = Path(os.environ.get("WX_OUTPUT_DIR", PATH_CONFIG.get("output_dir", str(_BASE_DIR / "data"))))

SECTION_NAMES = {}
for sec in NL_CONFIG.get("sections", []):
    SECTION_NAMES[sec["id"]] = sec["name"]


def find_html_file() -> Path:
    """从 data/ 目录查找今日晨报 HTML"""
    default_path = OUTPUT_DIR / f"自动驾驶晨报_{_DATE_STR}.html"
    return default_path if default_path.exists() else None


def extract_summary(html_path: str) -> dict:
    """从 HTML 提取摘要信息"""
    text = Path(html_path).read_text("utf-8")

    m_total = re.search(r'<span class="num">(\d+)</span> 条', text)
    total = m_total.group(1) if m_total else "0"

    stats = re.findall(
        r'<span class="stat-icon">([^<]+)</span><span class="stat-label">([^<]+)</span><span class="stat-num">(\d+)</span>',
        text
    )
    stats_text = " · ".join(f"{icon}{label.strip()}{n}条" for icon, label, n in stats) if stats else ""

    m_date = re.search(r"(\d{4})年(\d+)月(\d+)日", text)
    date_str = f"{m_date.group(2)}月{m_date.group(3)}日" if m_date else _DATE_STR

    titles = re.findall(r'<div class="card-title">([^<]+)</div>', text)
    top5 = titles[:5]

    return {
        "total": total,
        "stats": stats_text,
        "date": date_str,
        "top5_titles": top5,
    }


def send_interactive_card(summary: dict, links_url: str = ""):
    """发送交互式卡片消息到 D-Chat"""
    title = f"🚗 自动驾驶晨报 · {summary['date']}"
    subtitle = f"精选 {summary['total']} 条 · {summary['stats']}"

    actions = []
    actions.append({"type": "section", "text": [{"value": f"今日精选 {summary['total']} 条自动驾驶动态"}]})
    actions.append({"type": "divider"})

    for i, t in enumerate(summary["top5_titles"], 1):
        display = t if len(t) <= 40 else t[:37] + "..."
        value_text = f"{i}. {display}"
        actions.append({"type": "section", "text": [{"value": value_text}]})

    actions.append({"type": "divider"})

    if links_url:
        actions.append({"type": "section", "text": [{"value": f"🔗 原文链接: {links_url}"}]})
    else:
        actions.append({"type": "section", "text": [{"value": "🔗 点击公众号「阅读原文」查看所有链接"}]})

    # ⚠️ text 和 form 必须在顶层,不要包裹在 interactive 字段
    msg_body = {
        "username": DCHAT_USERNAME,
        "msg_type": "interactive",
        "text": title,
        "form": {
            "header": {
                "title": {"text": title, "sub_text": subtitle},
                "background": "normal"
            },
            "actions": actions,
            "version": "2.0"
        }
    }

    body_json = json.dumps(msg_body, ensure_ascii=False)

    cmd = [
        "curl", "-s", "-X", "POST",
        "--noproxy", "*",
        "--max-time", "15",
        "-H", "Content-Type: application/json",
        "-H", f"X-Bot-Type: bot_user",
        "-H", f"X-Bot-Id: {DCHAT_APP_ID}",
        "-d", body_json,
        DCHAT_API,
    ]

    if DCHAT_APP_SECRET:
        cmd.extend(["-u", f"{DCHAT_APP_ID}:{DCHAT_APP_SECRET}"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        print(f"  D-Chat 响应: {proc.stdout[:200]}")
        if proc.returncode != 0:
            print(f"  ⚠️ curl 返回码: {proc.returncode}")
            print(f"  stderr: {proc.stderr[:200]}")
            return False
        try:
            resp = json.loads(proc.stdout)
            if resp.get("code", -1) == 0 or resp.get("errcode", -1) == 0:
                print("  ✅ D-Chat 推送成功")
                return True
            else:
                print(f"  ⚠️ D-Chat 返回错误: {resp}")
                return False
        except json.JSONDecodeError:
            print(f"  ⚠️ D-Chat 响应无法解析")
            return False
    except subprocess.TimeoutExpired:
        print("  ❌ D-Chat 推送超时")
        return False
    except Exception as e:
        print(f"  ❌ D-Chat 推送失败: {e}")
        return False


def main():
    html_path = find_html_file()
    if not html_path:
        print(f"❌ 未找到晨报 HTML 文件")
        sys.exit(1)

    print(f"📄 读取晨报: {html_path}")
    summary = extract_summary(str(html_path))
    print(f"  日期: {summary['date']}")
    print(f"  总数: {summary['total']} 条")
    print(f"  前5条: {summary['top5_titles']}")

    links_url = os.environ.get("WX_LINKS_URL", "")

    print("\n📤 推送到 D-Chat ...")
    success = send_interactive_card(summary, links_url)

    if success:
        print("\n🎉 D-Chat 推送完成!")
    else:
        print("\n⚠️ D-Chat 推送失败,请检查配置")


if __name__ == "__main__":
    main()
