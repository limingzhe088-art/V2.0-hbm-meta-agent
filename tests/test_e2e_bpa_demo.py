#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_e2e_bpa_demo.py — bpa-china-demo 合成案例端到端测试（S1→S2→S3 交接）

★★ 输入数据全部为虚构（examples/bpa-china-demo/），仅用于验证链路。★★

流程（全部产物写入临时目录，不污染仓库；脚本自身确定性，可重复）
  test_s1_parse      RIS ×3 → 统一 CSV（3 库 20 条）
  test_s1_merge      合并多来源 CSV → all_raw（20 条，标题集合与 raw_records_example 一致）
  test_s2_normalize  raw_records_example → normalized（哈希重算）
  test_s2_dedup      三级去重：R003 精确重复被剔除；R018 模糊疑似保留待人工
  （人工模拟）        应用 dedup_decisions：R018 duplicate → 人工确认唯一集 18 条；
                      填写 title_abstract_screening 的 human 列（GATE-S2b）
  test_s2_classify   规则引擎分类：8 hbm→proceed / 3 review+3 animal+2 in_vitro
                     →suggest_exclude / 2 non_hbm→uncertain
  test_s2_fulltext   8 条全文判定：6 include_candidate + 1 no_usable_values
                     + 1 duplicate_cohort（AI 建议 include，人工推翻）
  （人工模拟）        填写 human_final_decision / meta_disposition（GATE-S2c）
  test_s2_inclusion  included 6 / excluded 2 + screening_log
  test_s2_prisma     PRISMA 2020 流程图（四条恒等式校验）
  test_s1_to_s3_handoff  合成主表（6 行 × 72 列）通过 validate_table.py，
                          且 DOI 与 included_studies 一一对应

与入库的"期望文件"的关系
  examples/bpa-china-demo/02-screening/ 下的 CSV/MD 是本脚本某次运行的
  **确定性输出快照**（人工列已按 GATE-S2b/S2c 模拟填写）。CI 每次重跑
  链路并逐行比对快照——脚本或配置的任何行为变化都会使 CI 变红。
  `--update-expected` 可在有意变更后重新生成快照。

用法
  python tests/test_e2e_bpa_demo.py                    # 运行全部测试
  python tests/test_e2e_bpa_demo.py --update-expected  # 重生成期望快照
