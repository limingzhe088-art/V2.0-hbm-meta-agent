#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_inclusion_list.py — 生成最终纳入/排除清单与筛选日志（S2 收口）

输入（★ GATE-S2c 检查点：本脚本假定人工已完成全文终裁）
  1. fulltext_screening.csv —— human_final_decision 必须全部非空，
     且不得残留 uncertain（不确定必须由人裁决为 include/exclude 后再运行）；
     exclude 行的 human_final_reason_code 必须在 screening.exclusion_reasons
     闭集内。违反任一条 → 退出码 2，不产出清单。
  2. --records  dedup_unique.csv（题录元数据：title/authors/year/doi）
  3. --ta-screening  title_abstract_screening.csv（题摘阶段人工数字）

输出（--output-dir）
  included_studies.csv  study_id, record_id, title, first_author, year, doi,
                        country, population, matrix, analyte,
                        screening_status, human_gate_status
                        （country/population/matrix/analyte 留空 —— 由 S3 提取时
                          回填，本脚本不做任何猜测）
  excluded_studies.csv  同上 + exclusion_reason
  screening_log.csv     metric,value —— PRISMA 流程数字（generate_prisma.py 消费）

用法
  python generate_inclusion_list.py --input fulltext_screening.csv \
      --records dedup_unique.csv --ta-screening title_abstract_screening.csv \
      --config project.yaml --output-dir .
  python generate_inclusion_list.py --self-test

退出码
  0 成功   2 前置条件不满足
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
    from record_schema import read_records_csv  # noqa: E402
    from screening_schema import (  # noqa: E402  ★ S2 单一来源
        EXCLUDED_FIELDS, FT_FIELDS, HUMAN_FT_DECISIONS, HUMAN_TA_DECISIONS,
        INCLUDED_FIELDS, LOG_FIELDS, TA_FIELDS, ConfigError,
        first_author, load_screening_config, normalize_text, read_csv_dicts,
        write_csv_dicts,
    )
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 screening_schema.py（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


