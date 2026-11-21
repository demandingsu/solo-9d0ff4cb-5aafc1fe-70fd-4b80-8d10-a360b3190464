"""对齐核心：以「插入 + 删除总次数最少」为准的最短对齐。

插入+删除总次数 = (n - L) + (m - L) = n + m - 2L，其中 L 为最长公共子序列
（LCS）长度，n、m 为新旧两稿长度。因此「最短对齐」恰好就是全部 LCS 对齐
（改字视为一删一插，不会更优）。

最短对齐的数量可能随文本长度指数增长，本模块不做枚举，而是用前/后缀 LCS
动态规划表，对每条旧标注直接判断：

    在所有（符合已有确认约束的）最短对齐中，该标注的旧稿字符是否都逐字
    对应新稿的同一连续区间。

满足才「可用」（提供新区间），否则「待核」。

编辑确认把旧区间 [s, e) 钉到新区间 [ns, ne)，等价于加入一组锚点
(s+k, ns+k)。锚点把两稿切成若干独立区间，区间之间的 LCS 互不影响，
因此「符合全部确认的最短对齐」= 各区间最短对齐的笛卡尔积。
"""

from __future__ import annotations


def lcs_tables(a: str, b: str) -> tuple[list[list[int]], list[list[int]]]:
    """返回 (f, g)：f[i][j] = LCS(a[:i], b[:j])，g[i][j] = LCS(a[i:], b[j:])。"""
    n, m = len(a), len(b)
    f = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        ai = a[i - 1]
        row, prev = f[i], f[i - 1]
        for j in range(1, m + 1):
            if ai == b[j - 1]:
                row[j] = prev[j - 1] + 1
            else:
                x, y = prev[j], row[j - 1]
                row[j] = x if x >= y else y
    g = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        ai = a[i]
        row, nxt = g[i], g[i + 1]
        for j in range(m - 1, -1, -1):
            if ai == b[j]:
                row[j] = nxt[j + 1] + 1
            else:
                x, y = nxt[j], row[j + 1]
                row[j] = x if x >= y else y
    return f, g


