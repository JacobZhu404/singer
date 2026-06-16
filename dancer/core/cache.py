import json
import os
import time
import hashlib
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "cache"
CACHE_TTL = 3600  # 1小时


class Cache:
    """文件缓存"""

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_to_path(self, key: str) -> Path:
        """key转文件路径"""
        h = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"{h}.json"

    def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        path = self._key_to_path(key)
        if not path.exists():
            return None

        try:
            with open(path) as f:
                data = json.load(f)

            # 检查过期
            if time.time() - data.get('_ts', 0) > CACHE_TTL:
                path.unlink()
                return None

            return data.get('value')
        except Exception as e:
            logger.warning(f"缓存读取失败: {e}")
            return None

    def set(self, key: str, value: Any) -> None:
        """设置缓存"""
        path = self._key_to_path(key)
        try:
            with open(path, 'w') as f:
                json.dump({'_ts': time.time(), 'value': value}, f, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"缓存写入失败: {e}")

    def clear(self) -> int:
        """清理过期缓存"""
        count = 0
        for p in self.cache_dir.glob("*.json"):
            try:
                with open(p) as f:
                    data = json.load(f)
                if time.time() - data.get('_ts', 0) > CACHE_TTL:
                    p.unlink()
                    count += 1
            except:
                pass
        return count