#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_demo_inputs.py — 生成 bpa-china-demo 的 S1 合成输入（RIS ×3 + 题录 CSV）

★★ 所有题录均为虚构（标题/作者/DOI/PMID/数值全部编造），仅用于测试。★★

单一定义处：RECORDS 列表 → 派生
  01-search/pubmed_export.ris / wos_export.ris / scopus_export.ris
  01-search/raw_records_example.csv（R001…R020，raw_row_hash 由
  shared/record_schema.compute_row_hash 单一来源算法算出）
  01-search/source_summary.csv（检索登记示例）

用法
  python make_demo_inputs.py --write      # 写出全部文件
  python make_demo_inputs.py --self-test  # 校验记录设计不变式（类别配额/重复对/哈希）
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
SEARCH = HERE / "01-search"
_ROOT = HERE.parent.parent
sys.path.insert(0, str(_ROOT / "shared"))
from record_schema import compute_row_hash  # noqa: E402

# ── 20 条合成题录（单一定义处）────────────────────────────────────
# 设计配额：8 human_hbm_original + 3 review + 3 animal + 2 in_vitro
#          + 2 human_non_hbm + 2 重复（R003 与 R001 同 DOI → 精确去重；
#            R018 与 R001 同题同人同年但无 DOI → 模糊去重）
# 设计配额：每条 (rid, 文件, 类别, title, abstract, authors, year, doi, pmid, journal, keywords)
RECORDS = [
    # ── human_hbm_original ×8（合并目标：6 纳入 + R017 no_usable_values + R009 duplicate_cohort）
    ("R001", "pubmed", "hbm",
     "Urinary bisphenol A concentrations in adults from Shanghai, China: a cross-sectional study",
     "A cross-sectional biomonitoring survey of 1200 adults from the general population of Shanghai. Urinary bisphenol A was measured by isotope dilution liquid chromatography tandem mass spectrometry.",
     "Zhang W; Li M; Chen H", "2019", "10.1234/demo.001", "31100222",
     "Environment International", "bisphenol A; urinary; biomonitoring"),
    ("R002", "pubmed", "hbm",
     "Urinary bisphenol A exposure and reproductive outcomes in Chinese pregnant women: a cohort study",
     "A prospective cohort of 800 pregnant women recruited in Hangzhou. Urinary bisphenol A concentrations were determined in each trimester and related to reproductive outcomes.",
     "Wang L; Liu Y", "2020", "10.1234/demo.002", "32100333",
     "Environmental Health Perspectives", "bisphenol A; pregnancy; urine"),
    ("R004", "pubmed", "hbm",
     "Urinary bisphenol A levels among children in Guangdong, China: a school-based survey",
     "A school-based survey measuring urinary bisphenol A in 640 children aged 6 to 12 years from Guangdong. Geometric mean concentrations are reported by sex and age group.",
     "Chen X; Zhao Q", "2021", "10.1234/demo.004", "33100444",
     "Science of the Total Environment", "bisphenol A; children; urine"),
    ("R005", "pubmed", "hbm",
     "Urinary bisphenol A concentrations and type 2 diabetes in Chinese adults: a longitudinal study",
     "A longitudinal study of 980 Chinese adults examining the association between urinary bisphenol A and incident type 2 diabetes over four years of follow-up.",
     "Liu J; Sun H", "2018", "10.1234/demo.005", "34100555",
     "Diabetes Care", "bisphenol A; diabetes; urinary"),
    ("R006", "pubmed", "hbm",
     "Urinary bisphenol A in pregnant women from Zhejiang, China: exposure levels and determinants",
     "Cross-sectional analysis of urinary bisphenol A in 500 pregnant women from Zhejiang, with determinants including diet and personal care product use.",
     "Huang Y; Zhou M", "2022", "10.1234/demo.006", "35100666",
     "Chemosphere", "bisphenol A; pregnant; urine"),
    ("R009", "wos", "hbm",
     "Urinary bisphenol A and thyroid hormone levels in Chinese adults: a repeated-measures study",
     "Repeated urinary bisphenol A measurements in 430 adults from Tianjin with concurrent thyroid hormone assessment. Sample collection was performed in 2019.",
     "Zhou F; Wu G", "2020", "10.1234/demo.009", "36100777",
     "Environmental Research", "bisphenol A; thyroid; urinary"),
    ("R015", "scopus", "hbm",
     "Urinary concentrations of bisphenol A among pregnant women in Beijing, China",
     "Biomonitoring of 700 pregnant women in Beijing. Urinary bisphenol A geometric mean concentrations are reported overall and by trimester.",
     "Xu L; Ma J", "2023", "10.1234/demo.015", "37100888",
     "Environmental Pollution", "bisphenol A; pregnant; biomonitoring"),
    ("R017", "scopus", "hbm",
     "Urinary bisphenol A levels in preschool children from rural Sichuan, China: seasonal variation",
     "Seasonal assessment of bisphenol A exposure among 300 preschool children in rural Sichuan. Detection frequencies and demographic characteristics are described.",
     "Yang K; Li T", "2024", "10.1234/demo.017", "38100999",
     "Journal of Exposure Science", "bisphenol A; children; urine"),
    # ── review ×3
    ("R007", "pubmed", "review",
     "Bisphenol A and endocrine disruption: a systematic review of human studies",
     "A systematic review synthesizing human evidence on bisphenol A and endocrine outcomes, following PRISMA guidance.",
     "Smith J; Brown K", "2021", "10.1234/demo.007", "39101111",
     "Toxicology Reviews", "bisphenol A; review; endocrine"),
    ("R011", "wos", "review",
     "Bisphenol A exposure assessment in human populations: a meta-analysis of urinary concentrations",
     "A meta-analysis pooling urinary bisphenol A concentrations reported in human biomonitoring studies worldwide.",
     "Rossi M; Bianchi P", "2022", "10.1234/demo.011", "40101222",
     "Environment International", "bisphenol A; meta-analysis"),
    ("R014", "wos", "review",
     "Bisphenol A research in China: a narrative review of environmental and human studies",
     "A narrative review summarizing two decades of bisphenol A research in China across environmental and human disciplines.",
     "White R; Green T", "2016", "10.1234/demo.014", "41101333",
     "Trends in Environmental Analysis", "bisphenol A; review; China"),
    # ── animal_study ×3
    ("R010", "wos", "animal",
     "Effects of bisphenol A on hepatic gene expression in Sprague-Dawley rats",
     "Male Sprague-Dawley rats were exposed to bisphenol A by oral gavage for 28 days and hepatic gene expression was profiled.",
     "Tanaka S; Yamamoto K", "2018", "10.1234/demo.010", "42101444",
     "Toxicological Sciences", "bisphenol A; rats; liver"),
    ("R012", "wos", "animal",
     "Developmental exposure to bisphenol A alters social behavior in mice",
     "Mice exposed developmentally to bisphenol A showed altered social interaction in adulthood. Behavioral domains and doses are described.",
     "Kim D; Park S", "2019", "10.1234/demo.012", "43101555",
     "Neurotoxicology", "bisphenol A; mice; behavior"),
    ("R013", "pubmed", "animal",
     "Reproductive toxicity of bisphenol A in male rats: a dose-response study",
     "A dose-response study of bisphenol A administration in male rats with reproductive organ histopathology and sperm analysis.",
     "Garcia R; Lopez S", "2016", "10.1234/demo.013", "44101666",
     "Reproductive Toxicology", "bisphenol A; rats; reproduction"),
    # ── in_vitro_study ×2
    ("R008", "pubmed", "in_vitro",
     "Effects of bisphenol A on HepG2 cell line viability and oxidative stress responses",
     "HepG2 cells were exposed to bisphenol A at environmental and supraphysiological concentrations; viability and oxidative stress markers were assessed.",
     "Nakamura T; Sato H", "2017", "10.1234/demo.008", "45101777",
     "Toxicology in Vitro", "bisphenol A; cell line; oxidative stress"),
    ("R016", "scopus", "in_vitro",
     "Bisphenol A induces mitochondrial dysfunction in vitro in HEK293 cells",
     "An in vitro characterization of bisphenol A effects on mitochondrial membrane potential in HEK293 cells.",
     "Ahmed N; Kaur S", "2017", "10.1234/demo.016", "46101888",
     "Cell Biology and Toxicology", "bisphenol A; in vitro; mitochondria"),
    # ── human_non_hbm ×2（人群/环境研究，但无人体内暴露浓度）
    ("R019", "scopus", "non_hbm",
     "Bisphenol A in thermal paper receipts: migration and dermal exposure assessment",
     "Migration of bisphenol A from thermal paper receipts was quantified and dermal uptake was estimated with a hand-wipe technique. No human biological samples were collected.",
     "Martin P; Dubois L", "2015", "10.1234/demo.019", "47101999",
     "Journal of Hazardous Materials", "bisphenol A; thermal paper; dermal"),
    ("R020", "pubmed", "non_hbm",
     "Dietary determinants of bisphenol A intake among Chinese households: a questionnaire-based survey",
     "A questionnaire-based survey of dietary habits in 900 Chinese households used to model intake of bisphenol A from canned food and packaged beverages. No biomarkers were measured.",
     "Sun Q; Gao W", "2023", "10.1234/demo.020", "48102020",
     "Food and Chemical Toxicology", "bisphenol A; dietary; questionnaire"),
    # ── 重复 ×2
    ("R003", "wos", "dup_exact",
     "Urinary bisphenol A concentrations in adults from Shanghai, China: a cross-sectional biomonitoring study",
     "A biomonitoring survey of 1200 adults from the general population of Shanghai. Urinary bisphenol A was measured by isotope dilution liquid chromatography tandem mass spectrometry with isotopically labelled standards.",
     "Zhang W; Li M; Chen H", "2019", "10.1234/demo.001", "31100223",
     "Environment International", "bisphenol A; urinary; biomonitoring"),
    ("R018", "wos", "dup_fuzzy",
     "Urinary bisphenol A concentrations in adults from Shanghai, China: a cross-sectional study",
     "The same Shanghai adult cohort as the Environment International publication; this record is the database duplicate without a registered DOI.",
     "Zhang W; Li M; Chen H", "2019", "", "",
     "Environment International", "bisphenol A; urinary"),
]

