#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_templates.py — 从实现代码生成 CSV 模板（禁止手写模板值）

本脚本是**全工作包唯一的模板生成器**（STEP4-TODO.md C2：模板禁止手写）：
  S5  templates/edi_results_OUTPUT_example.csv
  S5  templates/pooled_results_OUTPUT_example.csv
  S1  skills/S1-search-strategy/templates/raw_records_template.csv
  S1  skills/S1-search-strategy/templates/source_summary_template.csv
  S2  skills/S2-screening-quality/templates/{dedup_decisions, dedup_summary,
      title_abstract_screening, screening_summary, fulltext_screening,
      included_studies, excluded_studies, screening_log}_template.csv

动机
  手写 CSV 模板反复出现两类错误：列错位与数值与代码不一致。
  本脚本按"已提交模板纪律"（STEP4-TODO.md **C2**）改为：模板值**一律由实现代码算出**
  （S2 模板由 S2 的去重/分类/清单引擎对内置夹具真实运行算出）。

用法
  python gen_templates.py --self-test        # 校验模板与代码一致性（CI 用）
  python gen_templates.py --write            # 重新生成模板文件
  python gen_templates.py --check            # 只校验不写（等价 self-test）

校验内容
  1. 列数与表头一致（列对齐）
  2. 关键字段值可由实现代码复现（数值一致性）
  3. 枚举值合法（population_group 等）
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# 导入兄弟脚本（同目录）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import edi_calculation as edi  # noqa: E402
import weighted_gm as wgm      # noqa: E402

# shared/record_schema.py —— S1 题录字段的单一来源
_REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO / "shared"))
from record_schema import (  # noqa: E402
    NOTE_PENDING_FILL, RECORD_FIELDS, SUMMARY_FIELDS, compute_row_hash,
)

# S2 实现代码（模板生成器消费实现，方向永远是 生成器 → 实现，不存在技能间数据依赖）
sys.path.insert(0, str(_REPO / "skills" / "S2-screening-quality" / "scripts"))
import screening_schema as s2  # noqa: E402
import dedup_records as s2_dedup  # noqa: E402
import classify_articles as s2_classify  # noqa: E402
import fulltext_screening as s2_ft  # noqa: E402
import generate_inclusion_list as s2_list  # noqa: E402

TPL_DIR = Path(__file__).resolve().parent.parent / "templates"
S1_TPL_DIR = _REPO / "skills" / "S1-search-strategy" / "templates"
S2_TPL_DIR = _REPO / "skills" / "S2-screening-quality" / "templates"

# ★ 模板命名约定：**输出示例**必须带 `_OUTPUT_example` 后缀，
#   以免被误当作某个 Skill 的**输入**（例如把 S5 的合并结果当 S4 的输入）。
#   输入模板（如 S3 的 extraction_template.csv）则不带该后缀。
EDI_TPL_NAME = "edi_results_OUTPUT_example.csv"
POOLED_TPL_NAME = "pooled_results_OUTPUT_example.csv"
RAW_TPL_NAME = "raw_records_template.csv"
SUMMARY_TPL_NAME = "source_summary_template.csv"

# ---------------------------------------------------------------------------
# 契约枚举（用于校验）
# ---------------------------------------------------------------------------
POPULATION_GROUP_ENUM = {"Adults", "Minors", "Pregnant", "Elderly", "Mixed", "Unknown", ""}
SAMPLE_TYPE_ENUM = {"Urine", "Blood", "CordBlood", "BreastMilk", "Nail", "Hair",
                    "Serum", "Plasma", "Other"}


# ===========================================================================
# 1. EDI 结果模板（值全部由 edi_calculation 算出）
# ===========================================================================

EDI_HEADER = ["record_id", "sample_type", "population_group", "gm_summary", "sample_size",
              "route", "edi_primary", "edi_secondary", "cross_check_rel_diff",
              "status", "flags", "message", "params"]

