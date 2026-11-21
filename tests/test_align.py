"""对齐核心（app.align）的单元测试。"""

from app.align import (PageAlignment, anchors_consistent, evaluate_page, lcs_len)


def ann(key, s, e):
    return {"key": key, "start": s, "end": e}


def test_lcs_len_basic():
    assert lcs_len("張三和李四", "李四和張三") == 2
    assert lcs_len("張三還書，張三攜酒。", "張三還書，張三張三攜酒。") == 10
    assert lcs_len("abc", "abc") == 3
    assert lcs_len("", "abc") == 0


def test_identical_text_forces_every_annotation():
    # 两稿相同：最短对齐唯一（代价 0），所有标注都逐字对应原区间
    aln = PageAlignment("張三張三", "張三張三", [])
    assert aln.candidates(0, 2) == [0]
    assert aln.is_forced(0, 2, 0)
    assert aln.candidates(2, 4) == [2]
    assert aln.is_forced(2, 4, 2)


def test_duplicate_name_yields_two_candidates():
    # 衍文「張三」：旧稿第二处張三可对应新稿 [5,7) 或 [7,9)
    old, new = "張三還書，張三攜酒。", "張三還書，張三張三攜酒。"
    aln = PageAlignment(old, new, [])
    assert aln.total == 10  # 插入 2 字：edit_ops = 10 + 12 - 20 = 2
    assert aln.candidates(0, 2) == [0]
    assert aln.is_forced(0, 2, 0)
    assert sorted(aln.candidates(5, 7)) == [5, 7]
    assert not aln.is_forced(5, 7, 5)
    assert not aln.is_forced(5, 7, 7)


def test_anchor_narrows_duplicate_to_confirmed_interval():
    old, new = "張三還書，張三攜酒。", "張三還書，張三張三攜酒。"
    # 确认 [5,7) -> [7,9) 后，锚点 (5,7),(6,8) 钉死；第一处仍唯一可用
    aln = PageAlignment(old, new, [(5, 7), (6, 8)])
    assert aln.candidates(5, 7) == [7]
    assert aln.is_forced(5, 7, 7)
    assert aln.candidates(0, 2) == [0]
    assert aln.is_forced(0, 2, 0)


def test_swapped_names_both_pending_and_crossing_anchors_rejected():
    old, new = "張三和李四", "李四和張三"
    results, aln = evaluate_page(old, new, [ann("z", 0, 2), ann("l", 3, 5)], {})
    assert results["z"] == ("pending", None, None, [3])
    assert results["l"] == ("pending", None, None, [0])
    # 各自单独确认都合法
    assert anchors_consistent(old, new, aln.total, [(0, 3), (1, 4)])
    assert anchors_consistent(old, new, aln.total, [(3, 0), (4, 1)])
    # 但两组确认交叉，不能同时成立
    assert not anchors_consistent(old, new, aln.total,
                                  [(0, 3), (1, 4), (3, 0), (4, 1)])


def test_confirmation_makes_other_annotation_resolvable():
    # 重文衍字：两处「張三」互相摇摆；确认其一后，另一个随之唯一
    old, new = "張三張三", "張三張三張三"
    results, _ = evaluate_page(old, new, [ann("a", 0, 2), ann("b", 2, 4)], {})
    assert results["a"] == ("pending", None, None, [0, 2])
    assert results["b"] == ("pending", None, None, [2, 4])
    results2, _ = evaluate_page(old, new, [ann("a", 0, 2), ann("b", 2, 4)],
                                {"a": (2, 4)})
    assert results2["a"] == ("confirmed", 2, 4, None)
    assert results2["b"] == ("auto", 4, 6, None)


def test_deleted_annotation_has_no_candidate():
    # 校订稿删去整段：标注无合法新区间，只能待核
    old, new = "張三攜酒。", "攜酒。"
    results, _ = evaluate_page(old, new, [ann("z", 0, 2)], {})
    assert results["z"] == ("pending", None, None, [])


def test_non_continuous_match_is_not_usable():
    # 新稿中间插字：旧标注虽字字有对应，但构不成连续区间 → 待核
    old, new = "甲乙", "甲X乙"
    results, _ = evaluate_page(old, new, [ann("x", 0, 2)], {})
    assert results["x"] == ("pending", None, None, [])


def test_anchors_consistent_rejects_text_mismatch_and_out_of_range():
    old, new = "張三和李四", "李四和張三"
    total = lcs_len(old, new)
    assert not anchors_consistent(old, new, total, [(0, 0)])   # 張 != 李
    assert not anchors_consistent(old, new, total, [(0, 99)])  # 越界
    # 锚点列表无需有序，函数内部会排序后判定（(0,3),(1,4) 即張三→[3,5)，合法）
    assert anchors_consistent(old, new, total, [(1, 4), (0, 3)])


def test_max_length_page_performance():
    # 300 字上限的边界：构造大改动的两稿，确认评估可快速完成
    old = ("春去花還在，人來鳥不驚。" * 26)[:300]
    new = ("春去花猶在，人來鳥自驚。" * 26)[:300]
    assert len(old) == len(new) == 300
    anns = [ann(i, i * 3, i * 3 + 2) for i in range(100)]
    results, aln = evaluate_page(old, new, anns, {})
    assert aln.total == lcs_len(old, new)
    assert len(results) == 100
    # 未触及改字的标注唯一可定；盖住改字的标注无连续对应 → 待核
    assert results[0] == ("auto", 0, 2, None)          # 「春去」
    assert results[1] == ("pending", None, None, [])   # 「還在」：校订稿作「猶在」
