"""地方志校订稿人名标注迁移 API。

规则概要：
- 逐页提交新旧两稿（各 ≤300 字）与旧稿人名标注的字符区间；
- 以「插入+删除总次数最少」的对齐为准（即全部 LCS 对齐）；
- 只有在所有符合已有确认的最短对齐中，旧标注都逐字对应新稿同一连续
  区间，才视为可用并连同新区间提供给检索库；其余标为「待核」；
- 编辑确认一条待核标注的新区间后，重新判断本页全部标注；
- 新确认只有在原本的最短对齐中仍有一种符合全部确认时才生效，
  冲突提交（含并发冲突）一律退回并保留当前结果。
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Path

from . import db
from .align import anchors_consistent, evaluate_page
from .models import (AnnotationOut, ConfirmIn, IndexItem, IndexOut, PageCreate,
                     PageOut, PageSummary, PendingItem)

# 进程内写锁：确认 → 重判 → 落库为一条临界区，并发提交串行化，
# 后到者基于最新结果重新校验，冲突即退回。
CONFIRM_LOCK = threading.Lock()

# ---------------------------------------------------------------- 示例数据
DUP_PAGE = {
    "page_ref": "卷一·頁三",
    "old_text": "張三還書，張三攜酒。",
    "new_text": "張三還書，張三張三攜酒。",
    "annotations": [
        {"name": "張三", "start": 0, "end": 2},
        {"name": "張三", "start": 5, "end": 7},
    ],
}
SWAP_PAGE = {
    "page_ref": "卷二·頁七",
    "old_text": "張三和李四",
    "new_text": "李四和張三",
    "annotations": [
        {"name": "張三", "start": 0, "end": 2},
        {"name": "李四", "start": 3, "end": 5},
    ],
}
PAGES_EXAMPLES = {
    "重复人名": {
        "summary": "重复人名：一处可定、一处待核",
        "description": "校订稿衍出重文「張三」。第一处（[0,2)）在所有最短对齐中"
                       "位置唯一 → 可用；第二处（[5,7)）可对应新稿 [5,7) 或 "
                       "[7,9) → 待核，candidates 会列出两个候选区间。",
        "value": DUP_PAGE,
    },
    "确认冲突": {
        "summary": "确认冲突：两人换位，两条确认互相排斥",
        "description": "「張三和李四」改作「李四和張三」，两条标注均待核。先确认"
                       "「張三」→ [3,5) 生效后，再确认「李四」→ [0,2) 必与之交叉，"
                       "被退回（409）且保留当前结果。",
        "value": SWAP_PAGE,
    },
}
CONFIRM_EXAMPLES = {
    "确认待核标注": {
        "summary": "确认待核标注（重复人名示例）",
        "description": "对「重复人名」页中待核的第二条張三（[5,7)），确认其对应"
                       "新稿 [7,9)。该区间是原本最短对齐之一，故生效。",
        "value": {"new_start": 7, "new_end": 9},
    },
    "冲突确认": {
        "summary": "冲突确认（换位示例，应得 409）",
        "description": "对「确认冲突」页：先把張三确认为 [3,5)，再对李四提交 "
                       "[0,2)。两条确认交叉，原本的最短对齐无法同时满足，"
                       "返回 409 并保留当前结果。",
        "value": {"new_start": 0, "new_end": 2},
    },
}


# ---------------------------------------------------------------- 应用
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="地方志校订稿人名标注迁移",
    version="1.0.0",
    lifespan=lifespan,
    description=(
        "逐页把旧稿人名标注迁移到校订稿：以插入+删除总次数最少的对齐为准；"
        "只有在所有符合已有确认的最短对齐中都逐字对应新稿同一连续区间的标注，"
        "才连同新区间提供给检索库，其余标为待核；编辑确认后重新判断，"
        "冲突提交（含并发）退回并保留当前结果。\n\n"
        "演示建议：先用 `POST /pages` 的「重复人名」示例建页，再对其待核标注"
        "调用 `POST /annotations/{id}/confirm`；然后用「确认冲突」示例建页，"
        "体会两条确认互相排斥时的 409。"
    ),
)

db.init_db()  # 兼容不触发 lifespan 的运行方式（如部分测试客户端）


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"service": "地方志校订稿人名标注迁移", "docs": "/docs"}


# ---------------------------------------------------------------- 内部函数
def _load_page_or_404(conn, page_id: int):
    page = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
    if page is None:
        raise HTTPException(status_code=404, detail="页不存在")
    return page


def _evaluate_stored(page, anns, confs):
    """对库中一页的标注重跑判定，返回 {annotation_id: (status, ns, ne, cands)}。"""
    confirmed_map = {c["annotation_id"]: (c["new_start"], c["new_end"])
                     for c in confs}
    ann_dicts = [{"key": a["id"], "start": a["old_start"], "end": a["old_end"]}
                 for a in anns]
    results, _ = evaluate_page(page["old_text"], page["new_text"],
                               ann_dicts, confirmed_map)
    return results


def _page_state(page_id: int) -> PageOut:
    conn = db.connect()
    try:
        page = _load_page_or_404(conn, page_id)
        anns = conn.execute(
            "SELECT * FROM annotations WHERE page_id = ? ORDER BY seq, id",
            (page_id,)).fetchall()
        confs = conn.execute(
            "SELECT * FROM confirmations WHERE page_id = ?", (page_id,)).fetchall()
    finally:
        conn.close()
    results = _evaluate_stored(page, anns, confs)
    out_anns: list[AnnotationOut] = []
    usable = pending = 0
    for a in anns:
        status = a["status"]
        ns, ne = a["new_start"], a["new_end"]
        cands = None
        if status == "pending":
            pending += 1
            ln = a["old_end"] - a["old_start"]
            cands = [[c, c + ln] for c in (results[a["id"]][3] or [])]
        else:
            usable += 1
        out_anns.append(AnnotationOut(
            id=a["id"], name=a["name"],
            old_start=a["old_start"], old_end=a["old_end"],
            old_excerpt=page["old_text"][a["old_start"]:a["old_end"]],
            status=status, new_start=ns, new_end=ne,
            new_excerpt=page["new_text"][ns:ne] if ns is not None else None,
            candidates=cands))
    return PageOut(
        id=page["id"], page_ref=page["page_ref"],
        old_text=page["old_text"], new_text=page["new_text"],
        edit_ops=page["edit_ops"], version=page["version"],
        usable_count=usable, pending_count=pending, annotations=out_anns)


def _recompute_and_store(conn, page) -> None:
    """确认生效后重判本页全部标注并落库（单调：可用不会变回待核）。"""
    anns = conn.execute(
        "SELECT * FROM annotations WHERE page_id = ? ORDER BY seq, id",
        (page["id"],)).fetchall()
    confs = conn.execute(
        "SELECT * FROM confirmations WHERE page_id = ?", (page["id"],)).fetchall()
    results = _evaluate_stored(page, anns, confs)
    for a in anns:
        status, ns, ne, _ = results[a["id"]]
        conn.execute(
            "UPDATE annotations SET status = ?, new_start = ?, new_end = ? "
            "WHERE id = ?", (status, ns, ne, a["id"]))


# ---------------------------------------------------------------- 接口
@app.post("/pages", response_model=PageOut, status_code=201,
          summary="提交一页新旧两稿与旧标注，完成迁移判定",
          tags=["迁移"])
def create_page(payload: PageCreate = Body(..., openapi_examples=PAGES_EXAMPLES)):
    old, new = payload.old_text, payload.new_text
    ann_dicts = [{"key": i, "start": a.start, "end": a.end}
                 for i, a in enumerate(payload.annotations)]
    results, aln = evaluate_page(old, new, ann_dicts, {})
    edit_ops = len(old) + len(new) - 2 * aln.total
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT INTO pages (page_ref, old_text, new_text, lcs_len, edit_ops) "
            "VALUES (?, ?, ?, ?, ?)",
            (payload.page_ref, old, new, aln.total, edit_ops))
        page_id = cur.lastrowid
        for i, a in enumerate(payload.annotations):
            status, ns, ne, _ = results[i]
            conn.execute(
                "INSERT INTO annotations (page_id, seq, name, old_start, old_end,"
                " status, new_start, new_end) VALUES (?,?,?,?,?,?,?,?)",
                (page_id, i, a.name, a.start, a.end, status, ns, ne))
        conn.commit()
    finally:
        conn.close()
    return _page_state(page_id)


@app.get("/pages", response_model=list[PageSummary],
         summary="列出全部页", tags=["迁移"])
def list_pages():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT p.id, p.page_ref, p.edit_ops, p.version, p.created_at,"
            " SUM(CASE WHEN a.status = 'pending' THEN 1 ELSE 0 END) AS pending,"
            " SUM(CASE WHEN a.status != 'pending' THEN 1 ELSE 0 END) AS usable"
            " FROM pages p LEFT JOIN annotations a ON a.page_id = p.id"
            " GROUP BY p.id ORDER BY p.id").fetchall()
    finally:
        conn.close()
    return [PageSummary(id=r["id"], page_ref=r["page_ref"],
                        edit_ops=r["edit_ops"], version=r["version"],
                        usable_count=r["usable"] or 0,
                        pending_count=r["pending"] or 0,
                        created_at=r["created_at"]) for r in rows]


@app.get("/pages/{page_id}", response_model=PageOut,
         summary="查看一页当前判定结果", tags=["迁移"])
def get_page(page_id: int = Path(ge=1)):
    return _page_state(page_id)


@app.get("/pages/{page_id}/index", response_model=IndexOut,
         summary="检索库视图：仅含可用标注及其新区间",
         tags=["检索库"])
def page_index(page_id: int = Path(ge=1)):
    state = _page_state(page_id)
    items = [IndexItem(annotation_id=a.id, name=a.name,
                       start=a.new_start, end=a.new_end,
                       excerpt=a.new_excerpt, source=a.status)
             for a in state.annotations if a.status in ("auto", "confirmed")]
    pending_items = [PendingItem(annotation_id=a.id, name=a.name,
                                 old_start=a.old_start, old_end=a.old_end,
                                 candidates=a.candidates or [])
                     for a in state.annotations if a.status == "pending"]
    return IndexOut(page_id=state.id, page_ref=state.page_ref,
                    items=items, pending=pending_items)


@app.post("/annotations/{annotation_id}/confirm", response_model=PageOut,
          summary="编辑确认一条待核标注的新区间，并重判本页",
          tags=["确认"],
          responses={409: {"description": "冲突：已退回并保留当前结果"}})
def confirm_annotation(payload: ConfirmIn = Body(..., openapi_examples=CONFIRM_EXAMPLES),
                       annotation_id: int = Path(ge=1)):
    page_id = None
    with CONFIRM_LOCK:
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")  # 库级写锁，与进程锁双保险
            ann = conn.execute("SELECT * FROM annotations WHERE id = ?",
                               (annotation_id,)).fetchone()
            if ann is None:
                raise HTTPException(status_code=404, detail="标注不存在")
            page = _load_page_or_404(conn, ann["page_id"])
            page_id = page["id"]
            if ann["status"] != "pending":
                raise HTTPException(
                    status_code=409,
                    detail=f"标注当前状态为「{ann['status']}」，仅待核标注可确认；"
                           "已保留当前结果")
            old, new = page["old_text"], page["new_text"]
            ns, ne = payload.new_start, payload.new_end
            if not (0 <= ns < ne <= len(new)):
                raise HTTPException(status_code=422,
                                    detail="新区间超出校订稿范围")
            old_ln = ann["old_end"] - ann["old_start"]
            if ne - ns != old_ln:
                raise HTTPException(
                    status_code=409,
                    detail="新区间长度与旧标注不一致，无法逐字对应；已保留当前结果")
            if new[ns:ne] != old[ann["old_start"]:ann["old_end"]]:
                raise HTTPException(
                    status_code=409,
                    detail="新区间文字与旧标注不一致，不存在符合该确认的最短对齐；"
                           "已保留当前结果")
            # 汇总全部确认（含本次）为锚点，校验是否仍落在原本的最短对齐里
            confs = conn.execute(
                "SELECT c.new_start, c.new_end, a.old_start, a.old_end"
                " FROM confirmations c"
                " JOIN annotations a ON a.id = c.annotation_id"
                " WHERE c.page_id = ?", (page_id,)).fetchall()
            anchors: list[tuple[int, int]] = []
            for c in confs:
                for t in range(c["new_end"] - c["new_start"]):
                    anchors.append((c["old_start"] + t, c["new_start"] + t))
            for t in range(ne - ns):
                anchors.append((ann["old_start"] + t, ns + t))
            if not anchors_consistent(old, new, page["lcs_len"], anchors):
                raise HTTPException(
                    status_code=409,
                    detail="与已有确认冲突：原本的最短对齐中不存在符合全部确认的"
                           "方案；已退回并保留当前结果")
            conn.execute(
                "INSERT INTO confirmations (page_id, annotation_id, new_start,"
                " new_end) VALUES (?,?,?,?)", (page_id, annotation_id, ns, ne))
            _recompute_and_store(conn, page)
            conn.execute("UPDATE pages SET version = version + 1 WHERE id = ?",
                         (page_id,))
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        finally:
            conn.close()
    return _page_state(page_id)
