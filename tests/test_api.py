"""API 端到端测试：迁移判定、确认、冲突与并发。"""

import threading
import time

import httpx
import uvicorn

from app.main import app
from tests.conftest import DUP_PAGE, SWAP_PAGE


# ---------------------------------------------------------------- 工具
def _serve(port: int):
    """在本进程后台线程起一个真实 uvicorn 服务，用于并发测试。"""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # 非主线程不能装信号处理
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn 未能启动")
        time.sleep(0.02)
    return server, thread


def _ann(page, name, status=None):
    for a in page["annotations"]:
        if a["name"] == name and (status is None or a["status"] == status):
            return a
    raise AssertionError(f"未找到标注 {name} ({status})")


# ---------------------------------------------------------------- 基本判定
def test_create_page_duplicate_names(client):
    r = client.post("/pages", json=DUP_PAGE)
    assert r.status_code == 201, r.text
    page = r.json()
    assert page["edit_ops"] == 2  # 衍文「張三」两字
    assert page["usable_count"] == 1 and page["pending_count"] == 1
    first, second = page["annotations"]
    assert first["status"] == "auto"
    assert (first["new_start"], first["new_end"]) == (0, 2)
    assert first["new_excerpt"] == "張三"
    assert second["status"] == "pending"
    assert second["new_start"] is None
    assert second["candidates"] == [[5, 7], [7, 9]]


def test_index_only_exposes_usable_annotations(client):
    page = client.post("/pages", json=DUP_PAGE).json()
    idx = client.get(f"/pages/{page['id']}/index").json()
    assert [it["name"] for it in idx["items"]] == ["張三"]
    assert (idx["items"][0]["start"], idx["items"][0]["end"]) == (0, 2)
    assert idx["items"][0]["source"] == "auto"
    assert len(idx["pending"]) == 1
    assert idx["pending"][0]["candidates"] == [[5, 7], [7, 9]]


def test_identical_text_all_auto(client):
    payload = {"old_text": "張三攜酒。", "new_text": "張三攜酒。",
               "annotations": [{"name": "張三", "start": 0, "end": 2}]}
    page = client.post("/pages", json=payload).json()
    assert page["edit_ops"] == 0
    assert page["annotations"][0]["status"] == "auto"
    assert (page["annotations"][0]["new_start"],
            page["annotations"][0]["new_end"]) == (0, 2)


# ---------------------------------------------------------------- 确认流程
def test_confirm_resolves_pending_and_reevaluates(client):
    page = client.post("/pages", json=DUP_PAGE).json()
    target = _ann(page, "張三", "pending")
    r = client.post(f"/annotations/{target['id']}/confirm",
                    json={"new_start": 7, "new_end": 9})
    assert r.status_code == 200, r.text
    page2 = r.json()
    assert page2["version"] == page["version"] + 1
    assert page2["pending_count"] == 0 and page2["usable_count"] == 2
    confirmed = _ann(page2, "張三", "confirmed")
    assert (confirmed["new_start"], confirmed["new_end"]) == (7, 9)
    assert confirmed["new_excerpt"] == "張三"
    idx = client.get(f"/pages/{page['id']}/index").json()
    assert len(idx["items"]) == 2
    assert {it["source"] for it in idx["items"]} == {"auto", "confirmed"}


def test_confirm_non_pending_rejected(client):
    page = client.post("/pages", json=DUP_PAGE).json()
    target = _ann(page, "張三", "pending")
    ok = client.post(f"/annotations/{target['id']}/confirm",
                     json={"new_start": 7, "new_end": 9})
    assert ok.status_code == 200
    again = client.post(f"/annotations/{target['id']}/confirm",
                        json={"new_start": 7, "new_end": 9})
    assert again.status_code == 409
    auto_one = _ann(page, "張三", "auto")
    r = client.post(f"/annotations/{auto_one['id']}/confirm",
                    json={"new_start": 0, "new_end": 2})
    assert r.status_code == 409


def test_confirm_text_mismatch_rejected(client):
    page = client.post("/pages", json=DUP_PAGE).json()
    target = _ann(page, "張三", "pending")
    r = client.post(f"/annotations/{target['id']}/confirm",
                    json={"new_start": 2, "new_end": 4})  # 新稿作「還書」
    assert r.status_code == 409
    assert "不一致" in r.json()["detail"]


def test_confirm_not_in_shortest_alignment_rejected(client):
    # 文字雖對得上，但該對應不在任何原本的最短對齊裡
    page = client.post("/pages", json=DUP_PAGE).json()
    target = _ann(page, "張三", "pending")
    r = client.post(f"/annotations/{target['id']}/confirm",
                    json={"new_start": 0, "new_end": 2})
    assert r.status_code == 409
    # 状态保持不变
    after = client.get(f"/pages/{page['id']}").json()
    assert _ann(after, "張三", "pending")["id"] == target["id"]
    assert after["version"] == page["version"]


