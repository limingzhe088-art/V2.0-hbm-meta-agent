#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_demo_master_table.py — 生成 bpa-china-demo 的合成主表（03-extraction/）

★★ 全部记录为虚构（DEMO-* 占位标题、编造的 DOI/数值），仅用于 S2→S3 交接
   与 validate_table.py 的端到端验证。★★

对应关系：6 行主表 ↔ S2 included_studies.csv 的 6 项纳入研究（按 DOI 链接）。
所有行都是"逐条人工校对完成"形态：extracted_by=AI+Human、verified=TRUE。

用法
  python make_demo_master_table.py --write
  python make_demo_master_table.py --self-test
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

HERE = Path(__file__).resolve().parent
OUT = HERE / "03-extraction" / "master_table_EXAMPLE.csv"

# ── 契约 72 列（顺序即 shared/data-contract.md §3）────────────────────
FIELDS = """record_id study_no cohort_id dedup_group title author doi t_publication source_database
time time_start time_end country province city county region latitude longitude
population population_group recruit_crowd population_desc classification_rule age age_mean height weight bmi male_n female_n gender
flag_occupational flag_disease flag_endemic_area flag_mixed_occupational inclusion_decision exclusion_reason
sample_type blood_matrix flag_blood_matrix analyte analyte_species detection_method qc_reported
stat_type initial_gm initial_mean initial_sd initial_median initial_p25 initial_p75 initial_p05 initial_p95 initial_min initial_max initial_gsd unit_original sample_size
adjusted unit_final gm_summary gsd_summary conversion_path conversion_params inferred_flags notes
extracted_by verified verified_by snapshot_tag row_hash""".split()

assert len(FIELDS) == 72, f"契约应为 72 列，实际 {len(FIELDS)}"


def row(**kw) -> list[str]:
    unknown = set(kw) - set(FIELDS)
    assert not unknown, f"未知字段：{unknown}"
    return [str(kw.get(f, "")) for f in FIELDS]


