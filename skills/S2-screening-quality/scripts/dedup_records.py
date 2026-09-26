#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dedup_records.py — 三级去重（S2-A：AI 出疑似，人工裁决）

输入
  normalize_records.py 产出的 normalized_records.csv（RECORD_FIELDS 同构）。

三级策略
  Level 1  exact_doi   —— DOI（normalize 后）完全匹配
  Level 2  exact_pmid  —— PMID（纯数字）完全匹配
  Level 3  fuzzy_title —— 标题相似度（词 bigram Jaccard，阈值从
             project.yaml → screening.dedup.title_similarity_threshold 读取）
             × first_author 匹配（author_match_required=true 时）
             × 年份差 ≤ year_tolerance（任一年份缺失时该项放行，仅凭标题判断）

边界（★ 铁律）
  - 精确命中（L1/L2）：AI 标 `duplicate`/`keep`，**从 dedup_unique.csv 剔除**，
    但 dedup_decisions.csv 里保留全部行（人工可翻案）。
  - 模糊命中（L3 ≥ 阈值）：一律标 `suspected_duplicate`，**不自动合并**、
    不从 dedup_unique.csv 剔除 —— 留给人工裁决。
  - 任何记录都不物理删除。

输出（--output-dir 下）
  dedup_decisions.csv  record_id, duplicate_group_id, duplicate_match_type,
                       kept_record_id, ai_decision, human_decision(空，人工填)
  dedup_unique.csv     剔除精确重复后的记录集（RECORD_FIELDS 同构，
                       suspected 仍在其中）
  dedup_summary.csv    metric,value 长表（总数/各级去重数/疑似数/唯一数/待人工数）

用法
  python dedup_records.py --input normalized_records.csv --config project.yaml --output-dir .
  python dedup_records.py --self-test

退出码
  0 成功   2 前置条件不满足（文件缺失 / 配置非法 / record_id 重复）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
