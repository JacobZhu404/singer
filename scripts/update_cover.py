#!/usr/bin/env python3
"""更新公众号草稿的封面图

配置来源: config.json (优先), 环境变量 (可覆盖)

使用方法:
  export WX_DRAFT_MEDIA_ID=<草稿media_id>   (必传:要更新封面的草稿ID)
  python3 update_cover.py
"""
import os, sys, json
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("缺少 requests 库,请先运行: pip install requests")
    sys.exit(1)

# ==================== 配置加载 ====================
_SCRIPT_DIR = Path(__file__).parent.resolve()
_BASE_DIR = _SCRIPT_DIR.parent.resolve()
_CONFIG_PATH = _BASE_DIR / "config.json"
_DATE_STR = datetime.now().strftime("%Y%m%d")

CONFIG = {}
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)

WX_CONFIG = CONFIG.get("wechat", {})
PATH_CONFIG = CONFIG.get("paths", {})

WX_APP_ID = os.environ.get("WX_APP_ID", WX_CONFIG.get("app_id", ""))
WX_APP_SECRET = os.environ.get("WX_APP_SECRET", WX_CONFIG.get("app_secret", ""))
WX_DRAFT_MEDIA_ID = os.environ.get("WX_DRAFT_MEDIA_ID", "")

OUTPUT_DIR = Path(os.environ.get("WX_OUTPUT_DIR", PATH_CONFIG.get("output_dir", str(_BASE_DIR / "data"))))

# 封面图路径
COVER_PATH = os.environ.get("WX_COVER_FILE", "")
if not COVER_PATH:
    COVER_PATH = str(OUTPUT_DIR / f"cover_{_DATE_STR}.png")


def upload_image(token, img_path):
    url = "https://api.weixin.qq.com/cgi-bin/material/add_material"
    params = {"access_token": token, "type": "image"}
    with open(img_path, "rb") as f:
        r = requests.post(url, params=params, files={"media": f}, timeout=30)
    data = r.json()
    if "media_id" not in data:
        raise RuntimeError(f"上传失败: {data}")
    return data["media_id"]


def update_draft(token, media_id, thumb_id):
    """更新草稿封面"""
    url = "https://api.weixin.qq.com/cgi-bin/draft/update"
    get_url = "https://api.weixin.qq.com/cgi-bin/draft/get"
    r = requests.post(get_url, params={"access_token": token},
                      json={"media_id": media_id}, timeout=15)
    existing = r.json()
    article = (existing.get("news_item") or [{}])[0]
    article["thumb_media_id"] = thumb_id
    body = json.dumps({
        "media_id": media_id,
        "article": article,
        "index": 0,
    }, ensure_ascii=False)
    r = requests.post(url, params={"access_token": token},
                      data=body.encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8"},
                      timeout=30)
    data = r.json()
    if data.get("errcode", -1) != 0:
        raise RuntimeError(f"更新草稿失败: {data}")
    print(f"✅ 草稿封面更新成功")
    return data


def main():
    if not WX_APP_ID or not WX_APP_SECRET:
        print("❌ 缺少凭据,请检查 config.json 或设置环境变量")
        sys.exit(1)
    if not WX_DRAFT_MEDIA_ID:
        print("❌ 缺少草稿 media_id,请设置 WX_DRAFT_MEDIA_ID 环境变量")
        sys.exit(1)
    if not COVER_PATH or not Path(COVER_PATH).exists():
        print(f"❌ 封面图不存在: {COVER_PATH}")
        print("等待 image-creator 生成完成后重试")
        sys.exit(1)

    token_url = "https://api.weixin.qq.com/cgi-bin/token"
    r = requests.get(token_url, params={
        "grant_type": "client_credential",
        "appid": WX_APP_ID,
        "secret": WX_APP_SECRET,
    }, timeout=15)
    token = r.json()["access_token"]

    print("📤 上传封面图...")
    thumb_id = upload_image(token, COVER_PATH)
    print(f"  media_id={thumb_id}")

    print("📝 更新草稿封面...")
    update_draft(token, WX_DRAFT_MEDIA_ID, thumb_id)

    print(f"\n🎉 完成!封面已更新,草稿 media_id={WX_DRAFT_MEDIA_ID}")


if __name__ == "__main__":
    main()
