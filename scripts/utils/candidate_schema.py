#!/usr/bin/env python3
"""
候选数据 schema — 阶段一（采集）的产出格式。

比 outline.json 更轻量：只要求 title/url/source_name/origin，
不要求 summary/dimension/section（这些是阶段二精编的产出）。

用法:
    from utils.candidate_schema import Candidate, CandidateList
    from utils.candidate_schema import load_candidates, save_candidates
"""
import json
from pathlib import Path
from typing import List, Optional, Union

try:
    from pydantic import BaseModel, Field, model_validator
except ImportError:
    print("❌ 需要 pydantic: pip install pydantic")
    raise SystemExit(1)


class Candidate(BaseModel):
    """单条候选新闻 — 阶段一产出"""
    title: str = Field(min_length=1, description="新闻标题")
    url: str = Field(default="", description="原文链接")
    source_name: str = Field(default="", description="来源媒体")
    date: str = Field(default="", description="发表日期(可空)")
    search_group: Optional[int] = Field(default=None, description="LLM搜索组ID")
    snippet: str = Field(default="", description="搜索引擎摘要/页面片段")
    origin: str = Field(description="来源渠道: llm_search | script_crawl")
    confidence: str = Field(default="high", description="置信度: high | low")

    # 阶段二融合后附加的字段
    url_status: Optional[int] = Field(default=None, description="HTTP状态码")
    url_reachable: Optional[bool] = Field(default=None, description="链接是否可达")
    is_history_dup: Optional[bool] = Field(default=None, description="是否历史重复")
    dup_reason: str = Field(default="", description="重复原因")


class CandidateList(BaseModel):
    """候选列表"""
    date: str = Field(description="日期 YYYYMMDD")
    source_type: str = Field(description="来源类型: llm_search | script_crawl | merged")
    total: int = Field(ge=0, description="总条数")
    candidates: List[Candidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def total_matches(self):
        if self.total != len(self.candidates):
            self.total = len(self.candidates)
        return self


def save_candidates(cl: CandidateList, path: Union[str, Path]):
    p = Path(path)
    data = cl.model_dump(mode="json", exclude_none=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def load_candidates(path: Union[str, Path]) -> CandidateList:
    p = Path(path)
    raw = json.loads(p.read_text("utf-8"))
    return CandidateList.model_validate(raw)