# (record_id, sample_type, population_group, gm, n)
EDI_INPUTS = [
    ("R00001", "Urine",     "Adults",   32.58, 1200),
    ("R00002", "Urine",     "Pregnant", 12.45,  800),
    ("R00003", "Urine",     "Minors",   55.33,  450),
    ("R00004", "Urine",     "Mixed",    23.50,  600),
    ("R00005", "Urine",     "Unknown",  23.50,  300),
    ("R00006", "Blood",     "Adults",    2.72,  800),
    ("R00007", "Blood",     "Pregnant",  1.99,  400),
    ("R00008", "Blood",     "Adults",    1.39,  300),
    ("R00009", "CordBlood", "Adults",    4.89,  300),
    ("R00010", "Urine",     "Adults",   None,   500),
]


def fmt(x, nd=6):
    if x is None:
        return ""
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def build_edi_rows() -> list[list[str]]:
    cfg = edi.FIXTURE_EDI_PARAMS     # 自检/模板生成专用夹具
    out: list[list[str]] = []
    for rid, st, pg, gm, n in EDI_INPUTS:
        if gm is None:
            out.append([rid, st, pg, "", str(n), "", "", "", "", "SKIP", "",
                        "gm_summary 缺失，无法计算",
                        f"record_id={rid};counted=True"])
            continue
        if st == "Urine":
            r = edi.compute_urine_edi(gm, pg, cfg)
            out.append([rid, st, pg, fmt(gm), str(n), "urine",
                        fmt(r.edi_primary), fmt(r.edi_secondary),
                        fmt(r.cross_check_rel_diff, 6) if r.cross_check_rel_diff is not None else "",
                        r.status, ";".join(r.flags), " | ".join(r.messages), r.params])
        elif st in ("Blood", "CordBlood"):
            # ★ 必须传 sample_type：脐血走"方案 A 跳过"分支，不套成人参数
            r = edi.compute_blood_edi(gm, pg, cfg, sample_type=st)
            out.append([rid, st, pg, fmt(gm), str(n), "blood",
                        fmt(r.edi_primary), "", "",
                        r.status, ";".join(r.flags), " | ".join(r.messages), r.params])
        else:
            out.append([rid, st, pg, fmt(gm), str(n), "", "", "", "", "SKIP", "",
                        f"sample_type=`{st}` 不支持 EDI", f"record_id={rid};counted=True"])
    return out


# ===========================================================================
# 2. 分层合并结果模板（stratum 单一来源 + 分解列）
# ===========================================================================

POOLED_DIMS = ["sample_type", "period", "region", "province", "population_group"]

POOLED_INPUTS = [
    # 尿 1980-2000 西南
    ("R1", "Urine", "1995", "Southwest", "云南省", "Adults", 32.58, 1000, "S001"),
    ("R2", "Urine", "1998", "Southwest", "贵州省", "Adults", 32.58, 500, "S002"),
    # 同省多记录 → 单记录主导
    ("R3", "Urine", "1996", "Southwest", "云南省", "Adults", 46.32, 400, "S003"),
    ("R4", "Urine", "1994", "Southwest", "云南省", "Adults", 46.32, 400, "S004"),
    # 尿 2001-2010 华东
    ("R5", "Urine", "2005", "East", "山东省", "Adults", 12.45, 800, "S005"),
    # 尿 2011-2020 西南 / 华北
    ("R6", "Urine", "2015", "Southwest", "贵州省", "Adults", 39.03, 900, "S006"),
    ("R7", "Urine", "2016", "North", "北京市", "Adults", 8.87, 700, "S007"),
    # 尿 2021-2024 华东
    ("R8", "Urine", "2022", "East", "山东省", "Adults", 23.52, 500, "S008"),
    # 血 2001-2010 华北
    ("R9", "Blood", "2005", "North", "北京市", "Adults", 2.72, 600, "S009"),
    # 血 2011-2020 华北 / 西南
    ("R10", "Blood", "2015", "North", "北京市", "Adults", 4.24, 400, "S010"),
    ("R11", "Blood", "2016", "Southwest", "云南省", "Adults", 3.91, 300, "S011"),
    # 血 2021-2024 华东（单记录主导）
    ("R12", "Blood", "2022", "East", "山东省", "Adults", 1.39, 1180, "S012"),
    # 脐血 2011-2020 华东
    ("R13", "CordBlood", "2016", "East", "江苏省", "Adults", 4.89, 500, "S013"),
    # 尿 2011-2020 华东（大样本主导）
    ("R14", "Urine", "2017", "East", "上海市", "Adults", 27.66, 100000, "S014"),
    ("R15", "Urine", "2018", "East", "上海市", "Adults", 30.00, 100, "S015"),
]