FILE_OF = {"pubmed": "pubmed_export.ris", "wos": "wos_export.ris",
           "scopus": "scopus_export.ris"}
SOURCE_NAME = {"pubmed": "PubMed", "wos": "WebOfScience", "scopus": "Scopus"}

# 设计不变式（--self-test 断言）
EXPECTED_QUOTA = {"hbm": 8, "review": 3, "animal": 3, "in_vitro": 2,
                  "non_hbm": 2, "dup_exact": 1, "dup_fuzzy": 1}


def ris_text(records) -> str:
    lines = []
    for rid, _file, _cat, title, abstract, authors, year, doi, pmid, journal, kw in records:
        lines.append("TY  - JOUR")
        for a in authors.split(";"):
            lines.append(f"AU  - {a.strip()}")
        lines.append(f"TI  - {title}")
        lines.append(f"AB  - {abstract}")
        lines.append(f"PY  - {year}")
        if doi:
            lines.append(f"DO  - {doi}")
        if pmid:
            lines.append(f"AN  - {pmid}")
        lines.append(f"JO  - {journal}")
        for k in kw.split(";"):
            lines.append(f"KW  - {k.strip()}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


def records_csv_rows(records) -> tuple[list[str], list[list[str]]]:
    fields = ["record_id", "source_database", "title", "abstract", "authors",
              "year", "doi", "pmid", "journal", "keywords", "raw_row_hash"]
    rows = []
    for rid, file_, _cat, title, abstract, authors, year, doi, pmid, journal, kw in records:
        rows.append([rid, SOURCE_NAME[file_], title, abstract, authors, year,
                     doi, pmid, journal, kw, compute_row_hash(title, abstract)])
    return fields, rows


def run_self_test() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    cats: dict[str, int] = {}
    for r in RECORDS:
        cats[r[2]] = cats.get(r[2], 0) + 1
    check(f"共 {len(RECORDS)} 条记录", len(RECORDS) == 20)
    check("类别配额符合设计", cats == EXPECTED_QUOTA, str(cats))
    ids = [r[0] for r in RECORDS]
    check("record_id 唯一且恰为 R001–R020（文件内顺序不必连续）",
          sorted(ids) == [f"R{i:03d}" for i in range(1, 21)])
    # 重复设计
    doi_of = {r[0]: r[7] for r in RECORDS}
    title_of = {r[0]: r[3] for r in RECORDS}
    check("R003 与 R001 同 DOI（精确去重对）",
          doi_of["R003"] == doi_of["R001"] == "10.1234/demo.001")
    check("R018 与 R001 同题同人同年且无 DOI（模糊去重对）",
          title_of["R018"] == title_of["R001"] and doi_of["R018"] == "")
    check("R018 标题哈希与 R001 不同（abstract 不同 → 不被哈希去重误覆盖）",
          compute_row_hash(title_of["R001"], dict((r[0], r[4]) for r in RECORDS)["R001"])
          != compute_row_hash(title_of["R018"], dict((r[0], r[4]) for r in RECORDS)["R018"]))
    # hbm 记录的标题都含 hbm 关键词、non_hbm 都不含
    hbm_kw = ("biomonitoring", "urinary", "urine", "blood", "serum")
    non_hbm_kw = ("dietary", "questionnaire", "thermal paper", "dermal", "dust", "air")
    review_kw = ("review", "meta-analysis", "overview")
    animal_kw = ("rats", "mouse", "mice", "animal")
    vitro_kw = ("cell line", "in vitro")
    ok_cat = True
    for r in RECORDS:
        rid, cat, title = r[0], r[2], r[3].lower()
        if cat == "hbm" and not any(k in title for k in hbm_kw):
            ok_cat = False
        if cat == "non_hbm" and (any(k in title for k in hbm_kw)
                                 or not any(k in title for k in non_hbm_kw)):
            ok_cat = False
        if cat == "review" and not any(k in title for k in review_kw):
            ok_cat = False
        if cat == "animal" and not any(k in title for k in animal_kw):
            ok_cat = False
        if cat == "in_vitro" and not any(k in title for k in vitro_kw):
            ok_cat = False
    check("每条记录标题含其类别的触发关键词（且互斥）", ok_cat)
    check("全部年份在 2005–2024 窗口内",
          all(2005 <= int(r[6]) <= 2024 for r in RECORDS))
    return 0 if all(ok for _, ok, _d in checks) else 1


def write_all() -> int:
    SEARCH.mkdir(parents=True, exist_ok=True)
    by_file: dict[str, list] = {}
    for r in RECORDS:
        by_file.setdefault(r[1], []).append(r)
    for file_key, fname in FILE_OF.items():
        (SEARCH / fname).write_text(ris_text(by_file[file_key]), encoding="utf-8")
        print(f"已生成 01-search/{fname}（{len(by_file[file_key])} 条）")
    fields, rows = records_csv_rows(RECORDS)
    with io.open(SEARCH / "raw_records_example.csv", "w", encoding="utf-8",
                 newline="") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        w.writerows(rows)
    print(f"已生成 01-search/raw_records_example.csv（{len(rows)} 条）")
    # 检索登记示例（人工在 GATE-1 维护；数字为演示用虚构值）
    with io.open(SEARCH / "source_summary.csv", "w", encoding="utf-8",
                 newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["database", "search_date", "search_string_summary",
                    "records_found", "records_exported", "format"])
        w.writerow(["PubMed", "2024-06-15", "BPA AND China AND urinary", 342, 342, "ris"])
        w.writerow(["Web of Science", "2024-06-15", "BPA AND China AND urinary",
                    287, 287, "ris"])
        w.writerow(["Scopus", "2024-06-15", "BPA AND China AND urinary", 391, 391, "ris"])
    print("已生成 01-search/source_summary.csv")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="make_demo_inputs.py")
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