# ── 6 条合成记录（↔ included_studies.csv 的 6 项纳入研究，按 DOI 链接）──
ROWS: list[dict] = [
    # ST-0001 ← R001：直接报告 GM/GSD（零推断：reported_GM）
    dict(record_id="R00001", study_no="S001", title="DEMO-Study-001 urinary bisphenol A in adults, Shanghai",
         author="DEMO-Author-A", doi="10.1234/demo.001", t_publication="2019",
         source_database="PubMed", time="2019", country="China", province="上海市",
         city="示例市", region="East", population="一般成年人", population_group="Adults",
         recruit_crowd="社区", population_desc="多阶段分层抽样",
         classification_rule="18 岁及以上常住居民", age="18-75",
         male_n="600", female_n="600", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="BPA", detection_method="LC-MS/MS",
         qc_reported="TRUE", stat_type="GM_GSD", initial_gm="2.31", initial_gsd="2.05",
         unit_original="ug/L", sample_size="1200", adjusted="No",
         unit_final="ug/L", gm_summary="2.31", gsd_summary="2.05",
         conversion_path="reported_GM",
         notes="合成演示记录：原文直接报告 GM 与 GSD（零推断子集）",
         extracted_by="AI+Human", verified="TRUE", verified_by="DEMO-Verifier",
         snapshot_tag="BPA_DEMO", row_hash="DEMO0001"),
    # ST-0002 ← R002：中位数+IQR → as_gm 策略
    dict(record_id="R00002", study_no="S002", title="DEMO-Study-002 urinary bisphenol A in pregnant women, Hangzhou",
         author="DEMO-Author-B", doi="10.1234/demo.002", t_publication="2020",
         source_database="PubMed", time="2018", country="China", province="浙江省",
         city="示例市", region="East", population="孕妇", population_group="Pregnant",
         recruit_crowd="医院体检", population_desc="产检孕妇",
         classification_rule="单胎妊娠", age="24-38",
         female_n="800", gender="Female",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="BPA", detection_method="LC-MS/MS",
         qc_reported="TRUE", stat_type="Median_IQR", initial_median="1.90",
         initial_p25="1.20", initial_p75="2.80",
         unit_original="ug/L", sample_size="800", adjusted="No",
         unit_final="ug/L", gm_summary="1.90",
         conversion_path="Median_IQR→Median_as_GM→GM",
         conversion_params="strategy=as_gm;configured=standardization.median_to_gm_strategy",
         inferred_flags="gm_from_median",
         notes="合成演示记录：对数正态假设下中位数作为 GM",
         extracted_by="AI+Human", verified="TRUE", verified_by="DEMO-Verifier",
         snapshot_tag="BPA_DEMO", row_hash="DEMO0002"),
    # ST-0003 ← R004：儿童，直接报告 GM/GSD
    dict(record_id="R00003", study_no="S003", title="DEMO-Study-003 urinary bisphenol A in children, Guangdong",
         author="DEMO-Author-C", doi="10.1234/demo.004", t_publication="2021",
         source_database="PubMed", time="2021", country="China", province="广东省",
         city="示例市", region="South", population="学龄儿童", population_group="Minors",
         recruit_crowd="学校", population_desc="整班抽样的在校学生",
         classification_rule="6-12 岁在校学生", age="6-12",
         male_n="330", female_n="310", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="BPA", detection_method="LC-MS/MS",
         qc_reported="TRUE", stat_type="GM_GSD", initial_gm="3.62", initial_gsd="1.98",
         unit_original="ug/L", sample_size="640", adjusted="No",
         unit_final="ug/L", gm_summary="3.62", gsd_summary="1.98",
         conversion_path="reported_GM",
         notes="合成演示记录：原文直接报告 GM 与 GSD",
         extracted_by="AI+Human", verified="TRUE", verified_by="DEMO-Verifier",
         snapshot_tag="BPA_DEMO", row_hash="DEMO0003"),
    # ST-0004 ← R005：AM+SD → Wan S1 对数正态转换
    dict(record_id="R00004", study_no="S004", title="DEMO-Study-004 urinary bisphenol A and diabetes, Beijing",
         author="DEMO-Author-D", doi="10.1234/demo.005", t_publication="2018",
         source_database="PubMed", time="2016", country="China", province="北京市",
         city="示例市", region="North", population="一般成年人", population_group="Adults",
         recruit_crowd="疾控监测", population_desc="随访队列基线",
         classification_rule="18 岁及以上常住居民", age="35-70",
         male_n="470", female_n="510", gender="Both",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="BPA", detection_method="LC-MS/MS",
         qc_reported="TRUE", stat_type="AM_SD", initial_mean="2.15", initial_sd="1.31",
         unit_original="ug/L", sample_size="980", adjusted="No",
         unit_final="ug/L", gm_summary="1.84", gsd_summary="1.75",
         conversion_path="AM_SD→AMSD_to_LN→GM",
         conversion_params="AM=2.15;SD=1.31;CV=0.609",
         inferred_flags="gm_from_mean_sd",
         notes="合成演示记录：原文只给 AM+SD，经 Wan 2014 S1 转换",
         extracted_by="AI+Human", verified="TRUE", verified_by="DEMO-Verifier",
         snapshot_tag="BPA_DEMO", row_hash="DEMO0004"),
    # ST-0005 ← R006：孕妇，直接报告 GM
    dict(record_id="R00005", study_no="S005", title="DEMO-Study-005 urinary bisphenol A in pregnant women, Zhejiang",
         author="DEMO-Author-E", doi="10.1234/demo.006", t_publication="2022",
         source_database="PubMed", time="2022", country="China", province="浙江省",
         city="示例市", region="East", population="孕妇", population_group="Pregnant",
         recruit_crowd="医院体检", population_desc="产科门诊招募",
         classification_rule="单胎妊娠", age="22-36",
         female_n="500", gender="Female",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="BPA", detection_method="LC-MS/MS",
         qc_reported="TRUE", stat_type="GM_only", initial_gm="2.74",
         unit_original="ug/L", sample_size="500", adjusted="No",
         unit_final="ug/L", gm_summary="2.74",
         conversion_path="reported_GM",
         notes="合成演示记录：原文直接报告 GM（无 GSD）",
         extracted_by="AI+Human", verified="TRUE", verified_by="DEMO-Verifier",
         snapshot_tag="BPA_DEMO", row_hash="DEMO0005"),
    # ST-0006 ← R015：孕妇，直接报告 GM/GSD
    dict(record_id="R00006", study_no="S006", title="DEMO-Study-006 urinary bisphenol A in pregnant women, Beijing",
         author="DEMO-Author-F", doi="10.1234/demo.015", t_publication="2023",
         source_database="Scopus", time="2023", country="China", province="北京市",
         city="示例市", region="North", population="孕妇", population_group="Pregnant",
         recruit_crowd="医院体检", population_desc="分娩期截面调查",
         classification_rule="单胎妊娠", age="23-39",
         female_n="700", gender="Female",
         flag_occupational="FALSE", flag_disease="FALSE", flag_endemic_area="FALSE",
         flag_mixed_occupational="FALSE", inclusion_decision="Include",
         sample_type="Urine", analyte="BPA", detection_method="LC-MS/MS",
         qc_reported="TRUE", stat_type="GM_GSD", initial_gm="2.12", initial_gsd="2.10",
         unit_original="ug/L", sample_size="700", adjusted="No",
         unit_final="ug/L", gm_summary="2.12", gsd_summary="2.10",
         conversion_path="reported_GM",
         notes="合成演示记录：原文直接报告 GM 与 GSD",
         extracted_by="AI+Human", verified="TRUE", verified_by="DEMO-Verifier",
         snapshot_tag="BPA_DEMO", row_hash="DEMO0006"),
]


