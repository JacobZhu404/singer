#!/usr/bin/env python3
"""
outline.json 的 pydantic schema — 这是 LLM 和代码层之间的唯一接缝。

用途:
    1. 验证 LLM 生成的 outline 字段完整、类型正确
    2. 给 generate_html.py 提供确定性的数据结构
    3. 让 dedup.py 等下游脚本依赖 schema 而非散装 dict

用法:
    from outline_schema import Outline, load_outline
    outline = load_outline("data/outline_20260706.json")
    # outline.items 已按板块索引
    # outline.all_urls 是扁平去重列表
"""
import json
from pathlib import Path
from typing import List, Optional
from datetime import date, datetime

try:
    from pydantic import BaseModel, Field, field_validator, model_validator
except ImportError:
    print("❌ 需要 pydantic 库，请运行:")
    print("   pip install pydantic")
    raise SystemExit(1)


# ─────────────────────────── 内层模型 ───────────────────────────

class NewsItem(BaseModel):
    """单条新闻条目"""
    num: int = Field(ge=1, description="全局连续编号")
    title: str = Field(min_length=1, max_length=200, description="新闻标题")
    source: str = Field(min_length=1, max_length=50, description="来源媒体")
    url: str = Field(min_length=5, description="原文链接")
    date: str = Field(min_length=4, max_length=10, description="文章发表日期，支持 YYYY / YYYY-MM / YYYY-MM-DD，由 dedup 等下游做标准化")

    @field_validator("date")
    @classmethod
    def date_should_be_plausible(cls, v: str) -> str:
        """宽松校验：只检查年份在合理范围内。"""
        year_part = v[:4]
        if not year_part.isdigit() or not (2020 <= int(year_part) <= 2030):
            raise ValueError(f"日期年份不合理: {v}")
        return v
    dimension: str = Field(min_length=1, description="维度标签，如 🏢 企业动向")
    summary: str = Field(min_length=1, max_length=200, description="60字内摘要")

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith("http"):
            raise ValueError(f"URL 必须以 http 开头: {v}")
        return v


class Section(BaseModel):
    """新闻板块"""
    id: str = Field(pattern=r"^sec-\d+$", description="板块ID")
    name: str = Field(min_length=1, description="板块名，如 行业动态")
    icon: str = Field(min_length=1, description="图标 emoji")
    count: int = Field(ge=0, description="本版块条目数")
    items: List[NewsItem] = Field(default_factory=list, description="新闻列表")

    @model_validator(mode="after")
    def count_matches_items(self):
        if self.count != len(self.items):
            raise ValueError(f"{self.id} {self.name}: count={self.count} 但 items 有 {len(self.items)} 条")
        # 校验 items 内的 num 连续
        for i, item in enumerate(self.items):
            expected = (self.items[0].num if i == 0 else self.items[i-1].num + 1)
            if item.num != expected:
                raise ValueError(f"{self.id} 内 num 不连续: 期望 {expected} 实际 {item.num}")
        return self


class Metadata(BaseModel):
    """元数据"""
    all_urls: List[str] = Field(default_factory=list)
    all_titles: List[str] = Field(default_factory=list)
    merged_from: List[str] = Field(default_factory=list)
    quality_notes: Optional[str] = None


class Outline(BaseModel):
    """完整的 outline.json"""
    version: str = Field(pattern=r"^\d+\.\d+$", description="schema 版本")
    generated_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="生成日期")
    date_display: str = Field(min_length=1, description="显示用日期")
    title: str = Field(min_length=1, description="推文标题")
    digest: str = Field(min_length=1, max_length=100, description="推文摘要")
    total: int = Field(ge=0, description="总条数")
    sections: List[Section] = Field(min_length=1, description="板块列表")
    metadata: Metadata = Field(default_factory=Metadata)

    @classmethod
    def from_file(cls, path: str | Path) -> "Outline":
        """从 JSON 文件加载并验证"""
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        return cls.model_validate(raw)

    @model_validator(mode="after")
    def total_matches(self):
        actual = sum(s.count for s in self.sections)
        if self.total != actual:
            raise ValueError(f"total={self.total} 但实际 sum={actual}")
        # 全局 num 连续
        all_nums = []
        for s in self.sections:
            all_nums.extend(item.num for item in s.items)
        for i, n in enumerate(all_nums):
            if n != i + 1:
                raise ValueError(f"全局 num 不连续: 期望 {i+1} 实际 {n}")
        return self

    def flat_items(self) -> List[NewsItem]:
        """所有新闻扁平列表"""
        return [item for s in self.sections for item in s.items]

    def section_map(self) -> dict[str, Section]:
        """id → Section"""
        return {s.id: s for s in self.sections}

    @property
    def all_urls(self) -> List[str]:
        return [item.url for item in self.flat_items()]

    @property
    def all_titles(self) -> List[str]:
        return [item.title for item in self.flat_items()]


# ─────────────────────────── 便捷函数 ───────────────────────────

def load_outline(path: str | Path) -> Outline:
    """加载并验证 outline.json，失败时抛出详细错误。"""
    try:
        return Outline.from_file(path)
    except Exception as e:
        print(f"❌ outline 验证失败: {e}")
        raise


def save_outline(outline: Outline, path: str | Path):
    """序列化并写入 outline.json。"""
    p = Path(path)
    p.write_text(
        outline.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  ✅ outline 已保存: {p}")


# ─────────────────────────── 单元测试 ───────────────────────────

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        try:
            o = load_outline(path)
            print(f"✅ 验证通过: {path}")
            print(f"   版本: {o.version}, 总条数: {o.total}")
            print(f"   板块: {', '.join(f'{s.name}({s.count})' for s in o.sections)}")
        except Exception as e:
            print(f"❌ 验证失败: {e}")
            sys.exit(1)
    else:
        # 自检：用当前数据跑一遍
        import glob
        for fp in sorted(glob.glob("data/outline_*.json")):
            try:
                load_outline(fp)
                print(f"  ✅ {fp}")
            except Exception as e:
                print(f"  ❌ {fp}: {e}")