def build_pooled_rows(scheme: dict) -> tuple[list[list[str]], list[str], list[str]]:
    """
    返回 (数据行, 表头, 组成 stratum 的维度名)。
    表头 = 分解列（STRATA_DIMS 子集）+ 指标列 + stratum（人读标签，放最后）
    """
    rows_std = []
    for rid, st, yr, region, prov, pg, gm, n, sno in POOLED_INPUTS:
        rows_std.append({"record_id": rid, "sample_type": st, "time": yr,
                         "region": region, "province": prov,
                         "population_group": pg, "gm_summary": str(gm),
                         "sample_size": str(n), "study_no": sno})

    # 分层维度：sample_type / period / region / province
    # 注意：不按 population_group 分层 → 该列留空 + note 标注 not_stratified
    dims = ["sample_type", "period", "region", "province"]
    res, msgs = wgm.pool(rows_std, scheme, dims=dims)

    header = ["stratum"] + dims + [
        "population_group", "gm", "gsd", "record_count", "study_count",
        "total_sample_size", "max_weight_share", "note"]

    out = []
    for r in res:
        dim_vals = [str(r.dims.get(d, "")) for d in dims]
        # stratum 由同源维度拼出（分隔符与 S5 脚本一致）
        stratum = " | ".join(dim_vals)
        note = r.note
        # 未按人群分层 → 留空 + 标注（契约枚举无 All）
        pg_val = r.dims.get("population_group", "")
        if not pg_val:
            note = ("not_stratified" + ("; " + note if note else ""))
        out.append([stratum] + dim_vals
                   + [pg_val, f"{r.gm:.6f}", f"{r.gsd:.6f}", str(r.record_count),
                      str(r.study_count), f"{r.total_sample_size:g}",
                      f"{r.max_weight_share:.4f}", note])
    return out, header, dims


# ===========================================================================
# 2b. S1 检索题录模板（字段与哈希取自 shared/record_schema.py 单一来源）
# ===========================================================================

def build_raw_records_rows() -> tuple[list[str], list[list[str]]]:
    """raw_records_template：统一字段表头 + 2 条示例题录（哈希由代码算出）。"""
    rec1 = {
        "record_id": "pubmed-00001", "source_database": "PubMed",
        "title": "Urinary arsenic in general population: a national survey",
        "abstract": "Background: national biomonitoring data on arsenic exposure "
                    "remain limited. Methods: we measured urinary arsenic.",
        "authors": "Zhang, San; Li, Si", "year": "2020",
        "doi": "10.1234/example.2020.001", "pmid": "32456789",
        "journal": "Environmental Research", "keywords": "arsenic; biomonitoring",
    }
    rec1["raw_row_hash"] = compute_row_hash(rec1["title"], rec1["abstract"])
    rec2 = {
        "record_id": "wos-00001", "source_database": "WebOfScience",
        "title": "Blood cadmium in parturients", "abstract": "",
    }
    rec2["raw_row_hash"] = compute_row_hash(rec2["title"], rec2["abstract"])
    rows = []
    for rec in (rec1, rec2):
        rows.append([rec.get(f, "") for f in RECORD_FIELDS])
    return list(RECORD_FIELDS), rows


def build_source_summary_rows() -> tuple[list[str], list[list[str]]]:
    """source_summary_template：检索式/命中数由人工回填（GATE-1），导出数示例。"""
    rows = [
        ["PubMed",
         '("Arsenic"[MeSH Terms] OR "arsenic"[tiab]) AND ("urine"[tiab] OR '
         '"urinary"[tiab]) AND ("human biomonitoring"[tiab])',
         "2026-07-19", "3120", "3120",
         "检索式示例：请以 GATE-1 确认后的最终检索式原文为准"],
        ["WebOfScience", "", "", "", "1875", NOTE_PENDING_FILL],
    ]
    return list(SUMMARY_FIELDS), rows


