"""
通达信沪深京日线离线数据一键下载 + 解压。

来源页：https://www.tdx.com.cn/article/vipdata.html
直链：  https://data.tdx.com.cn/vipdoc/hsjday.zip

解压后目录结构（与 TdxOfflineStore 期望一致）：
    {dest_dir}/sh/lday/sh600000.day
    {dest_dir}/sz/lday/sz000001.day
    {dest_dir}/bj/lday/bj000001.day

供 web 调用，进度通过 progress_callback(phase, current, total, extra) 上报：
    phase ∈ {download, extract, done, error}
"""

import os
import shutil
import logging
import zipfile
import threading
from pathlib import Path
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

TDX_HSJDAY_URL = "https://data.tdx.com.cn/vipdoc/hsjday.zip"
_CHUNK = 1024 * 256  # 256 KiB
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST_DIR = _PROJECT_ROOT / "data" / "tdx_vipdoc"

ProgressCB = Callable[[str, int, int, dict], None]


def _noop_progress(phase: str, current: int, total: int, extra: dict) -> None:  # noqa: ARG001
    return


def _normalize_member_path(name: str) -> Optional[str]:
    """
    把 zip 内的成员路径规范化为 'sh/lday/xxx.day' 这种相对路径。

    通达信 zip 可能是：
        sh/lday/sh600000.day
        vipdoc/sh/lday/sh600000.day
        hsjday/sh/lday/sh600000.day
        SH/LDAY/sh600000.day   ← 不同打包工具可能大写
    我们丢掉前缀，只保留从 sh|sz|bj 开始的部分，并把市场目录小写化。

    返回 None 表示该成员应当跳过（目录条目、非数据文件、可疑路径等）。
    """
    if not name or name.endswith("/"):
        return None
    parts = [p for p in name.replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return None
    for i, p in enumerate(parts):
        if p.lower() in ("sh", "sz", "bj"):
            tail = parts[i:]
            # 把市场目录 (parts[i]) 和紧随其后的 lday 目录小写化，文件名保持原样
            normalized = [tail[0].lower()]
            if len(tail) >= 2:
                normalized.append(tail[1].lower())
            normalized.extend(tail[2:])
            return "/".join(normalized)
    return None


def _human_size(n: int) -> str:
    if n is None or n < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}GB"


def _stopped(stop_event: Optional[threading.Event]) -> bool:
    return stop_event is not None and stop_event.is_set()


def download_tdx_zip(
    url: str,
    dest_zip_path: Path,
    progress_cb: ProgressCB,
    stop_event: Optional[threading.Event] = None,
    timeout: float = 30.0,
) -> Path:
    """
    流式下载 TDX zip 到 dest_zip_path。
    支持中途 stop。
    返回最终 zip 文件路径。
    """
    dest_zip_path = Path(dest_zip_path)
    dest_zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_zip_path.with_suffix(dest_zip_path.suffix + ".part")

    logger.info(f"开始下载 TDX 数据包: {url} → {dest_zip_path}")
    progress_cb("download", 0, 0, {"msg": "正在连接服务器..."})

    headers = {"User-Agent": "Mozilla/5.0 (stock-screener/tdx-import)"}
    with requests.get(url, stream=True, timeout=timeout, headers=headers) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0) or 0)
        downloaded = 0
        last_report = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=_CHUNK):
                if _stopped(stop_event):
                    f.close()
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    raise RuntimeError("用户已取消下载")
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                # 限频回调，避免抖动
                if downloaded - last_report >= 1024 * 1024 or downloaded == total:
                    last_report = downloaded
                    msg = f"下载中 {_human_size(downloaded)}"
                    if total:
                        msg += f" / {_human_size(total)}"
                    progress_cb("download", downloaded, total, {"msg": msg})

    # 原子替换
    if dest_zip_path.exists():
        dest_zip_path.unlink()
    tmp_path.rename(dest_zip_path)
    logger.info(f"下载完成: {dest_zip_path} ({_human_size(downloaded)})")
    return dest_zip_path


