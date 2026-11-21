"""请求 / 响应模型。字符区间均为 [start, end)，按 Unicode 码位计。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator

MAX_CHARS = 300  # 每稿最多 300 个字符


class AnnotationIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="人名")
    start: int = Field(ge=0, description="旧稿区间起点（含）")
    end: int = Field(gt=0, description="旧稿区间终点（不含）")


class PageCreate(BaseModel):
    page_ref: Optional[str] = Field(default=None, max_length=128,
                                    description="页码标识，如「卷一·頁三」")
    old_text: str = Field(min_length=1, max_length=MAX_CHARS,
                          description="旧稿全文（≤300 字）")
    new_text: str = Field(min_length=1, max_length=MAX_CHARS,
                          description="校订稿全文（≤300 字）")
    annotations: list[AnnotationIn] = Field(default_factory=list,
                                            max_length=500,
                                            description="旧稿上的人名标注")

    @model_validator(mode="after")
    def check_intervals(self) -> "PageCreate":
        n = len(self.old_text)
        for a in self.annotations:
            if not (0 <= a.start < a.end <= n):
                raise ValueError(
                    f"标注「{a.name}」的旧稿区间 [{a.start}, {a.end}) "
                    f"超出范围（旧稿长 {n}）"
                )
        return self


class ConfirmIn(BaseModel):
    new_start: int = Field(ge=0, description="编辑确认的新区间起点（含）")
    new_end: int = Field(gt=0, description="编辑确认的新区间终点（不含）")


class AnnotationOut(BaseModel):
    id: int
    name: str
    old_start: int
    old_end: int
    old_excerpt: str = Field(description="旧稿区间文字")
    status: str = Field(description="auto=自动可用 / pending=待核 / confirmed=编辑确认")
    new_start: Optional[int] = None
    new_end: Optional[int] = None
    new_excerpt: Optional[str] = Field(default=None, description="新稿区间文字")
    candidates: Optional[list[list[int]]] = Field(
        default=None,
        description="待核标注在当前最短对齐约束下仍可能的新区间列表 [start, end)")


class PageOut(BaseModel):
    id: int
    page_ref: Optional[str]
    old_text: str
    new_text: str
    edit_ops: int = Field(description="插入+删除总次数（最短对齐的代价）")
    version: int = Field(description="每次确认生效后 +1")
    usable_count: int = Field(description="可用标注数（auto + confirmed）")
    pending_count: int = Field(description="待核标注数")
    annotations: list[AnnotationOut]


class PageSummary(BaseModel):
    id: int
    page_ref: Optional[str]
    edit_ops: int
    version: int
    usable_count: int
    pending_count: int
    created_at: str


class IndexItem(BaseModel):
    annotation_id: int
    name: str
    start: int = Field(description="新稿区间起点（含）")
    end: int = Field(description="新稿区间终点（不含）")
    excerpt: str = Field(description="新稿区间文字")
    source: str = Field(description="auto=自动迁移 / confirmed=编辑确认")


class PendingItem(BaseModel):
    annotation_id: int
    name: str
    old_start: int
    old_end: int
    candidates: list[list[int]]


class IndexOut(BaseModel):
    """提供给检索库的视图：只含可用标注及其新区间。"""
    page_id: int
    page_ref: Optional[str]
    items: list[IndexItem]
    pending: list[PendingItem] = Field(description="待核标注（不进入检索库）")