# ===========================================================================
# 2c. S2 筛选质控模板（值由 S2 引擎对内置夹具**真实运行**算出）
# ===========================================================================

S2_DEDUP_DECISIONS_TPL = "dedup_decisions_template.csv"
S2_DEDUP_SUMMARY_TPL = "dedup_summary_template.csv"
S2_TA_TPL = "title_abstract_screening_template.csv"
S2_SUMMARY_TPL = "screening_summary_template.csv"
S2_FT_TPL = "fulltext_screening_template.csv"
S2_INCLUDED_TPL = "included_studies_template.csv"
S2_EXCLUDED_TPL = "excluded_studies_template.csv"
S2_LOG_TPL = "screening_log_template.csv"

# S2 夹具：3 条题录（A/B 同 DOI；C 唯一）→ 走真实去重/分类/全文/清单引擎
_S2_RECORDS = [
    {"record_id": "pubmed-00001", "source_database": "PubMed",
     "title": "Urinary arsenic in general population of China",
     "abstract": "A biomonitoring survey reporting geometric mean concentrations.",
     "authors": "Zhang, San; Li, Si", "year": "2020",
     "doi": "10.1234/a.2020.001", "pmid": "32456789",
     "journal": "Environmental Research", "keywords": "arsenic",
     "raw_row_hash": ""},
    {"record_id": "wos-00001", "source_database": "WebOfScience",
     "title": "URINARY ARSENIC IN GENERAL POPULATION OF CHINA",
     "abstract": "", "authors": "Zhang, San", "year": "2020",
     "doi": "10.1234/a.2020.001", "pmid": "", "journal": "J",
     "keywords": "", "raw_row_hash": ""},
    {"record_id": "scopus-00001", "source_database": "Scopus",
     "title": "Blood mercury in parturients",
     "abstract": "Mercury levels measured in 200 pregnant women.",
     "authors": "Wang, Wu", "year": "2021", "doi": "10.1234/b.2021.002",
     "pmid": "", "journal": "Chemosphere", "keywords": "mercury",
     "raw_row_hash": ""},
]

_S2_FULLTEXT = {
    "pubmed-00001": "Methods: urinary arsenic measured in 1200 adults recruited "
                    "in 2015. Results: geometric mean 35.2 ug/L (95% CI 30-41). "
                    "Participants were general residents.",
    "scopus-00001": "Methods: mercury concentrations measured in 200 pregnant "
                    "women recruited in 2015. Results: geometric mean 0.9 ug/L "
                    "(95% CI 0.7-1.1). Participants were general residents.",
}

# 空摘要记录在 fixture 中留空 → normalize 引擎按空串处理
_S2_FIX_RECORDS = [dict(r) for r in _S2_RECORDS]