def build_lists(ft_rows: list[dict], records: list[dict], ta_rows: list[dict],
                spec: dict) -> tuple[list[dict], list[dict], list[dict], dict]:
    """→ (included 行, excluded 行, screening_log 行, 统计)。违反 GATE-S2c → ConfigError。"""
    pending = [r.get("record_id", "") for r in ft_rows
               if not normalize_text(r.get("human_final_decision", ""))]
    if pending:
        raise ConfigError(
            f"GATE-S2c 检查点未过：{len(pending)} 条记录 human_final_decision 为空"
            f"（如 {pending[:3]}…）。请先完成人工终裁。")
    unresolved = [r.get("record_id", "") for r in ft_rows
                  if normalize_text(r.get("human_final_decision", "")) == "uncertain"]
    if unresolved:
        raise ConfigError(
            f"GATE-S2c 检查点未过：{len(unresolved)} 条记录仍为 uncertain"
            f"（如 {unresolved[:3]}…）。终裁只能是 include / exclude。")
    bad_reason = [
        (r.get("record_id", ""), r.get("human_final_reason_code", ""))
        for r in ft_rows
        if normalize_text(r.get("human_final_decision", "")) == "exclude"
        and normalize_text(r.get("human_final_reason_code", ""))
        not in spec["exclusion_reasons"]]
    if bad_reason:
        raise ConfigError(
            f"GATE-S2c 检查点未过：{len(bad_reason)} 条 exclude 记录的"
            f" human_final_reason_code 为空或不在闭集内（如 {bad_reason[:2]}…）；"
            f"允许编码：{', '.join(spec['exclusion_reasons'])}。")

    rec_by_id = {r.get("record_id", ""): r for r in records}
    included: list[dict] = []
    excluded: list[dict] = []
    for seq, r in enumerate(sorted(ft_rows,
                                   key=lambda x: x.get("record_id", "")), start=1):
        rid = r.get("record_id", "")
        meta = rec_by_id.get(rid, {})
        base = {
            "study_id": f"ST-{seq:04d}",
            "record_id": rid,
            "title": meta.get("title", ""),
            "first_author": first_author(meta.get("authors", "")),
            "year": meta.get("year", ""),
            "doi": meta.get("doi", ""),
            "country": "",       # 由 S3 提取回填
            "population": "",    # 由 S3 提取回填
            "matrix": "",        # 由 S3 提取回填
            "analyte": "",       # 由 S3 提取回填
            "screening_status": "",
            "human_gate_status": "human_confirmed",
            "meta_disposition": normalize_text(r.get("meta_disposition", "")),
        }
        if base["meta_disposition"]:
            if not spec["meta_analysis_dispositions"]:
                raise ConfigError(
                    f"`{rid}` 的 meta_disposition 非空，但 project.yaml 未配置 "
                    "screening.meta_analysis_disposition 闭集 —— 无法校验，"
                    "拒绝执行（不做默认值兜底）。")
            if base["meta_disposition"] not in spec["meta_analysis_dispositions"]:
                raise ConfigError(
                    f"GATE-S2c 检查点未过：`{rid}` 的 meta_disposition="
                    f"`{base['meta_disposition']}` 不在 "
                    f"screening.meta_analysis_disposition 闭集内；"
                    f"允许取值：{', '.join(spec['meta_analysis_dispositions'])}。")
        decision = normalize_text(r.get("human_final_decision", ""))
        if decision == "include":
            base["screening_status"] = "included_fulltext"
            included.append(base)
        else:
            base["screening_status"] = "excluded_fulltext"
            base["exclusion_reason"] = normalize_text(
                r.get("human_final_reason_code", ""))
            excluded.append(base)

    # ── screening_log（PRISMA 中间数据）───────────────────────────────
    ta_human = {"proceed_to_fulltext": 0, "exclude": 0, "uncertain": 0}
    ta_pending = 0
    for r in ta_rows:
        hd = normalize_text(r.get("human_decision", ""))
        if hd in ta_human:
            ta_human[hd] += 1
        else:
            ta_pending += 1
    reason_counts: dict[str, int] = {}
    for r in excluded:
        reason_counts[r["exclusion_reason"]] = \
            reason_counts.get(r["exclusion_reason"], 0) + 1
    unavailable = sum(1 for r in ft_rows
                      if normalize_text(r.get("fulltext_available", "")) == "FALSE")

    metrics = [
        ("records_identified_total", str(len(records))),
        ("records_after_dedup", str(len(ta_rows))),
        ("duplicates_removed", str(len(records) - len(ta_rows))),
        ("ta_screened", str(len(ta_rows))),
        ("ta_excluded", str(ta_human["exclude"])),
        ("ta_uncertain_unresolved", str(ta_human["uncertain"] + ta_pending)),
        ("ta_proceeded_fulltext", str(ta_human["proceed_to_fulltext"])),
        ("fulltext_assessed", str(len(ft_rows))),
        ("fulltext_unavailable", str(unavailable)),
        ("fulltext_retrieved", str(len(ft_rows) - unavailable)),
        ("fulltext_excluded", str(len(excluded))),
        ("fulltext_included", str(len(included))),
    ]
    for reason in spec["exclusion_reasons"]:
        metrics.append((f"excluded_fulltext_reason_{reason}",
                        str(reason_counts.get(reason, 0))))
    log_rows = [{"metric": k, "value": v} for k, v in metrics]
    stats = {"included": len(included), "excluded": len(excluded)}
    return included, excluded, log_rows, stats