"""
from __future__ import annotations

import argparse
import csv
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "bpa-china-demo"
EXPECTED = DEMO / "02-screening"
SCRIPTS = {
    "parse_ris": ROOT / "skills/S1-search-strategy/scripts/parse_ris.py",
    "merge_sources": ROOT / "skills/S1-search-strategy/scripts/merge_sources.py",
    "normalize": ROOT / "skills/S2-screening-quality/scripts/normalize_records.py",
    "dedup": ROOT / "skills/S2-screening-quality/scripts/dedup_records.py",
    "classify": ROOT / "skills/S2-screening-quality/scripts/classify_articles.py",
    "fulltext": ROOT / "skills/S2-screening-quality/scripts/fulltext_screening.py",
    "inclusion": ROOT / "skills/S2-screening-quality/scripts/generate_inclusion_list.py",
    "prisma": ROOT / "skills/S2-screening-quality/scripts/generate_prisma.py",
    "validate_table": ROOT / "skills/S3-extraction-standardization/scripts/validate_table.py",
}
CONFIG = DEMO / "project.yaml"

CHECKS: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))


def run(script: Path, *args, expect=0) -> subprocess.CompletedProcess:
    r = subprocess.run([sys.executable, str(script), *[str(a) for a in args]],
                       capture_output=True, text=True, errors="replace")
    if expect is not None and r.returncode != expect:
        raise AssertionError(
            f"{script.name} 退出码 {r.returncode}（期望 {expect}）\n"
            f"stdout 尾部：{(r.stdout or '')[-400:]}\n"
            f"stderr 尾部：{(r.stderr or '')[-400:]}")
    return r


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), [dict(r) for r in reader]


# ===========================================================================
# 人工步骤模拟（GATE-S2b / GATE-S2c 的"人"在这台测试机上的确定性替身）
# ===========================================================================

def human_fill_ta(ta_path: Path, dedup_path: Path, unique_path: Path,
                  out_path: Path, out_unique: Path) -> None:
    """GATE-S2b 模拟：① 应用去重裁决（R018 duplicate → 剔除）② 填写 human 列。"""
    _, dedup = read_csv(dedup_path)
    human_dup = {d["record_id"] for d in dedup
                 if d["human_decision"] == "duplicate"}
    _, rows = read_csv(unique_path)
    kept = [r for r in rows if r["record_id"] not in human_dup]
    shutil.copy(out_unique.parent / "unused", out_unique) if False else None
    _write_unique(out_unique, rows, kept)

    _, ta = read_csv(ta_path)
    for r in ta:
        ai = r["ai_screening_decision"]
        if ai == "proceed_to_fulltext":
            r["human_decision"] = "proceed_to_fulltext"
        elif r["ai_article_type"] == "review":
            r["human_decision"] = "exclude"
            r["human_reason_code"] = "review_or_comment"
        elif r["ai_article_type"] in ("animal_study", "in_vitro_study"):
            r["human_decision"] = "exclude"
            r["human_reason_code"] = "not_human_study"
        else:  # human_non_hbm → 信息不足，维持 uncertain（不进入定量分析）
            r["human_decision"] = "uncertain"
    _write_rows(out_path, ta)


def _write_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _write_unique(out_unique: Path, rows, kept) -> None:
    fields = list(rows[0].keys()) if rows else []
    with io.open(out_unique, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(kept)


def human_fill_ft(ft_path: Path, out_path: Path) -> None:
    """GATE-S2c 模拟：终裁 + 定量处理意见。R009 与 R001 同队列 → 人工推翻 AI。"""
    _, rows = read_csv(ft_path)
    for r in rows:
        if r["ai_screening_decision"] == "include_candidate":
            if r["record_id"] == "R009":
                # 人工识别：与 R001 同一上海成人队列 → 排除（duplicate_cohort）
                r["human_final_decision"] = "exclude"
                r["human_final_reason_code"] = "duplicate_cohort"
                r["meta_disposition"] = "exclude"
            else:
                r["human_final_decision"] = "include"
                r["meta_disposition"] = "include"
        else:  # suggest_exclude（R017 no_usable_values）→ 人工确认排除
            r["human_final_decision"] = "exclude"
            r["human_final_reason_code"] = r["ai_reason_code"]
            r["meta_disposition"] = "exclude"
    _write_rows(out_path, rows)


# ===========================================================================
# 测试步骤（函数名即用例名；pytest 可收集，standalone 也能跑）
# ===========================================================================

def test_s1_parse(work: Path):
    """S1: 解析合成 RIS 文件，输出统一格式 CSV。"""
    total = 0
    for src, n in (("pubmed", 9), ("wos", 7), ("scopus", 4)):
        out = work / f"raw_{src}.csv"
        run(SCRIPTS["parse_ris"], "--input", DEMO / "01-search" / f"{src}_export.ris",
            "--output", out, "--source", src, "--validate")
        header, rows = read_csv(out)
        check(f"parse_ris {src}：{n} 条", len(rows) == n, f"{len(rows)}")
        check(f"parse_ris {src}：字段与 record_schema 一致",
              header == ["record_id", "source_database", "title", "abstract",
                         "authors", "year", "doi", "pmid", "journal", "keywords",
                         "raw_row_hash"])
        total += len(rows)
    check("parse_ris 三库合计 20 条", total == 20)


def test_s1_merge(work: Path):
    """S1: 合并多来源 CSV，标题集合与 raw_records_example.csv 一致。"""
    run(SCRIPTS["merge_sources"],
        "--inputs", work / "raw_pubmed.csv", work / "raw_wos.csv",
        work / "raw_scopus.csv",
        "--output", work / "all_raw_records.csv",
        "--summary", work / "source_summary_gen.csv")
    _, merged = read_csv(work / "all_raw_records.csv")
    check("合并后 20 条", len(merged) == 20, f"{len(merged)}")
    _, ref = read_csv(DEMO / "01-search" / "raw_records_example.csv")
    key = lambda r: (r["title"], r["doi"], r["year"])  # noqa: E731
    check("合并结果与 raw_records_example 的 (title,doi,year) 集合一致",
          sorted(map(key, merged)) == sorted(map(key, ref)))


def test_s2_normalize(work: Path):
    """S2: 规范化记录（清洗 + 哈希重算）。"""
    run(SCRIPTS["normalize"], "--input", DEMO / "01-search" / "raw_records_example.csv",
        "--output", work / "normalized_records.csv", "--validate")
    _, rows = read_csv(work / "normalized_records.csv")
    check("规范化后 20 条", len(rows) == 20)
    check("全部 raw_row_hash 为 64 位十六进制",
          all(len(r["raw_row_hash"]) == 64 for r in rows))
    check("record_id 保留 R001…R020（审计链不变）",
          {r["record_id"] for r in rows} == {f"R{i:03d}" for i in range(1, 21)})


def test_s2_dedup(work: Path):
    """S2: 三级去重（R003 精确；R018 模糊疑似 → 人工裁决）。"""
    run(SCRIPTS["dedup"], "--input", work / "normalized_records.csv",
        "--config", CONFIG, "--output-dir", work)
    _, dec = read_csv(work / "dedup_decisions.csv")
    by = {d["record_id"]: d for d in dec}
    check("R003 标记为 R001 的精确重复（DOI 匹配，Level 1）",
          by["R003"]["duplicate_match_type"] == "exact_doi"
          and by["R003"]["ai_decision"] == "duplicate"
          and by["R003"]["kept_record_id"] == "R001")
    check("R018 与 R001 同题同人同年 → 模糊疑似（不自动合并）",
          by["R018"]["duplicate_match_type"] == "fuzzy_title"
          and by["R018"]["ai_decision"] == "suspected_duplicate"
          and by["R018"]["duplicate_group_id"] == by["R001"]["duplicate_group_id"])
    _, unique = read_csv(work / "dedup_unique.csv")
    ids = {r["record_id"] for r in unique}
    check("精确重复 R003 已从唯一集剔除", "R003" not in ids)
    check("模糊疑似 R018 保留在唯一集中（AI 不自动合并）", "R018" in ids)
    check("AI 去重后唯一记录 19 条", len(unique) == 19, f"{len(unique)}")
    # 人工模拟：确认 R018 为重复并应用裁决（正式流程由人在 GATE-S2b 完成）
    for d in dec:
        if d["ai_decision"] == "duplicate":
            d["human_decision"] = "duplicate"
        elif d["ai_decision"] == "suspected_duplicate":
            d["human_decision"] = "keep" if d["record_id"] == d["kept_record_id"] \
                else "duplicate"
    _write_rows(work / "dedup_decisions.csv", dec)
    human_dup = {d["record_id"] for d in dec if d["human_decision"] == "duplicate"}
    confirmed = [r for r in unique if r["record_id"] not in human_dup]
    _write_unique(work / "dedup_unique.csv", unique, confirmed)
    check("人工应用裁决后唯一集 18 条（剔除 R018）",
          len(confirmed) == 18 and "R018" not in {r["record_id"] for r in confirmed})


def test_s2_classify(work: Path):
    """S2: 题目摘要分类（规则引擎 + 人工模拟填写 human 列）。"""
    run(SCRIPTS["classify"], "--input", work / "dedup_unique.csv",
        "--config", CONFIG, "--output", work / "ta_raw.csv")
    _, rows = read_csv(work / "ta_raw.csv")
    types: dict[str, int] = {}
    for r in rows:
        types[r["ai_article_type"]] = types.get(r["ai_article_type"], 0) + 1
    check("8 条 human_hbm_original", types.get("human_hbm_original") == 8, str(types))
    check("3 条 review / 3 条 animal / 2 条 in_vitro",
          types.get("review") == 3 and types.get("animal_study") == 3
          and types.get("in_vitro_study") == 2)
    check("2 条 human_non_hbm", types.get("human_non_hbm") == 2)
    ai_dec: dict[str, int] = {}
    for r in rows:
        ai_dec[r["ai_screening_decision"]] = ai_dec.get(r["ai_screening_decision"], 0) + 1
    check("AI 建议：8 proceed / 8 suggest_exclude / 2 uncertain",
          ai_dec.get("proceed_to_fulltext") == 8
          and ai_dec.get("suggest_exclude") == 8
          and ai_dec.get("uncertain") == 2, str(ai_dec))
    check("review/animal/in_vitro 的 AI 建议均为 suggest_exclude",
          all(r["ai_screening_decision"] == "suggest_exclude" for r in rows
              if r["ai_article_type"] in ("review", "animal_study", "in_vitro_study")))
    check("hbm 类全部 proceed_to_fulltext",
          all(r["ai_screening_decision"] == "proceed_to_fulltext" for r in rows
              if r["ai_article_type"] == "human_hbm_original"))
    # 人工模拟（GATE-S2b）：逐条填写 human_decision
    for r in rows:
        if r["ai_screening_decision"] == "proceed_to_fulltext":
            r["human_decision"] = "proceed_to_fulltext"
        elif r["ai_article_type"] == "review":
            r["human_decision"] = "exclude"
            r["human_reason_code"] = "review_or_comment"
        elif r["ai_article_type"] in ("animal_study", "in_vitro_study"):
            r["human_decision"] = "exclude"
            r["human_reason_code"] = "not_human_study"
        else:
            r["human_decision"] = "uncertain"
    _write_rows(work / "title_abstract_screening.csv", rows)
    _, filled = read_csv(work / "title_abstract_screening.csv")
    check("human_decision 无空值（GATE-S2b 检查点）",
          all(r["human_decision"] for r in filled))


def test_s2_fulltext(work: Path):
    """S2: 全文筛选（8 条进全文：6 候选纳入 + 2 条 AI/人工分层判定）。"""
    run(SCRIPTS["fulltext"], "--input", work / "title_abstract_screening.csv",
        "--records", work / "normalized_records.csv",
        "--fulltext-dir", DEMO / "02-screening" / "fulltexts",
        "--config", CONFIG, "--output", work / "fulltext_screening.csv")
    _, rows = read_csv(work / "fulltext_screening.csv")
    check("8 条进入全文筛", len(rows) == 8, f"{len(rows)}")
    check("全部 fulltext_available=TRUE（8 份合成全文齐备）",
          all(r["fulltext_available"] == "TRUE" for r in rows))
    dec: dict[str, int] = {}
    for r in rows:
        dec[r["ai_screening_decision"]] = dec.get(r["ai_screening_decision"], 0) + 1
    check("AI：7 include_candidate + 1 suggest_exclude(no_usable_values)",
          dec.get("include_candidate") == 7 and dec.get("suggest_exclude") == 1,
          str(dec))
    r017 = next(r for r in rows if r["record_id"] == "R017")
    check("R017（仅检出率、无数值）→ no_usable_values",
          r017["ai_reason_code"] == "no_usable_values"
          and r017["has_usable_concentration"] == "FALSE")
    r009 = next(r for r in rows if r["record_id"] == "R009")
    check("R009（同队列重复，AI 不可见）→ AI 建议 include_candidate，交人工推翻",
          r009["ai_screening_decision"] == "include_candidate")
    check("meta_disposition 列存在且 AI 留空",
          all(r["meta_disposition"] == "" for r in rows))
    # 人工模拟（GATE-S2c）：终裁
    for r in rows:
        if r["ai_screening_decision"] == "include_candidate":
            if r["record_id"] == "R009":
                r["human_final_decision"] = "exclude"
                r["human_final_reason_code"] = "duplicate_cohort"
                r["meta_disposition"] = "exclude"
            else:
                r["human_final_decision"] = "include"
                r["meta_disposition"] = "include"
        else:
            r["human_final_decision"] = "exclude"
            r["human_final_reason_code"] = r["ai_reason_code"]
            r["meta_disposition"] = "exclude"
    _write_rows(work / "fulltext_screening.csv", rows)


def test_s2_inclusion(work: Path):
    """S2: 生成纳入/排除清单与筛选日志。"""
    run(SCRIPTS["inclusion"], "--input", work / "fulltext_screening.csv",
        "--records", work / "normalized_records.csv",
        "--ta-screening", work / "title_abstract_screening.csv",
        "--config", CONFIG, "--output-dir", work)
    _, inc = read_csv(work / "included_studies.csv")
    check("included_studies.csv 有 6 条", len(inc) == 6, f"{len(inc)}")
    check("study_id 按 record_id 排序 ST-0001…ST-0006",
          [r["study_id"] for r in inc] == [f"ST-{i:04d}" for i in range(1, 7)])
    check("meta_disposition 全部 include",
          all(r["meta_disposition"] == "include" for r in inc))
    _, exc = read_csv(work / "excluded_studies.csv")
    reasons = sorted(r["exclusion_reason"] for r in exc)
    check("excluded_studies.csv 排除原因正确（duplicate_cohort + no_usable_values）",
          reasons == ["duplicate_cohort", "no_usable_values"], str(reasons))
    _, log = read_csv(work / "screening_log.csv")
    logd = {r["metric"]: r["value"] for r in log}
    expected = {"records_identified_total": "20", "records_after_dedup": "18",
                "duplicates_removed": "2", "ta_screened": "18",
                "ta_excluded": "8", "ta_uncertain_unresolved": "2",
                "ta_proceeded_fulltext": "8", "fulltext_assessed": "8",
                "fulltext_unavailable": "0", "fulltext_retrieved": "8",
                "fulltext_excluded": "2", "fulltext_included": "6"}
    check("screening_log 各阶段数字符合设计", all(
        logd.get(k) == v for k, v in expected.items()),
        str({k: logd.get(k) for k in expected}))
    check("PRISMA 恒等式：identified = after_dedup + duplicates_removed",
          int(logd["records_identified_total"])
          == int(logd["records_after_dedup"]) + int(logd["duplicates_removed"]))


def test_s2_prisma(work: Path):
    """S2: 生成 PRISMA 流程（含恒等式校验）。"""
    run(SCRIPTS["prisma"], "--input", work / "screening_log.csv",
        "--config", CONFIG, "--output", work / "prisma_flow.md")
    md = (work / "prisma_flow.md").read_text(encoding="utf-8")
    check("prisma_flow.md 存在且含 Mermaid 代码块", "```mermaid" in md)
    check("含最终纳入数字 n = 6", "n = 6" in md)
    check("含数字核对表", "## 数字核对表" in md)


def test_s1_to_s3_handoff(work: Path):
    """链路: S2 输出能被 S3 读取（合成主表通过 validate_table 且与纳入清单对账）。"""
    _, inc = read_csv(work / "included_studies.csv")
    inc_dois = {r["doi"] for r in inc}
    # 合成主表（72 列契约）应通过 S3 校验
    r = run(SCRIPTS["validate_table"],
            "--input", DEMO / "03-extraction" / "master_table_EXAMPLE.csv",
            "--config", CONFIG, "--out", work / "validate_report.md")
    check("validate_table 退出码 0 且结论 = PASS（ERROR=0）",
          r.returncode == 0 and "结论=PASS" in (r.stdout or "").replace(" ", ""),
          (r.stdout or "").strip().splitlines()[-1][:60] if r.stdout else "")
    _, master = read_csv(DEMO / "03-extraction" / "master_table_EXAMPLE.csv")
    master_dois = {r["doi"] for r in master}
    check("主表 6 行与 included_studies 按 DOI 一一对应",
          len(master) == 6 and master_dois == inc_dois,
          str(sorted(master_dois ^ inc_dois)))
    check("主表 analyte 全部为 BPA 且 matrix 为 Urine",
          all(r["analyte"] == "BPA" and r["sample_type"] == "Urine" for r in master))


# ===========================================================================
# 期望快照对比
# ===========================================================================

SNAPSHOTS = ["dedup_decisions.csv", "dedup_unique.csv", "dedup_summary.csv",
             "title_abstract_screening.csv", "fulltext_screening.csv",
             "included_studies.csv", "excluded_studies.csv", "screening_log.csv"]


def compare_snapshots(work: Path):
    """生成的确定性输出逐行比对入库快照（快照 = 本脚本上次确认的输出）。"""
    for name in SNAPSHOTS:
        exp = EXPECTED / name
        if not exp.exists():
            check(f"快照 {name} 存在", False, "缺失（--update-expected 生成）")
            continue
        _, gen_rows = read_csv(work / name)
        _, exp_rows = read_csv(exp)
        check(f"快照比对一致：{name}", gen_rows == exp_rows,
              f"生成 {len(gen_rows)} / 快照 {len(exp_rows)}")
    exp_md = EXPECTED / "prisma_flow.md"
    if not exp_md.exists():
        check("快照 prisma_flow.md 存在", False, "缺失（--update-expected 生成）")
        return
    gen_md = (work / "prisma_flow.md").read_text(encoding="utf-8")
    check("快照比对一致：prisma_flow.md",
          gen_md == exp_md.read_text(encoding="utf-8"))


def update_expected(work: Path):
    EXPECTED.mkdir(parents=True, exist_ok=True)
    for name in SNAPSHOTS:
        shutil.copy(work / name, EXPECTED / name)
    shutil.copy(work / "prisma_flow.md", EXPECTED / "prisma_flow.md")
    print(f"期望快照已更新 → {EXPECTED}")


def run_all(work: Path, do_update_expected: bool = False) -> int:
    steps = [test_s1_parse, test_s1_merge, test_s2_normalize, test_s2_dedup,
             test_s2_classify, test_s2_fulltext, test_s2_inclusion,
             test_s2_prisma, test_s1_to_s3_handoff]
    for step in steps:
        print(f"\n[{step.__name__}] {step.__doc__.strip().splitlines()[0]}")
        try:
            step(work)
        except AssertionError as exc:
            check(f"{step.__name__} 执行成功", False, str(exc)[:200])
    print("\n[期望快照比对]")
    if do_update_expected:
        update_expected(work)
    compare_snapshots(work)
    print("\n" + "-" * 70)
    failed = [c for c in CHECKS if not c[1]]
    for name, _ok, detail in failed:
        print(f"  ❌ {name}  {detail}")
    if failed:
        print(f"E2E 失败：{len(failed)}/{len(CHECKS)} 项未通过")
        return 1
    print(f"E2E 通过：{len(CHECKS)}/{len(CHECKS)} 项全部通过")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="test_e2e_bpa_demo.py",
        description="bpa-china-demo 合成案例端到端测试（S1→S2→S3 交接）")
    ap.add_argument("--update-expected", action="store_true",
                    help="运行链路并把输出写回 examples/bpa-china-demo/02-screening/")
    args = ap.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="hbm_e2e_"))
    rc = run_all(work, do_update_expected=args.update_expected)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