def extract_tdx_zip(
    zip_path: Path,
    dest_dir: Path,
    progress_cb: ProgressCB,
    stop_event: Optional[threading.Event] = None,
) -> dict:
    """
    解压 zip 到 dest_dir/{sh,sz,bj}/lday/*.day。
    返回 {markets: {sh,sz,bj -> count}, total: int}。
    """
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"开始解压: {zip_path} → {dest_dir}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        total = len(members)
        if total == 0:
            raise RuntimeError("zip 内未发现任何文件")

        markets = {"sh": 0, "sz": 0, "bj": 0}
        skipped = 0
        progress_cb("extract", 0, total, {"msg": f"开始解压 {total} 个文件"})

        for i, m in enumerate(members, 1):
            if _stopped(stop_event):
                raise RuntimeError("用户已取消解压")
            rel = _normalize_member_path(m.filename)
            if rel is None:
                skipped += 1
                if i % 500 == 0 or i == total:
                    progress_cb("extract", i, total, {"msg": f"跳过非数据文件 {skipped} 个"})
                continue

            out_path = dest_dir / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst, length=_CHUNK)

            mk = rel.split("/", 1)[0].lower()
            if mk in markets:
                markets[mk] += 1

            if i % 200 == 0 or i == total:
                progress_cb(
                    "extract",
                    i,
                    total,
                    {"msg": f"解压中 {i}/{total} (sh:{markets['sh']} sz:{markets['sz']} bj:{markets['bj']})"},
                )

    logger.info(f"解压完成: total={total}, markets={markets}, skipped={skipped}")
    return {"markets": markets, "total": sum(markets.values()), "skipped": skipped}


def import_tdx(
    dest_dir: Optional[Path] = None,
    url: str = TDX_HSJDAY_URL,
    progress_cb: Optional[ProgressCB] = None,
    stop_event: Optional[threading.Event] = None,
    keep_zip: bool = False,
) -> dict:
    """
    一键流程：下载 → 解压 → 通知 TdxOfflineStore 刷新。

    Args:
        dest_dir: 解压目标，默认 data/tdx_vipdoc/
        url: 数据包直链
        progress_cb: 进度回调 (phase, current, total, extra_dict)
        stop_event: 中断信号
        keep_zip: 解压完是否保留 zip 文件（默认删除以节省空间）

    Returns:
        {"status": "ok"/"stopped"/"error", "dest_dir": str, "stats": dict, "error": str?}
    """
    cb: ProgressCB = progress_cb or _noop_progress
    dest_dir = Path(dest_dir or DEFAULT_DEST_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "_tmp" / "hsjday.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        download_tdx_zip(url, zip_path, cb, stop_event=stop_event)
        if _stopped(stop_event):
            return {"status": "stopped", "dest_dir": str(dest_dir)}
        stats = extract_tdx_zip(zip_path, dest_dir, cb, stop_event=stop_event)
        if _stopped(stop_event):
            return {"status": "stopped", "dest_dir": str(dest_dir)}
    except RuntimeError as e:
        # 用户主动取消等
        msg = str(e)
        logger.info(f"TDX 导入终止: {msg}")
        cb("error", 0, 0, {"msg": msg})
        return {"status": "stopped" if "取消" in msg else "error",
                "dest_dir": str(dest_dir), "error": msg}
    except Exception as e:
        logger.error(f"TDX 导入失败: {e}", exc_info=True)
        cb("error", 0, 0, {"msg": f"导入失败: {e}"})
        return {"status": "error", "dest_dir": str(dest_dir), "error": str(e)}
    finally:
        if not keep_zip:
            try:
                if zip_path.exists():
                    zip_path.unlink()
                tmp_dir = zip_path.parent
                if tmp_dir.exists() and not any(tmp_dir.iterdir()):
                    tmp_dir.rmdir()
            except OSError as e:
                logger.warning(f"清理临时 zip 失败: {e}")

    # 通知 TdxOfflineStore 切换到新路径并刷新
    try:
        from . import tdx_offline
        tdx_offline.tdx_store.set_base_dir(str(dest_dir))
        store_status = tdx_offline.tdx_store.get_cache_status()
    except Exception as e:
        logger.warning(f"刷新 TdxOfflineStore 失败（不影响导入结果）: {e}")
        store_status = None

    cb("done", stats["total"], stats["total"],
       {"msg": f"导入完成，共 {stats['total']} 只股票",
        "markets": stats["markets"]})
    return {
        "status": "ok",
        "dest_dir": str(dest_dir),
        "stats": stats,
        "store_status": store_status,
    }