def rows_as_lists() -> list[list[str]]:
    return [row(**r) for r in ROWS]


def write_all() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        w.writerows(rows_as_lists())
    print(f"已生成 {OUT}（{len(ROWS)} 行 × {len(FIELDS)} 列）")
    return 0


def run_self_test() -> int:
    print("=" * 70)
    print("make_demo_master_table.py --self-test")
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    rows = rows_as_lists()
    check("6 行记录", len(rows) == 6)
    check("列数全部为 72", all(len(r) == 72 for r in rows))
    dois = {r[FIELDS.index("doi")] for r in rows}
    check("DOI 与 included_studies 的 6 项一致",
          dois == {"10.1234/demo.001", "10.1234/demo.002", "10.1234/demo.004",
                   "10.1234/demo.005", "10.1234/demo.006", "10.1234/demo.015"},
          str(sorted(dois)))
    check("全部 verified=TRUE 且 extracted_by=AI+Human",
          all(r[FIELDS.index("verified")] == "TRUE"
              and r[FIELDS.index("extracted_by")] == "AI+Human" for r in rows))
    check("全部 unit_final = ug/L（与 unit_policy 目标一致）",
          all(r[FIELDS.index("unit_final")] == "ug/L" for r in rows))
    check("全部 gm_summary > 0 且 sample_size > 0",
          all(float(r[FIELDS.index("gm_summary")]) > 0
              and int(r[FIELDS.index("sample_size")]) > 0 for r in rows))
    check("全部 Urine 行 adjusted 非空（契约条件必填）",
          all(r[FIELDS.index("adjusted")] for r in rows))
    check("时间窗口 2005-2024",
          all(2005 <= int(r[FIELDS.index("time")]) <= 2024 for r in rows))
    check("Wan 转换行带 gm_from_mean_sd 标记",
          any("gm_from_mean_sd" in r[FIELDS.index("inferred_flags")] for r in rows))
    check("标题为 DEMO 占位（脱敏）",
          all(r[FIELDS.index("title")].startswith("DEMO-Study-") for r in rows))
    failed = [c for c in checks if not c[1]]
    if failed:
        for name, _, _d in failed:
            print(f"  - {name}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="make_demo_master_table.py")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.write:
        return write_all()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