for _p in (str(_ROOT / "shared"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from record_schema import RECORD_FIELDS, SchemaError, read_records_csv  # noqa: E402
    from screening_schema import (  # noqa: E402  ★ S2 单一来源
        DEDUP_AI_DECISIONS, DEDUP_DECISION_FIELDS, DEDUP_MATCH_TYPES,
        LOG_FIELDS, ConfigError, first_author, jaccard_bigram, load_screening_config,
        normalize_doi, normalize_pmid, normalize_text, write_csv_dicts,
    )
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/record_schema.py 或 screening_schema.py（{_exc}）",
          file=sys.stderr)
    raise SystemExit(2)


# ===========================================================================
# 1. 并查集（组聚类）
# ===========================================================================

class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[max(ri, rj)] = min(ri, rj)


def _kept_index(indices: list[int], rows: list[dict]) -> int:
    """
    AI 的保留建议（确定性，可复现）：
      ① 有摘要者优先 ② 字段更满者优先 ③ record_id 字典序最小者。
    """
    def score(i: int) -> tuple:
        r = rows[i]
        filled = sum(1 for f in RECORD_FIELDS if normalize_text(r.get(f, "")))
        return (1 if normalize_text(r.get("abstract", "")) else 0, filled,
                _invert_str(r.get("record_id", "")))
    return max(indices, key=score)


def _invert_str(s: str) -> str:
    """让 record_id 字典序最小者在 max 排序中胜出。"""
    return tuple(-ord(c) for c in s)


def build_dedup(rows: list[dict], spec: dict) -> tuple[list[dict], list[dict], dict]:
    """
    三级去重主逻辑 → (decisions 行, unique 记录, summary 计数 dict)。
    spec 来自 screening_schema.validate_screening（dedup 键已被校验）。

    两段式设计（★ 防"传递性误升级"）：
      第一段（精确）：DOI / PMID 并查集聚组 → 组内 AI 建议 keep/duplicate，
        duplicate 者**从 dedup_unique.csv 剔除**（人工可在 decisions 里翻案）。
      第二段（模糊）：仅在**幸存记录**内部做 Jaccard 聚簇 → 簇内全部成员
        （含 AI 建议保留者）标 `suspected_duplicate`，**一律不剔除、不合并**。
        这样模糊链永远不会把一条记录"升级"成精确重复而被自动丢弃。
    """
    n = len(rows)
    strength = {"": 0, "fuzzy_title": 1, "exact_pmid": 2, "exact_doi": 3}

    # ── 第一段：精确合并 ─────────────────────────────────────────────
    exact_uf = _UnionFind(n)
    exact_pair_type: dict[tuple[int, int], str] = {}

    def exact_link(i: int, j: int, mtype: str) -> None:
        exact_uf.union(i, j)
        key = (min(i, j), max(i, j))
        if strength[mtype] > strength[exact_pair_type.get(key, "")]:
            exact_pair_type[key] = mtype

    by_doi: dict[str, int] = {}
    for i, r in enumerate(rows):
        doi = normalize_doi(r.get("doi", ""))
        if doi and _DOI_SHAPE_OK(doi):
            if doi in by_doi:
                exact_link(by_doi[doi], i, "exact_doi")
            else:
                by_doi[doi] = i
    by_pmid: dict[str, int] = {}
    for i, r in enumerate(rows):
        pmid = normalize_pmid(r.get("pmid", ""))
        if pmid.isdigit():
            if pmid in by_pmid:
                exact_link(by_pmid[pmid], i, "exact_pmid")
            else:
                by_pmid[pmid] = i

    exact_groups: dict[int, list[int]] = {}
    for i in range(n):
        exact_groups.setdefault(exact_uf.find(i), []).append(i)

    dropped: set[int] = set()
    kept_of: dict[int, int] = {}          # 组内每个成员 → AI 建议保留者
    exact_mtype: dict[int, str] = {}      # 组根 → 组最强匹配类型
    for root, members in exact_groups.items():
        if len(members) == 1:
            continue
        kept = _kept_index(members, rows)
        mtype = max((exact_pair_type.get((min(a, b), max(a, b)), "")
                     for x, a in enumerate(members) for b in members[x + 1:]),
                    key=lambda m: strength[m])
        exact_mtype[root] = mtype
        for i in members:
            kept_of[i] = kept
            if i != kept:
                dropped.add(i)

    # ── 第二段：模糊聚簇（仅在幸存者中；绝不导致剔除） ────────────────
    survivors = [i for i in range(n) if i not in dropped]
    thr = spec["dedup"]["title_similarity_threshold"]
    need_author = spec["dedup"]["author_match_required"]
    year_tol = spec["dedup"]["year_tolerance"]
    if need_author:
        blocks: dict[str, list[int]] = {}
        for i in survivors:
            key = first_author(rows[i].get("authors", "")).casefold()
            if key:
                blocks.setdefault(key, []).append(i)
        candidate_pairs = [(block[a], block[b]) for block in blocks.values()
                           for a in range(len(block))
                           for b in range(a + 1, len(block))]
    else:
        candidate_pairs = [(a, b) for x, a in enumerate(survivors)
                           for b in survivors[x + 1:]]

    fuzzy_uf = _UnionFind(n)
    for i, j in candidate_pairs:
        ri, rj = rows[i], rows[j]
        sim = jaccard_bigram(ri.get("title", ""), rj.get("title", ""))
        if sim < thr:
            continue
        yi = normalize_text(ri.get("year", ""))
        yj = normalize_text(rj.get("year", ""))
        if yi.isdigit() and yj.isdigit() and abs(int(yi) - int(yj)) > year_tol:
            continue
        if need_author:
            ai_ = first_author(ri.get("authors", "")).casefold()
            aj_ = first_author(rj.get("authors", "")).casefold()
            if not ai_ or not aj_ or ai_ != aj_:
                continue
        fuzzy_uf.union(i, j)

    fuzzy_groups: dict[int, list[int]] = {}
    for i in survivors:
        fuzzy_groups.setdefault(fuzzy_uf.find(i), []).append(i)

    suspected_roots = {root for root, members in fuzzy_groups.items()
                       if len(members) > 1}

    # ── 装配输出 ────────────────────────────────────────────────────
    decisions: list[dict] = []
    unique_rows: list[dict] = []
    stats = {"total_records": n, "groups_exact_doi": 0, "groups_exact_pmid": 0,
             "groups_fuzzy": 0, "records_flagged_duplicate": 0,
             "records_flagged_suspected": 0, "records_output_unique": 0,
             "records_pending_human": 0}
    exact_roots_sorted = sorted(exact_groups.keys())
    exact_seq = {root: k + 1 for k, root in enumerate(exact_roots_sorted)}
    fuzzy_roots_sorted = sorted(suspected_roots)
    fuzzy_seq = {root: k + 1 for k, root in enumerate(fuzzy_roots_sorted)}

    for i in range(n):
        rid = rows[i].get("record_id", "")
        root = exact_uf.find(i)
        if i in dropped:
            decisions.append({
                "record_id": rid,
                "duplicate_group_id": f"EXA-{exact_seq[root]:04d}",
                "duplicate_match_type": exact_mtype[root],
                "kept_record_id": rows[kept_of[i]].get("record_id", ""),
                "ai_decision": "duplicate", "human_decision": "",
            })
            stats["records_flagged_duplicate"] += 1
            continue

        fuzzy_root = fuzzy_uf.find(i)
        if fuzzy_root in suspected_roots:
            # 模糊簇成员（含精确组保留者）：一律 suspected_duplicate（更保守），
            # 不从 dedup_unique.csv 剔除 —— 合并与否由人工裁决
            cluster = sorted(fuzzy_groups[fuzzy_root])
            kept = _kept_index(cluster, rows)
            decisions.append({
                "record_id": rid,
                "duplicate_group_id": f"DUP-{fuzzy_seq[fuzzy_root]:04d}",
                "duplicate_match_type": "fuzzy_title",
                "kept_record_id": rows[kept].get("record_id", ""),
                "ai_decision": "suspected_duplicate", "human_decision": "",
            })
            stats["records_flagged_suspected"] += 1
            unique_rows.append(rows[i])
        else:
            in_exact_group = len(exact_groups[root]) > 1
            decisions.append({
                "record_id": rid, "duplicate_group_id": "",
                "duplicate_match_type": exact_mtype[root] if in_exact_group else "",
                "kept_record_id": rows[kept_of[i]].get("record_id", "")
                if in_exact_group else "",
                "ai_decision": "keep" if in_exact_group else "unique",
                "human_decision": "",
            })
            unique_rows.append(rows[i])

    for root, members in exact_groups.items():
        if len(members) > 1:
            if exact_mtype[root] == "exact_doi":
                stats["groups_exact_doi"] += 1
            elif exact_mtype[root] == "exact_pmid":
                stats["groups_exact_pmid"] += 1
    stats["groups_fuzzy"] = len(suspected_roots)
    stats["records_output_unique"] = len(unique_rows)
    stats["records_pending_human"] = stats["records_flagged_suspected"]
    return decisions, unique_rows, stats


def _DOI_SHAPE_OK(doi: str) -> bool:
    import re
    return bool(re.match(r"^10\.\d{4,9}/\S+$", doi))


def summary_rows(stats: dict) -> list[dict]:
    return [{"metric": k, "value": str(v)} for k, v in stats.items()]


# ===========================================================================
# self-test
# ===========================================================================

def _row(rid: str, title: str, doi: str = "", pmid: str = "",
         authors: str = "Zhang, San", year: str = "2020", abstract: str = "") -> dict:
    return {"record_id": rid, "source_database": "PubMed", "title": title,
            "abstract": abstract, "authors": authors, "year": year,
            "doi": doi, "pmid": pmid, "journal": "J", "keywords": "",
            "raw_row_hash": ""}


FIXTURE_ROWS = [
    _row("pubmed-00001", "Urinary arsenic in general population of China",
         doi="10.1234/a.2020.001", abstract="has abstract"),
    _row("wos-00001", "URINARY ARSENIC IN GENERAL POPULATION OF CHINA!",
         doi="https://doi.org/10.1234/A.2020.001"),            # L1：与 1 同 DOI
    _row("scopus-00001", "Urinary arsenic in general population of China",
         pmid="99988877", authors="Li, Si", year="2020",
         abstract="has abstract too"),                         # L2：与 5 同 PMID
    _row("embase-00001", "Urinary arsenic in general population of China",
         authors="Zhang, San", year="2020"),                    # L3：与 1 同题同人同年
    _row("pubmed-00002", "Blood mercury in parturients",
         pmid="99988877"),                                      # L2：与 3 同 PMID
    _row("cnki-00001", "某省居民尿砷水平调查", authors="张三", year="2019"),  # 唯一
]


def run_self_test() -> int:
    print("=" * 70)
    print("dedup_records.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    spec = _load_spec()
    decisions, unique_rows, stats = build_dedup([dict(r) for r in FIXTURE_ROWS], spec)
    by_rid = {d["record_id"]: d for d in decisions}

    print("\n[1] 精确级（L1 DOI / L2 PMID）")
    check("L1 exact_doi：同 DOI 两记录同组（EXA-0001）",
          by_rid["pubmed-00001"]["duplicate_group_id"] in ("EXA-0001", "DUP-0001")
          and by_rid["wos-00001"]["duplicate_match_type"] == "exact_doi",
          by_rid["wos-00001"]["duplicate_group_id"])
    check("L1 被重复者标 duplicate 并给出保留建议",
          by_rid["wos-00001"]["ai_decision"] == "duplicate"
          and by_rid["wos-00001"]["kept_record_id"] == "pubmed-00001")
    check("L2 exact_pmid：同 PMID 两记录同组",
          by_rid["scopus-00001"]["duplicate_match_type"] == "exact_pmid"
          and by_rid["pubmed-00002"]["duplicate_match_type"] == "exact_pmid")
    check("L2 保留者标 keep（有摘要者优先）",
          by_rid["scopus-00001"]["ai_decision"] == "keep"
          and by_rid["scopus-00001"]["kept_record_id"] == "scopus-00001")
    check("L2 被重复者标 duplicate",
          by_rid["pubmed-00002"]["ai_decision"] == "duplicate")

    print("\n[2] 模糊级（L3）：只标疑似，绝不自动合并")
    check("L3 同题同人同年 → suspected_duplicate",
          by_rid["embase-00001"]["ai_decision"] == "suspected_duplicate"
          and by_rid["embase-00001"]["duplicate_match_type"] == "fuzzy_title")
    check("模糊簇编号 DUP-0001", by_rid["embase-00001"]["duplicate_group_id"] == "DUP-0001")
    check("★ 精确组保留者若又入模糊簇 → 升级为 suspected_duplicate（更保守）",
          by_rid["pubmed-00001"]["ai_decision"] == "suspected_duplicate",
          by_rid["pubmed-00001"]["ai_decision"])
    check("模糊簇不改变精确组另一成员的 duplicate 判定",
          by_rid["wos-00001"]["ai_decision"] == "duplicate")
    check("同题但作者不同 → 不入模糊簇（author_match_required=true）",
          by_rid["scopus-00001"]["ai_decision"] == "keep"
          and by_rid["scopus-00001"]["duplicate_group_id"] == "")
    check("唯一记录 ai_decision=unique 且无组号",
          by_rid["cnki-00001"]["ai_decision"] == "unique"
          and by_rid["cnki-00001"]["duplicate_group_id"] == "")
    check("human_decision 初始为空（只由人填写）",
          all(d["human_decision"] == "" for d in decisions))
    check("决策值全部在闭集内",
          {d["ai_decision"] for d in decisions} <= set(DEDUP_AI_DECISIONS))
    check("match_type 全部在闭集内",
          {d["duplicate_match_type"] for d in decisions} <= set(DEDUP_MATCH_TYPES))

    print("\n[3] dedup_unique 与 summary")
    check("unique 剔除精确重复（6 - 2 = 4 条）", len(unique_rows) == 4,
          f"{len(unique_rows)}")
    check("suspected 保留在 unique 中（不自动合并）",
          any(r["record_id"] == "embase-00001" for r in unique_rows))
    check("summary 总数 = 6", stats["total_records"] == 6)
    check("summary 精确去重 2 条", stats["records_flagged_duplicate"] == 2,
          str(stats))
    check("summary 疑似 = 2（模糊簇 2 成员）",
          stats["records_flagged_suspected"] == 2)
    check("summary 组计数：doi=1 pmid=1 fuzzy=1",
          stats["groups_exact_doi"] == 1 and stats["groups_exact_pmid"] == 1
          and stats["groups_fuzzy"] == 1)
    check("summary 输出唯一数 = 4", stats["records_output_unique"] == 4)

    print("\n[4] 确定性（同输入两次一致）")
    d2, u2, s2 = build_dedup([dict(r) for r in FIXTURE_ROWS], spec)
    check("decisions 确定性", d2 == decisions)
    check("summary 确定性", s2 == stats)
    check("unique 顺序确定性", [r["record_id"] for r in u2]
          == [r["record_id"] for r in unique_rows])

    print("\n[5] 阈值行为（author_match_required=false 时跨作者聚簇）")
    spec2 = _load_spec()
    spec2 = dict(spec2)
    spec2["dedup"] = dict(spec2["dedup"], author_match_required=False)
    d3, _, _ = build_dedup([dict(r) for r in FIXTURE_ROWS], spec2)
    by3 = {x["record_id"]: x for x in d3}
    check("关闭作者约束后，同题异作者并入同一模糊簇",
          by3["scopus-00001"]["duplicate_match_type"] == "fuzzy_title"
          and by3["scopus-00001"]["duplicate_group_id"] == "DUP-0001",
          by3["scopus-00001"]["duplicate_group_id"])
    check("跨作者簇仍标 suspected_duplicate（不自动合并）",
          by3["scopus-00001"]["ai_decision"] == "suspected_duplicate")
    check("精确判定不受关闭作者约束影响",
          by3["pubmed-00002"]["ai_decision"] == "duplicate")

    print("\n[6] 保留策略确定性")
    check("模糊簇建议保留有摘要者（pubmed-00001）",
          by_rid["embase-00001"]["kept_record_id"] == "pubmed-00001",
          by_rid["embase-00001"]["kept_record_id"])

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


def _load_spec() -> dict:
    from screening_schema import load_fixture_spec
    return load_fixture_spec()


# ===========================================================================
# CLI
# ===========================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="dedup_records.py",
        description="三级去重（S2-A）→ dedup_decisions / dedup_unique / dedup_summary")
    ap.add_argument("--input", type=Path, help="normalized_records.csv")
    ap.add_argument("--config", type=Path, help="project.yaml（读 screening.dedup）")
    ap.add_argument("--output-dir", type=Path, help="输出目录")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    missing = [a for a, v in (("--input", args.input), ("--config", args.config),
                              ("--output-dir", args.output_dir)) if not v]
    if missing:
        print(f"错误：缺少必需参数 {' '.join(missing)}（--self-test 可单独运行）",
              file=sys.stderr)
        return 2
    if not args.input.exists():
        print(f"错误：输入文件不存在：{args.input}", file=sys.stderr)
        return 2

    try:
        rows = read_records_csv(args.input)
        _, spec = load_screening_config(args.config)
    except (SchemaError, ConfigError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    rid_seen: set[str] = set()
    for r in rows:
        rid = normalize_text(r.get("record_id", ""))
        if not rid:
            print("错误：输入含 record_id 为空的行。", file=sys.stderr)
            return 2
        if rid in rid_seen:
            print(f"错误：record_id 重复：{rid}。", file=sys.stderr)
            return 2
        rid_seen.add(rid)

    decisions, unique_rows, stats = build_dedup(rows, spec)
    out = args.output_dir
    write_csv_dicts(out / "dedup_decisions.csv", DEDUP_DECISION_FIELDS, decisions)
    from record_schema import write_records_csv
    write_records_csv(out / "dedup_unique.csv", unique_rows)
    write_csv_dicts(out / "dedup_summary.csv", LOG_FIELDS, summary_rows(stats))

    print(f"去重完成：{stats['total_records']} 条 → "
          f"精确重复 {stats['records_flagged_duplicate']} / "
          f"疑似 {stats['records_flagged_suspected']} / "
          f"唯一 {stats['records_output_unique']}")
    print(f"  dedup_decisions.csv / dedup_unique.csv / dedup_summary.csv → {out}")
    if stats["records_pending_human"]:
        print(f"  ⚠️ {stats['records_pending_human']} 条疑似重复待人工裁决"
              "（AI 不自动合并；human_decision 列由人填写）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