def build_s2_rows() -> dict[str, tuple[list[str], list[list[str]]]]:
    """
    返回 {模板名: (表头, 行)}。全部值由 S2 实现代码算出：
    去重 → 分类 → 汇总 → 全文 → 清单 → 日志（一条微型流水线）。
    """
    spec = s2.load_fixture_spec()

    # S2-A：去重（夹具跑真实引擎）
    decisions, unique_rows, stats = s2_dedup.build_dedup(_S2_FIX_RECORDS, spec)
    out: dict[str, tuple[list[str], list[list[str]]]] = {}
    out[S2_DEDUP_DECISIONS_TPL] = (
        list(s2.DEDUP_DECISION_FIELDS),
        [[d.get(f, "") for f in s2.DEDUP_DECISION_FIELDS] for d in decisions])
    out[S2_DEDUP_SUMMARY_TPL] = (
        list(s2.LOG_FIELDS),
        [[r["metric"], r["value"]] for r in s2_dedup.summary_rows(stats)])

    # S2-B：分类 + 汇总
    ta_rows = s2_classify.classify_records(unique_rows, spec)
    out[S2_TA_TPL] = (
        list(s2.TA_FIELDS),
        [[r.get(f, "") for f in s2.TA_FIELDS] for r in ta_rows])
    # 模板示例中人工列给两条示例裁决（均 proceed，与全文夹具一致）——
    # 人工列取值域见 screening_schema.HUMAN_TA_DECISIONS；未示例的排除写法见
    # excluded_studies 模板（human_final_reason_code 走闭集 wrong_analyte）
    for i, r in enumerate(out[S2_TA_TPL][1]):
        if i in (0, 1):
            r[s2.TA_FIELDS.index("human_decision")] = "proceed_to_fulltext"
    out[S2_SUMMARY_TPL] = (
        list(s2.SUMMARY_FIELDS),
        [[r["scope"], r["category"], r["count"]]
         for r in s2_classify_summary_rows(ta_rows, spec)])

    # S2-C：全文（夹具全文写临时目录；人工终裁给示例值）
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="hbm_s2_tpl_"))
    ft_dir = tmp / "fulltexts"
    ft_dir.mkdir(parents=True, exist_ok=True)
    for rid, text in _S2_FULLTEXT.items():
        (ft_dir / f"{rid}.txt").write_text(text, encoding="utf-8")
    ta_for_ft = []
    for r in ta_rows:
        row = dict(r)
        row["human_decision"] = "proceed_to_fulltext"
        ta_for_ft.append(row)
    ft_rows, _stats = s2_ft.build_ft_rows(ta_for_ft, unique_rows, spec,
                                          (1980, 2024), ft_dir)
    out[S2_FT_TPL] = (
        list(s2.FT_FIELDS),
        [[r.get(f, "") for f in s2.FT_FIELDS] for r in ft_rows])

    # 收口：清单 + 日志（人工终裁示例：首条 include，其余 exclude 带闭集编码）
    ft_final = []
    for i, r in enumerate(ft_rows):
        row = dict(r)
        if i == 0:
            row["human_final_decision"] = "include"
        else:
            row["human_final_decision"] = "exclude"
            row["human_final_reason_code"] = "wrong_analyte"
        ft_final.append(row)
    included, excluded, log_rows, _stats2 = s2_list.build_lists(
        ft_final, unique_rows, ta_for_ft, spec)
    out[S2_INCLUDED_TPL] = (
        list(s2.INCLUDED_FIELDS),
        [[r.get(f, "") for f in s2.INCLUDED_FIELDS] for r in included])
    out[S2_EXCLUDED_TPL] = (
        list(s2.EXCLUDED_FIELDS),
        [[r.get(f, "") for f in s2.EXCLUDED_FIELDS] for r in excluded])
    out[S2_LOG_TPL] = (
        list(s2.LOG_FIELDS),
        [[r["metric"], r["value"]] for r in log_rows])
    return out


def s2_classify_summary_rows(ta_rows: list[dict], spec: dict):
    """screening_summary.build_summary 的薄封装（避免循环导入名字歧义）。"""
    import screening_summary as s2_sum
    return s2_sum.build_summary(ta_rows, spec)


# ===========================================================================
# 3. 写盘与校验
# ===========================================================================

def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def check_alignment(name: str, header: list[str], rows: list[list[str]]) -> list[str]:
    bad = []
    for i, r in enumerate(rows, start=2):
        if len(r) != len(header):
            bad.append(f"{name} 第 {i} 行列数 {len(r)} ≠ 表头 {len(header)}")
    return bad