def test_confirm_length_mismatch_rejected(client):
    page = client.post("/pages", json=DUP_PAGE).json()
    target = _ann(page, "張三", "pending")
    r = client.post(f"/annotations/{target['id']}/confirm",
                    json={"new_start": 5, "new_end": 10})
    assert r.status_code == 409


def test_swap_page_sequential_conflict(client):
    page = client.post("/pages", json=SWAP_PAGE).json()
    assert page["pending_count"] == 2
    zs = _ann(page, "張三", "pending")
    ls = _ann(page, "李四", "pending")
    r1 = client.post(f"/annotations/{zs['id']}/confirm",
                     json={"new_start": 3, "new_end": 5})
    assert r1.status_code == 200, r1.text
    # 第二条确认与第一条交叉：退回并保留当前结果
    r2 = client.post(f"/annotations/{ls['id']}/confirm",
                     json={"new_start": 0, "new_end": 2})
    assert r2.status_code == 409
    after = client.get(f"/pages/{page['id']}").json()
    assert _ann(after, "張三")["status"] == "confirmed"
    assert _ann(after, "李四")["status"] == "pending"
    assert after["version"] == page["version"] + 1  # 冲突提交没有推进版本
    idx = client.get(f"/pages/{page['id']}/index").json()
    assert [it["name"] for it in idx["items"]] == ["張三"]


# ---------------------------------------------------------------- 参数校验
def test_page_over_300_chars_rejected(client):
    payload = {"old_text": "甲" * 301, "new_text": "甲" * 300, "annotations": []}
    assert client.post("/pages", json=payload).status_code == 422
    payload = {"old_text": "甲" * 300, "new_text": "乙" * 301, "annotations": []}
    assert client.post("/pages", json=payload).status_code == 422
    payload = {"old_text": "甲" * 300, "new_text": "甲" * 300, "annotations": []}
    assert client.post("/pages", json=payload).status_code == 201


def test_annotation_interval_validated(client):
    base = {"old_text": "張三。", "new_text": "張三。"}
    bad1 = dict(base, annotations=[{"name": "張三", "start": 0, "end": 4}])
    bad2 = dict(base, annotations=[{"name": "張三", "start": 2, "end": 2}])
    bad3 = dict(base, annotations=[{"name": "張三", "start": -1, "end": 2}])
    for p in (bad1, bad2, bad3):
        assert client.post("/pages", json=p).status_code == 422


def test_missing_page_and_annotation_404(client):
    assert client.get("/pages/999").status_code == 404
    assert client.post("/annotations/999/confirm",
                       json={"new_start": 0, "new_end": 1}).status_code == 404


def test_list_pages(client):
    client.post("/pages", json=DUP_PAGE)
    client.post("/pages", json=SWAP_PAGE)
    rows = client.get("/pages").json()
    assert len(rows) == 2
    assert rows[0]["pending_count"] == 1 and rows[1]["pending_count"] == 2


# ---------------------------------------------------------------- 并发
def test_concurrent_confirm_same_annotation(client):
    """两个客户端同时确认同一条待核标注：恰一个生效，另一个退回。"""
    page = client.post("/pages", json=DUP_PAGE).json()
    target = _ann(page, "張三", "pending")
    server, thread = _serve(8321)
    try:
        codes = []

        def hit():
            with httpx.Client(base_url="http://127.0.0.1:8321",
                              timeout=15) as h:
                r = h.post(f"/annotations/{target['id']}/confirm",
                           json={"new_start": 7, "new_end": 9})
                codes.append(r.status_code)

        ts = [threading.Thread(target=hit) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert sorted(codes) == [200, 409, 409, 409]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    after = client.get(f"/pages/{page['id']}").json()
    assert after["version"] == page["version"] + 1
    assert _ann(after, "張三", "confirmed")


def test_concurrent_cross_annotation_conflict(client):
    """换位页上两条互斥确认同时提交：恰一个生效，结果保持一致。"""
    page = client.post("/pages", json=SWAP_PAGE).json()
    zs = _ann(page, "張三", "pending")
    ls = _ann(page, "李四", "pending")
    server, thread = _serve(8322)
    try:
        results = {}

        def hit(ann_id, ns, ne, tag):
            with httpx.Client(base_url="http://127.0.0.1:8322",
                              timeout=15) as h:
                r = h.post(f"/annotations/{ann_id}/confirm",
                           json={"new_start": ns, "new_end": ne})
                results[tag] = r.status_code

        t1 = threading.Thread(target=hit, args=(zs["id"], 3, 5, "zs"))
        t2 = threading.Thread(target=hit, args=(ls["id"], 0, 2, "ls"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # 两条确认互斥：一个 200、一个 409
        assert sorted(results.values()) == [200, 409]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    after = client.get(f"/pages/{page['id']}").json()
    statuses = {a["name"]: a["status"] for a in after["annotations"]}
    # 恰一条 confirmed，另一条仍 pending，且与获胜方一致
    assert sorted(statuses.values()) == ["confirmed", "pending"]
    winner = "張三" if results["zs"] == 200 else "李四"
    assert statuses[winner] == "confirmed"
    assert after["version"] == page["version"] + 1