def lcs_len(a: str, b: str) -> int:
    """最长公共子序列长度。"""
    n, m = len(a), len(b)
    prev = [0] * (m + 1)
    for i in range(1, n + 1):
        cur = [0] * (m + 1)
        ai = a[i - 1]
        for j in range(1, m + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
            else:
                x, y = prev[j], cur[j - 1]
                cur[j] = x if x >= y else y
        prev = cur
    return prev[m]


class _Segment:
    """相邻锚点之间（或首尾之外）的独立对齐区间。"""

    __slots__ = ("a_lo", "a_hi", "b_lo", "b_hi", "a", "b", "f", "g", "L",
                 "row_del", "row_match")

    def __init__(self, old: str, a_lo: int, a_hi: int,
                 new: str, b_lo: int, b_hi: int) -> None:
        self.a_lo, self.a_hi = a_lo, a_hi
        self.b_lo, self.b_hi = b_lo, b_hi
        self.a = old[a_lo:a_hi]
        self.b = new[b_lo:b_hi]
        self.f, self.g = lcs_tables(self.a, self.b)
        self.L = self.f[len(self.a)][len(self.b)]
        n, m = len(self.a), len(self.b)
        f, g, L = self.f, self.g, self.L
        # row_del[i]：删去旧稿第 i 字后仍能取得的最长公共子序列长度。
        # 若 == L，说明存在不保留该字的最短对齐。
        self.row_del = [0] * n
        # row_match[i]：旧稿第 i 字在所有最短对齐中可能对应的新稿位置。
        self.row_match: list[list[int]] = [[] for _ in range(n)]
        for i in range(n):
            fi, gi1 = f[i], g[i + 1]
            best = 0
            for j in range(m + 1):
                v = fi[j] + gi1[j]
                if v > best:
                    best = v
            self.row_del[i] = best
            ai = self.a[i]
            hits = []
            for j in range(m):
                if ai == self.b[j] and fi[j] + 1 + g[i + 1][j + 1] == L:
                    hits.append(j)
            self.row_match[i] = hits

    def pair_forced(self, i: int, j: int) -> bool:
        """本区间所有最短对齐中，旧稿第 i 字是否必然对应新稿第 j 字（局部下标）。"""
        if not (0 <= i < len(self.a) and 0 <= j < len(self.b)):
            return False
        if self.a[i] != self.b[j]:
            return False
        if self.f[i][j] + 1 + self.g[i + 1][j + 1] != self.L:
            return False  # 该对应不出现在任何最短对齐中
        if self.row_del[i] >= self.L:
            return False  # 存在删去该字的最短对齐
        return len(self.row_match[i]) == 1  # 该字的对应位置唯一


class PageAlignment:
    """一页新旧稿的对齐视图，受已有确认（锚点）约束。

    anchors: [(旧稿下标, 新稿下标)]，必须递增且同字（由调用方保证，
    见 anchors_consistent）。
    """

    def __init__(self, old: str, new: str, anchors: list[tuple[int, int]]) -> None:
        self.old, self.new = old, new
        self.anchor_map = dict(anchors)
        self.anchors = sorted(self.anchor_map.items())
        self.segments: list[_Segment] = []
        # 旧稿每个字符位置所属的区间序号（锚点位置为 -1）
        self.pos_seg = [-1] * len(old)
        pi, pj = -1, -1
        for ai, aj in self.anchors + [(len(old), len(new))]:
            idx = len(self.segments)
            self.segments.append(_Segment(old, pi + 1, ai, new, pj + 1, aj))
            for p in range(pi + 1, ai):
                self.pos_seg[p] = idx
            pi, pj = ai, aj
        # 符合全部确认的最短对齐的公共子序列长度（无锚点时即 LCS 长度）
        self.total = sum(s.L for s in self.segments) + len(self.anchors)

    def candidates(self, s: int, e: int) -> list[int]:
        """旧区间 [s, e) 在所有符合确认的最短对齐中，可能的连续新区间起点。"""
        old, new = self.old, self.new
        ln = e - s
        anchor_map = self.anchor_map
        inside = [i for i in range(s, e) if i in anchor_map]
        if not inside:
            seg = self.segments[self.pos_seg[s]]
            sl, el = s - seg.a_lo, e - seg.a_lo
            text = old[s:e]
            f, g, L, m = seg.f, seg.g, seg.L, len(seg.b)
            out = []
            for j0 in range(0, m - ln + 1):
                if seg.b[j0:j0 + ln] == text and f[sl][j0] + ln + g[el][j0 + ln] == L:
                    out.append(seg.b_lo + j0)
            return out
        # 标注覆盖锚点：锚点直接钉死新区间起点
        j0: int | None = None
        for i in inside:
            cand = anchor_map[i] - (i - s)
            if j0 is None:
                j0 = cand
            elif j0 != cand:
                return []  # 锚点之间本身矛盾
        assert j0 is not None
        if j0 < 0 or j0 + ln > len(new) or new[j0:j0 + ln] != old[s:e]:
            return []
        # 逐段验证非锚点部分也能落在同一最短对齐里
        k = 0
        while k < ln:
            i = s + k
            if i in anchor_map:
                k += 1
                continue
            k2 = k
            while k2 < ln and (s + k2) not in anchor_map:
                k2 += 1
            seg = self.segments[self.pos_seg[i]]
            sl = i - seg.a_lo
            el = (s + k2) - seg.a_lo
            j1 = j0 + k - seg.b_lo
            run = k2 - k
            if not (0 <= j1 and j1 + run <= len(seg.b)):
                return []
            if seg.f[sl][j1] + run + seg.g[el][j1 + run] != seg.L:
                return []
            k = k2
        return [j0]

    def is_forced(self, s: int, e: int, j0: int) -> bool:
        """在所有符合确认的最短对齐中，[s, e) 是否都逐字对应 [j0, j0+e-s)。"""
        anchor_map = self.anchor_map
        for k in range(e - s):
            i = s + k
            if i in anchor_map:
                if anchor_map[i] != j0 + k:
                    return False
                continue
            seg = self.segments[self.pos_seg[i]]
            if not seg.pair_forced(i - seg.a_lo, j0 + k - seg.b_lo):
                return False
        return True


def anchors_consistent(old: str, new: str, lcs_total: int,
                       anchors: list[tuple[int, int]]) -> bool:
    """全部锚点（确认）是否仍能落在原本的某一条最短对齐里。

    即：存在至少一个原本的最短对齐同时满足全部确认。
    """
    pairs = sorted(set(anchors))
    for (i1, j1), (i2, j2) in zip(pairs, pairs[1:]):
        if i2 <= i1 or j2 <= j1:
            return False  # 交叉，或同一字被钉到两个位置
    for i, j in pairs:
        if not (0 <= i < len(old) and 0 <= j < len(new)) or old[i] != new[j]:
            return False
    total = len(pairs)
    pi, pj = -1, -1
    for i, j in pairs + [(len(old), len(new))]:
        total += lcs_len(old[pi + 1:i], new[pj + 1:j])
        pi, pj = i, j
    return total == lcs_total


def evaluate_page(old: str, new: str,
                  annotations: list[dict],
                  confirmed_map: dict[int, tuple[int, int]]):
    """评估一页全部标注。

    annotations: [{"key": ..., "start": s, "end": e}]，key 为任意可哈希标识。
    confirmed_map: key -> (new_start, new_end)，已获编辑确认的标注。

    返回 (results, alignment)：
    results[key] = (status, new_start, new_end, candidates)
      status ∈ {"auto", "pending", "confirmed"}；pending 时 candidates 为
      当前仍可能的新区间起点列表（供编辑参考）。
    """
    anchors: list[tuple[int, int]] = []
    by_key = {a["key"]: a for a in annotations}
    for key, (ns, ne) in confirmed_map.items():
        a = by_key[key]
        for t in range(ne - ns):
            anchors.append((a["start"] + t, ns + t))
    aln = PageAlignment(old, new, anchors)
    results = {}
    for a in annotations:
        key, s, e = a["key"], a["start"], a["end"]
        cf = confirmed_map.get(key)
        if cf is not None:
            results[key] = ("confirmed", cf[0], cf[1], None)
            continue
        cands = aln.candidates(s, e)
        if len(cands) == 1 and aln.is_forced(s, e, cands[0]):
            results[key] = ("auto", cands[0], cands[0] + (e - s), None)
        else:
            results[key] = ("pending", None, None, cands)
    return results, aln