def run_self_test() -> int:
    print("=" * 70)
    print("gen_templates.py --self-test（模板 ↔ 实现一致性校验）")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    scheme = wgm.FIXTURE_PERIOD_SCHEME
    edi_rows = build_edi_rows()
    pooled_rows, pooled_header, dims = build_pooled_rows(scheme)

    print("\n[1] 列对齐")
    errs = check_alignment("edi_results", EDI_HEADER, edi_rows)
    errs += check_alignment("pooled_results", pooled_header, pooled_rows)
    check("edi_results 列数一致", not [e for e in errs if "edi_results" in e],
          "; ".join(e for e in errs if "edi_results" in e) or "全部一致")
    check("pooled_results 列数一致",
          not [e for e in errs if "pooled_results" in e],
          "; ".join(e for e in errs if "pooled_results" in e) or "全部一致")

    print("\n[2] EDI 双路径相对差：必须由代码算出（不手写）")
    idx = {h: i for i, h in enumerate(EDI_HEADER)}
    by_id = {r[0]: r for r in edi_rows}
    preg = by_id["R00002"]
    rel_preg = float(preg[idx["cross_check_rel_diff"]])
    manual_preg = abs(12.45 * 1.275 / (60 * 0.7)
                      - 12.45 * 0.638 * 2.0 / (60 * 0.7)) / (12.45 * 1.275 / (60 * 0.7))
    check("Pregnant 相对差 ≈ 0.0784%", abs(rel_preg - 0.000784) < 5e-6,
          f"{rel_preg:.6f}（手算 {manual_preg:.6f}）")
    check("Pregnant 相对差不为 0", rel_preg > 0, f"{rel_preg:.6f}")
    check("Pregnant 未触发不自洽告警（<3%）",
          "inconsistent_param" not in preg[idx["flags"]], preg[idx["flags"]] or "无标记")
    ad = by_id["R00001"]
    rel_ad = float(ad[idx["cross_check_rel_diff"]])
    check("Adults 相对差 ≈ 0.0296%", abs(rel_ad - 0.000296) < 5e-6, f"{rel_ad:.6f}")
    mi = by_id["R00003"]
    check("Minors 触发 inconsistent_param",
          "inconsistent_param" in mi[idx["flags"]], mi[idx["flags"]])
    check("Minors 相对差 ≈ 7.5%", abs(float(mi[idx["cross_check_rel_diff"]]) - 0.0749) < 5e-4,
          mi[idx["cross_check_rel_diff"]])
    check("Adults edi_primary 可复现",
          abs(float(ad[idx["edi_primary"]])
              - edi.edi_urine_creatinine_excretion(32.58, 1.35, 60, 0.7)) < 1e-6,
          ad[idx["edi_primary"]])

    print("\n[3] 血路与脐血处理")
    for rid, label in [("R00006", "Blood/Adults"), ("R00009", "CordBlood/Adults")]:
        r = by_id[rid]
        print(f"      · {rid} ({label}): status={r[idx['status']]} "
              f"flags={r[idx['flags']]!r} edi={r[idx['edi_primary']]}")
    check("血路标注 exploratory_reconstruction",
          "exploratory_reconstruction" in by_id["R00006"][idx["flags"]])
    check("★ 脐血不计算 EDI（方案 A：跳过并计数）",
          by_id["R00009"][idx["status"]] == "SKIP"
          and by_id["R00009"][idx["edi_primary"]] == "",
          f"status={by_id['R00009'][idx['status']]} "
          f"edi={by_id['R00009'][idx['edi_primary']] or '(空)'}")
    check("脐血跳过原因含胎儿参数说明",
          "胎儿" in by_id["R00009"][idx["message"]],
          by_id["R00009"][idx["message"]][:50])

    print("\n[4] Mixed / Unknown 跳过")
    for rid in ("R00004", "R00005"):
        check(f"{rid} → SKIP", by_id[rid][idx["status"]] == "SKIP",
              by_id[rid][idx["message"]][:40])

    print("\n[5] pooled_results 结构（stratum 单一来源 + 分解列）")
    check("stratum 在首列（人读标签）", pooled_header[0] == "stratum")
    check("分解列紧随其后且与维度一致", pooled_header[1:1 + len(dims)] == dims,
          str(pooled_header[1:1 + len(dims)]))
    check("无重复的拼接列", pooled_header.count("stratum") == 1)
    ok_stratum = True
    for r in pooled_rows:
        expect = " | ".join(r[1:1 + len(dims)])
        if r[0] != expect:
            ok_stratum = False
            break
    check("stratum == 分解列按同源分隔符拼接（单一来源）", ok_stratum)

    print("\n[6] population_group 枚举合法（无 All）")
    pgi = pooled_header.index("population_group")
    vals = {r[pgi] for r in pooled_rows}
    check("无越界值 All", "All" not in vals, str(sorted(vals)))
    check("取值均在契约枚举内", vals <= POPULATION_GROUP_ENUM, str(sorted(vals)))
    check("未按人群分层 → 留空", any(v == "" for v in vals))

    print("\n[7] 未分层标注 not_stratified")
    ni = pooled_header.index("note")
    blank_pg_rows = [r for r in pooled_rows if r[pgi] == ""]
    check("留空的 population_group 行均有 not_stratified 标注",
          all("not_stratified" in r[ni] for r in blank_pg_rows),
          f"{len(blank_pg_rows)} 行")

    print("\n[8] 权重占比与记录数告警可复现")
    check("存在 max_weight_share > 30% 的层",
          any(float(r[pooled_header.index("max_weight_share")]) > 0.30 for r in pooled_rows))
    check("单记录主导层已触发敏感性提示",
          any("敏感性" in r[ni] for r in pooled_rows),
          next((r[0] for r in pooled_rows if "敏感性" in r[ni]), "无"))
    check("记录数不足层已标注",
          any("解释谨慎" in r[ni] for r in pooled_rows))

    print("\n[9] 与已提交文件的一致性（若文件存在）")
    raw_header, raw_rows = build_raw_records_rows()
    sum_header, sum_rows = build_source_summary_rows()
    tpl_specs = [(TPL_DIR / EDI_TPL_NAME, EDI_HEADER, edi_rows),
                 (TPL_DIR / POOLED_TPL_NAME, pooled_header, pooled_rows),
                 (S1_TPL_DIR / RAW_TPL_NAME, raw_header, raw_rows),
                 (S1_TPL_DIR / SUMMARY_TPL_NAME, sum_header, sum_rows)]
    s2_specs = build_s2_rows()
    for name, (header, rows) in s2_specs.items():
        tpl_specs.append((S2_TPL_DIR / name, header, rows))
    for p, header, rows in tpl_specs:
        name = p.name
        if not p.exists():
            check(f"{name} 存在", False, "文件缺失（可跑 --write 生成）")
            continue
        h2, r2 = read_csv(p)
        same_header = h2 == header
        same_rows = r2 == rows
        check(f"{name} 表头与生成一致", same_header,
              "" if same_header else f"文件={h2[:4]}… 生成={header[:4]}…")
        check(f"{name} 数据与生成一致", same_rows,
              "" if same_rows else f"文件 {len(r2)} 行 / 生成 {len(rows)} 行")

    print("\n[10] S1 题录模板与 record_schema 单一来源对齐")
    check("raw 模板表头 == RECORD_FIELDS", raw_header == RECORD_FIELDS)
    check("summary 模板表头 == SUMMARY_FIELDS", sum_header == SUMMARY_FIELDS)
    errs = check_alignment("raw_records", raw_header, raw_rows)
    errs += check_alignment("source_summary", sum_header, sum_rows)
    check("S1 模板列数一致", not errs, "; ".join(errs) or "全部一致")
    h_idx = raw_header.index("raw_row_hash")
    t_idx = raw_header.index("title")
    a_idx = raw_header.index("abstract")
    recompute_ok = all(
        compute_row_hash(r[t_idx], r[a_idx]) == r[h_idx] for r in raw_rows)
    check("示例行哈希可由 compute_row_hash 复现", recompute_ok)
    check("示例行 title 均非空", all(r[t_idx] for r in raw_rows))
    check("待回填提示语来自单一来源常量",
          any(r[sum_header.index("note")] == NOTE_PENDING_FILL for r in sum_rows))

    print("\n[11] S2 筛选模板（值由 S2 引擎真实运行算出）")
    check("S2 模板共 8 份", len(s2_specs) == 8, str(sorted(s2_specs)))
    dd_header, dd_rows = s2_specs[S2_DEDUP_DECISIONS_TPL]
    dd_by = {r[dd_header.index("record_id")]: r for r in dd_rows}
    ai_idx = dd_header.index("ai_decision")
    check("去重模板：精确重复标 duplicate（pubmed-00001/wos-00001 同 DOI）",
          dd_by["wos-00001"][ai_idx] == "duplicate")
    check("去重模板：human_decision 列全部留空（只由人填写）",
          all(r[dd_header.index("human_decision")] == "" for r in dd_rows))
    check("去重模板：决策值在闭集内",
          {r[ai_idx] for r in dd_rows} <= set(s2.DEDUP_AI_DECISIONS))
    ta_header, ta_rows_s2 = s2_specs[S2_TA_TPL]
    check("题摘模板：AI 类型在配置闭集内",
          {r[ta_header.index("ai_article_type")] for r in ta_rows_s2}
          <= set(s2.load_fixture_spec()["article_types"]))
    ft_header, ft_rows_s2 = s2_specs[S2_FT_TPL]
    check("全文模板：排除编码在闭集内",
          all(r[ft_header.index("ai_reason_code")] in
              s2.load_fixture_spec()["exclusion_reasons"]
              or r[ft_header.index("ai_reason_code")] == "" for r in ft_rows_s2))
    log_header, log_rows_s2 = s2_specs[S2_LOG_TPL]
    log_by = {r[log_header.index("metric")]: r[log_header.index("value")]
              for r in log_rows_s2}
    check("日志模板数字自洽（identification 恒等式）",
          int(log_by["records_identified_total"])
          == int(log_by["records_after_dedup"]) + int(log_by["duplicates_removed"]))
    inc_rows = s2_specs[S2_INCLUDED_TPL][1]
    exc_rows = s2_specs[S2_EXCLUDED_TPL][1]
    check("清单模板：纳入 + 排除 = 全文评估数",
          len(inc_rows) + len(exc_rows) == int(log_by["fulltext_assessed"]))
    check("S2 模板确定性（两次构建一致）",
          build_s2_rows() == s2_specs)

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
        prog="gen_templates.py",
        description="从实现代码生成/校验 CSV 模板（HBM-Meta-Agent）")
    ap.add_argument("--self-test", action="store_true", help="校验模板与代码一致性")
    ap.add_argument("--check", action="store_true", help="同 --self-test")
    ap.add_argument("--write", action="store_true", help="重新生成模板文件")
    args = ap.parse_args(argv)

    if args.write:
        scheme = wgm.FIXTURE_PERIOD_SCHEME
        edi_rows = build_edi_rows()
        pooled_rows, pooled_header, _ = build_pooled_rows(scheme)
        raw_header, raw_rows = build_raw_records_rows()
        sum_header, sum_rows = build_source_summary_rows()
        TPL_DIR.mkdir(parents=True, exist_ok=True)
        S1_TPL_DIR.mkdir(parents=True, exist_ok=True)
        S2_TPL_DIR.mkdir(parents=True, exist_ok=True)
        write_csv(TPL_DIR / EDI_TPL_NAME, EDI_HEADER, edi_rows)
        write_csv(TPL_DIR / POOLED_TPL_NAME, pooled_header, pooled_rows)
        write_csv(S1_TPL_DIR / RAW_TPL_NAME, raw_header, raw_rows)
        write_csv(S1_TPL_DIR / SUMMARY_TPL_NAME, sum_header, sum_rows)
        for name, (header, rows) in build_s2_rows().items():
            write_csv(S2_TPL_DIR / name, header, rows)
        print(f"已生成：{TPL_DIR / EDI_TPL_NAME}")
        print(f"已生成：{TPL_DIR / POOLED_TPL_NAME}")
        print(f"已生成：{S1_TPL_DIR / RAW_TPL_NAME}")
        print(f"已生成：{S1_TPL_DIR / SUMMARY_TPL_NAME}")
        print(f"已生成：S2 模板 8 份 → {S2_TPL_DIR}")
        return 0

    return run_self_test()


if __name__ == "__main__":
    raise SystemExit(main())