def run_self_test() -> int:
    print("=" * 70)
    print("generate_inclusion_list.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    def expect_error(name, fn, needle=""):
        try:
            fn()
            check(name, False, "未抛出！")
        except ConfigError as e:
            check(name, True, (str(e)[:46] + "…") if needle in str(e) else str(e)[:46])

    from screening_schema import load_fixture_spec
    spec = load_fixture_spec()

    recs = [
        {"record_id": "pubmed-00001", "source_database": "PubMed",
         "title": "Urinary arsenic in general population",
         "abstract": "x", "authors": "Zhang, San; Li, Si", "year": "2020",
         "doi": "10.1/a", "pmid": "1", "journal": "J", "keywords": "",
         "raw_row_hash": ""},
        {"record_id": "wos-00001", "source_database": "WebOfScience",
         "title": "Arsenic review", "abstract": "", "authors": "Li, Si",
         "year": "2021", "doi": "10.1/b", "pmid": "", "journal": "J",
         "keywords": "", "raw_row_hash": ""},
        {"record_id": "scopus-00001", "source_database": "Scopus",
         "title": "Animal arsenic study", "abstract": "", "authors": "Wang, Wu",
         "year": "2019", "doi": "", "pmid": "", "journal": "J",
         "keywords": "", "raw_row_hash": ""},
    ]
    ta = [
        {"record_id": "pubmed-00001", "human_decision": "proceed_to_fulltext"},
        {"record_id": "wos-00001", "human_decision": "exclude"},
        {"record_id": "scopus-00001", "human_decision": "exclude"},
    ]
    ft = [
        {"record_id": "pubmed-00001", "fulltext_available": "TRUE",
         "human_final_decision": "include", "human_final_reason_code": ""},
        {"record_id": "wos-00001", "fulltext_available": "TRUE",
         "human_final_decision": "exclude", "human_final_reason_code": "review_or_comment"},
        {"record_id": "scopus-00001", "fulltext_available": "TRUE",
         "human_final_decision": "exclude", "human_final_reason_code": "not_human_study"},
    ]

    print("\n[1] 清单生成")
    included, excluded, log_rows, stats = build_lists(
        [dict(r) for r in ft], [dict(r) for r in recs], ta, spec)
    check("纳入 1 条", len(included) == 1 and stats["included"] == 1)
    check("排除 2 条", len(excluded) == 2)
    check("study_id 按 record_id 排序分配 ST-0001",
          included[0]["study_id"] == "ST-0001"
          and included[0]["record_id"] == "pubmed-00001")
    check("元数据回填（title/first_author/year/doi）",
          included[0]["title"] == "Urinary arsenic in general population"
          and included[0]["first_author"] == "Zhang, San"
          and included[0]["year"] == "2020")
    check("S3 回填列留空（country/population/matrix/analyte 不猜）",
          all(included[0][c] == "" for c in ("country", "population", "matrix",
                                             "analyte")))
    check("human_gate_status = human_confirmed",
          all(r["human_gate_status"] == "human_confirmed"
              for r in included + excluded))
    check("排除行带闭集内原因",
          {r["exclusion_reason"] for r in excluded}
          <= set(spec["exclusion_reasons"]))

    print("\n[2] screening_log 数字")
    log = {r["metric"]: r["value"] for r in log_rows}
    check("识别总数 = 3", log["records_identified_total"] == "3")
    check("去重后 = 3 / 去重移除 = 0", log["records_after_dedup"] == "3"
          and log["duplicates_removed"] == "0")
    check("题摘排除 = 2", log["ta_excluded"] == "2")
    check("进全文 = 1", log["ta_proceeded_fulltext"] == "1")
    check("全文纳入 = 1 / 排除 = 2", log["fulltext_included"] == "1"
          and log["fulltext_excluded"] == "2")
    check("排除原因分项计数", log["excluded_fulltext_reason_review_or_comment"] == "1"
          and log["excluded_fulltext_reason_not_human_study"] == "1"
          and log["excluded_fulltext_reason_wrong_year"] == "0")

    print("\n[3] GATE-S2c 检查点（显式拒绝）")
    ft_pending = [dict(r) for r in ft]
    ft_pending[0]["human_final_decision"] = ""
    expect_error("human_final_decision 有空值 → 拒绝",
                 lambda: build_lists(ft_pending, recs, ta, spec), "GATE-S2c")
    ft_unc = [dict(r) for r in ft]
    ft_unc[0]["human_final_decision"] = "uncertain"
    expect_error("uncertain 残留 → 拒绝",
                 lambda: build_lists(ft_unc, recs, ta, spec), "GATE-S2c")
    ft_bad = [dict(r) for r in ft]
    ft_bad[1]["human_final_reason_code"] = "i_dont_like_it"
    expect_error("排除原因越界 → 拒绝",
                 lambda: build_lists(ft_bad, recs, ta, spec), "GATE-S2c")

    print("\n[3b] meta_disposition 传递与闭集校验")
    ft_disp = [dict(r) for r in ft]
    ft_disp[0]["meta_disposition"] = "include"
    ft_disp[1]["meta_disposition"] = "background_only"
    inc2, exc2, _log2, _s2 = build_lists(ft_disp, recs, ta, spec)
    check("meta_disposition 透传到 included/excluded 清单",
          inc2[0]["meta_disposition"] == "include"
          and any(r["meta_disposition"] == "background_only" for r in exc2))
    ft_bad_disp = [dict(r) for r in ft]
    ft_bad_disp[0]["meta_disposition"] = "maybe"
    expect_error("meta_disposition 越界 → 拒绝",
                 lambda: build_lists(ft_bad_disp, recs, ta, spec), "meta_disposition")
    spec_nodisp = dict(spec)
    spec_nodisp["meta_analysis_dispositions"] = []
    ft_disp2 = [dict(r) for r in ft]
    ft_disp2[0]["meta_disposition"] = "include"
    expect_error("配置未声明闭集却填了 meta_disposition → 拒绝",
                 lambda: build_lists(ft_disp2, recs, ta, spec_nodisp),
                 "无法校验")

    print("\n[4] 结构")
    check("included 字段 = INCLUDED_FIELDS", list(included[0].keys()) == INCLUDED_FIELDS)
    check("excluded 字段 = EXCLUDED_FIELDS", list(excluded[0].keys()) == EXCLUDED_FIELDS)
    check("log 字段 = LOG_FIELDS", list(log_rows[0].keys()) == LOG_FIELDS)
    check("决策闭集（HUMAN_FT）引用一致", HUMAN_FT_DECISIONS[-1] == "uncertain")

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="generate_inclusion_list.py",
        description="生成最终纳入/排除清单与筛选日志（S2 收口，GATE-S2c 后运行）")
    ap.add_argument("--input", type=Path, help="fulltext_screening.csv")
    ap.add_argument("--records", type=Path, help="dedup_unique.csv（题录元数据）")
    ap.add_argument("--ta-screening", type=Path,
                    help="title_abstract_screening.csv（题摘人工数字）")
    ap.add_argument("--config", type=Path, help="project.yaml")
    ap.add_argument("--output-dir", type=Path, help="输出目录")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    missing = [a for a, v in (("--input", args.input), ("--records", args.records),
                              ("--ta-screening", args.ta_screening),
                              ("--config", args.config),
                              ("--output-dir", args.output_dir)) if not v]
    if missing:
        print(f"错误：缺少必需参数 {' '.join(missing)}（--self-test 可单独运行）",
              file=sys.stderr)
        return 2
    for p in (args.input, args.records, args.ta_screening):
        if not p.exists():
            print(f"错误：输入文件不存在：{p}", file=sys.stderr)
            return 2

    ft_rows = read_csv_dicts(args.input)
    if [k for k in list(ft_rows[0].keys())[:1]] != ["record_id"] or \
            set(FT_FIELDS) - set(ft_rows[0].keys()):
        print(f"错误：{args.input} 不是 fulltext_screening.py 的输出。", file=sys.stderr)
        return 2
    records = read_records_csv(args.records)
    ta_rows = read_csv_dicts(args.ta_screening)
    if ta_rows and set(TA_FIELDS) - set(ta_rows[0].keys()):
        print(f"错误：{args.ta_screening} 不是 classify_articles.py 的输出。",
              file=sys.stderr)
        return 2

    try:
        _, spec = load_screening_config(args.config)
        included, excluded, log_rows, stats = build_lists(
            ft_rows, records, ta_rows, spec)
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    write_csv_dicts(args.output_dir / "included_studies.csv", INCLUDED_FIELDS, included)
    write_csv_dicts(args.output_dir / "excluded_studies.csv", EXCLUDED_FIELDS, excluded)
    write_csv_dicts(args.output_dir / "screening_log.csv", LOG_FIELDS, log_rows)
    print(f"纳入 {stats['included']} 篇 → included_studies.csv")
    print(f"排除 {stats['excluded']} 篇 → excluded_studies.csv（记录保留，不物理删除）")
    print("筛选日志 → screening_log.csv")
    print("提醒：请人工核对清单后在 _state/gates.json 确认 GATE-S2c（AI 无权开启闸门）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
