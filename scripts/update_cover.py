#!/usr/bin/env python3
"""更新公众号草稿的封面图

配置来源: config.json (优先), 环境变量 (可覆盖)

使用方法:
  export WX_DRAFT_MEDIA_ID=<草稿media_id>   (必传:要更新封面的草稿ID)
  python3 update_cover.py
"""
import os, sys, json
import urllib.request
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

# 封面图路径（默认用 generate_cover.py 产出的高清成品 cover_YYYYMMDD_fixed.png）
COVER_PATH = os.environ.get("WX_COVER_FILE", "")
if not COVER_PATH:
    fixed = OUTPUT_DIR / f"cover_{_DATE_STR}_fixed.png"
    COVER_PATH = str(fixed if fixed.exists() else OUTPUT_DIR / f"cover_{_DATE_STR}.png")


def _post_json(url: str, body: bytes, timeout: int = 30) -> dict:
    """用 urllib 发送 JSON POST（绕过 requests 导致 WeChat 乱码的问题）。"""
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upload_image(token, img_path):
    url = "https://api.weixin.qq.com/cgi-bin/material/add_material"
    params = {"access_token": token, "type": "image"}
    with open(img_path, "rb") as f:
        r = requests.post(url, params=params, files={"media": f}, timeout=30)
    data = r.json()
    if "media_id" not in data:
        raise RuntimeError(f"上传失败: {data}")
    return data["media_id"]


# draft/get 返回的只读字段，draft/update 不接受，回传会报错
_WRITABLE_FIELDS = [
    "title", "author", "digest", "content", "content_source_url",
    "need_open_comment", "only_fans_can_comment",
]


def update_draft(token, media_id, thumb_id):
    """更新草稿封面：拉取原文 → 换封面 → 仅回传可写字段。"""
    update_url = f"https://api.weixin.qq.com/cgi-bin/draft/update?access_token={token}"
    get_url = f"https://api.weixin.qq.com/cgi-bin/draft/get?access_token={token}"

    existing = _post_json(
        get_url,
        json.dumps({"media_id": media_id}, ensure_ascii=False).encode("utf-8"),
        timeout=15,
    )
    src = (existing.get("news_item") or [{}])[0]
    # 只保留可写字段，剔除 url/thumb_url/article_type 等只读字段
    article = {k: src[k] for k in _WRITABLE_FIELDS if k in src}
    article["thumb_media_id"] = thumb_id
    # 注意：draft/update 的键是 "articles"（对象），不是 "article"
    body = json.dumps({
        "media_id": media_id,
        "index": 0,
        "articles": article,
    }, ensure_ascii=False).encode("utf-8")
    data = _post_json(update_url, body, timeout=30)
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
