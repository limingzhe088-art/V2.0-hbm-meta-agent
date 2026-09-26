#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fulltext_screening.py — 全文层 AI 复筛（S2-C：AI 出证据与建议，最终裁决在人）

输入
  1. title_abstract_screening.csv —— 人工已逐条填写 human_decision
     （GATE-S2b 检查点：存在空值即拒绝执行，退出码 2）；只处理
     human_decision = proceed_to_fulltext 的行。
  2. --fulltext-dir —— 全文目录，文件按 record_id 命名：
     {record_id}.pdf / .html / .htm / .txt（大小写不敏感）。
     PDF 解析用 PyMuPDF（fitz，requirements.txt 中的条件必需依赖）；
     缺库且有 PDF 待处理 → 显式报错退出码 2（不静默标记 unavailable）。

判定逻辑（关键词与编码全部来自 project.yaml → screening）
  - confirmed_article_type：复用 classify_articles 的规则引擎，
    对 title + abstract + 全文重新分类
  - has_human_samples / has_usable_concentration：fulltext_rules 词表命中
  - analytes_reported：analyte_terms 命中列表（"; " 连接，空 = 未测目标物）
  - 年份核对：全文中出现的 (19|20)xx 年份与 period_scheme 覆盖区间比对
    （区间内无任何年份才判 wrong_year；全文无年份 → 留给人工，不猜）
  - ai_reason_code：排除原因**闭集**（screening.exclusion_reasons）
  - 决策序：类型该排除 → not_human_study → wrong_analyte → no_usable_values
    → wrong_year → include_candidate（会议摘要词命中 → conference_abstract）

输出
  fulltext_screening.csv
    record_id, fulltext_available, confirmed_article_type, has_human_samples,
    analytes_reported, has_usable_concentration, ai_screening_decision,
    ai_reason_code, human_final_decision(空), human_final_reason_code(空),
    meta_disposition(空，人的字段：include/exclude/background_only/
    citation_tracking_only/uncertain，闭集来自 screening.meta_analysis_disposition)
  被排除的记录不物理删除（GATE-S2c 后由 generate_inclusion_list 汇总）。

用法
  python fulltext_screening.py --input title_abstract_screening.csv \
      --records dedup_unique.csv --fulltext-dir fulltexts/ \
      --config project.yaml --output fulltext_screening.csv
  python fulltext_screening.py --self-test

退出码
  0 成功   2 前置条件不满足（GATE-S2b 未过 / 文件缺失 / fitz 缺失）
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from html.parser import HTMLParser
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
    from record_schema import RECORD_FIELDS, read_records_csv  # noqa: E402
    from screening_schema import (  # noqa: E402  ★ S2 单一来源
        AI_FT_DECISIONS, FT_FIELDS, HUMAN_TA_DECISIONS, ConfigError,
        load_screening_config, normalize_text, write_csv_dicts,
    )
    from classify_articles import classify_text  # noqa: E402  复用同一规则引擎
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 screening_schema / classify_articles（{_exc}）",
          file=sys.stderr)
    raise SystemExit(2)


_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
CONFERENCE_TERMS = ["conference abstract", "conference paper", "meeting abstract",
                    "会议摘要", "大会发言"]


class _TextHTMLParser(HTMLParser):
    """抽取 HTML 可见文本（跳过 script/style）。"""

    def __init__(self):
        super().__init__()
        self._skip = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data.strip())


def html_to_text(html_text: str) -> str:
    parser = _TextHTMLParser()
    try:
        parser.feed(html_text)
    except Exception:  # 防御：坏 HTML 不崩，取原始文本
        return html_text
    return "\n".join(parser.chunks)


def extract_pdf_text(path: Path) -> str:
    """PDF → 文本（PyMuPDF）。缺库 → ConfigError（显式拒绝，不静默降级）。"""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ConfigError(
            "处理 PDF 全文需要 PyMuPDF：请先 `pip install PyMuPDF`，"
            "或将全文改为 HTML/TXT 格式。（不做静默降级）") from exc
    doc = fitz.open(str(path))
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def load_fulltext(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf_text(path)
    try:
        raw = io.open(path, "r", encoding="utf-8-sig", errors="replace").read()
    except OSError as exc:
        raise ConfigError(f"无法读取 {path}：{exc}") from exc
    return html_to_text(raw) if ext in (".html", ".htm") else raw


def find_fulltext(fulltext_dir: Path, record_id: str):
    """按 record_id 找全文文件（扩展名优先级 pdf > html > htm > txt）。"""
    for ext in (".pdf", ".html", ".htm", ".txt"):
        p = fulltext_dir / f"{record_id}{ext}"
        if p.exists():
            return p
    for ext in (".pdf", ".html", ".htm", ".txt"):
        p = fulltext_dir / f"{record_id.upper()}{ext}"
        if p.exists():
            return p
    return None


def analyze_fulltext(title: str, abstract: str, text: str, spec: dict,
                     period_range: tuple[int, int]) -> dict:
    """对单条记录做全文判定 → FT_FIELDS 中 AI 列的取值。"""
    full = normalize_text(f"{title} {abstract} {text}").lower()
    confirmed = classify_text(title, f"{abstract} {text[:8000]}", spec)

    def has_any(terms: list[str]) -> bool:
        return any(term.lower() in full for term in terms)

    human_samples = has_any(spec["fulltext_rules"]["human_sample_terms"])
    analytes = "; ".join(t for t in spec["analyte_terms"] if t.lower() in full)
    usable = has_any(spec["fulltext_rules"]["concentration_stat_terms"])
    years = [int(m) for m in _YEAR_RE.findall(text)]
    year_in_range = any(period_range[0] <= y <= period_range[1] for y in years)
    conference = has_any(CONFERENCE_TERMS)

    row = {
        "fulltext_available": "TRUE",
        "confirmed_article_type": confirmed["type"],
        "has_human_samples": "TRUE" if human_samples else "FALSE",
        "analytes_reported": analytes,
        "has_usable_concentration": "TRUE" if usable else "FALSE",
        "ai_screening_decision": "",
        "ai_reason_code": "",
    }

    # 决策序（第一条命中即停；全部编码在 exclusion_reasons 闭集内）
    tdm = spec["type_decision_map"]
    if tdm.get(confirmed["type"]) == "suggest_exclude":
        reason = {"review": "review_or_comment", "policy_or_comment": "review_or_comment",
                  "animal_study": "not_human_study", "in_vitro_study": "not_human_study",
                  "methodology": "no_hbm_data"}.get(confirmed["type"],
                                                    "insufficient_info")
        row.update(ai_screening_decision="suggest_exclude", ai_reason_code=reason)
    elif conference:
        row.update(ai_screening_decision="suggest_exclude",
                   ai_reason_code="conference_abstract")
    elif not human_samples:
        row.update(ai_screening_decision="suggest_exclude",
                   ai_reason_code="not_human_study")
    elif not analytes:
        row.update(ai_screening_decision="suggest_exclude",
                   ai_reason_code="wrong_analyte")
    elif not usable:
        row.update(ai_screening_decision="suggest_exclude",
                   ai_reason_code="no_usable_values")
    elif years and not year_in_range:
        row.update(ai_screening_decision="suggest_exclude",
                   ai_reason_code="wrong_year")
    else:
        row.update(ai_screening_decision="include_candidate", ai_reason_code="")
    return row


def build_ft_rows(ta_rows: list[dict], records: list[dict], spec: dict,
                  period_range: tuple[int, int], fulltext_dir: Path) -> tuple[list[dict], dict]:
    """主流程 → (FT 行, 统计 dict)。GATE-S2b 检查点在此强制执行。"""
    pending = [r.get("record_id", "") for r in ta_rows
               if not normalize_text(r.get("human_decision", ""))]
    if pending:
        raise ConfigError(
            f"GATE-S2b 检查点未过：{len(pending)} 条记录的 human_decision 为空"
            f"（如 {pending[:3]}…）。请先完成人工逐条裁决再运行全文筛。")

    rec_by_id = {r.get("record_id", ""): r for r in records}
    rows: list[dict] = []
    stats = {"fulltext_assessed": 0, "fulltext_unavailable": 0,
             "ai_include_candidate": 0, "ai_suggest_exclude": 0, "ai_uncertain": 0}
    for ta in ta_rows:
        if normalize_text(ta.get("human_decision", "")) != "proceed_to_fulltext":
            continue
        rid = ta.get("record_id", "")
        meta = rec_by_id.get(rid)
        if meta is None:
            meta = {}
        fpath = find_fulltext(fulltext_dir, rid) if fulltext_dir.exists() else None
        if fpath is None:
            # 全文不可得 ≠ 不符合纳入标准：标 uncertain + 专用编码，
            # 交人工处置（PRISMA 单列 "reports not retrieved"）
            row = {
                "record_id": rid, "fulltext_available": "FALSE",
                "confirmed_article_type": "", "has_human_samples": "",
                "analytes_reported": "", "has_usable_concentration": "",
                "ai_screening_decision": "uncertain",
                "ai_reason_code": "fulltext_unavailable",
            }
            stats["fulltext_unavailable"] += 1
        else:
            text = load_fulltext(fpath)
            analyzed = analyze_fulltext(meta.get("title", ""), meta.get("abstract", ""),
                                        text, spec, period_range)
            row = {"record_id": rid, **analyzed}
            stats["fulltext_assessed"] += 1
        # meta_disposition 是人的字段（定量处理意见：include/exclude/
        # background_only/citation_tracking_only/uncertain，闭集来自
        # project.yaml → screening.meta_analysis_disposition）；AI 不代填
        row.update(human_final_decision="", human_final_reason_code="",
                   meta_disposition="")
        rows.append(row)
    stats["ai_include_candidate"] = sum(
        1 for r in rows if r["ai_screening_decision"] == "include_candidate")
    stats["ai_suggest_exclude"] = sum(
        1 for r in rows if r["ai_screening_decision"] == "suggest_exclude")
    stats["ai_uncertain"] = sum(
        1 for r in rows if r["ai_screening_decision"] == "uncertain")
    return rows, stats


def period_range_from_cfg(cfg) -> tuple[int, int]:
    bounds = cfg.period_scheme.get("boundaries") or []
    years = [b[key] for b in bounds for key in ("start", "end")]
    if not years:
        raise ConfigError("period_scheme.boundaries 为空，无法核对全文年份范围。")
    return min(years), max(years)


# ===========================================================================
# self-test
# ===========================================================================

FIXTURE_TA = [
    {"record_id": "pubmed-00001", "ai_article_type": "human_hbm_original",
     "ai_confidence": "high", "ai_screening_decision": "proceed_to_fulltext",
     "ai_reason_code": "hbm_title", "ai_evidence_quote": "title_any:urinary",
     "human_decision": "proceed_to_fulltext", "human_reason_code": ""},
    {"record_id": "wos-00001", "ai_article_type": "review",
     "ai_confidence": "high", "ai_screening_decision": "suggest_exclude",
     "ai_reason_code": "review_title", "ai_evidence_quote": "title_any:review",
     "human_decision": "proceed_to_fulltext", "human_reason_code": ""},
    {"record_id": "scopus-00001", "ai_article_type": "other",
     "ai_confidence": "low", "ai_screening_decision": "uncertain",
     "ai_reason_code": "no_rule_matched", "ai_evidence_quote": "",
     "human_decision": "", "human_reason_code": ""},
]

FIXTURE_RECORDS = [
    {"record_id": "pubmed-00001", "source_database": "PubMed",
     "title": "Urinary arsenic in general population", "abstract": "",
     "authors": "Zhang, San", "year": "2020", "doi": "10.1/a", "pmid": "1",
     "journal": "J", "keywords": "", "raw_row_hash": ""},
    {"record_id": "wos-00001", "source_database": "WebOfScience",
     "title": "Systematic review of arsenic methods", "abstract": "",
     "authors": "Li, Si", "year": "2021", "doi": "10.1/b", "pmid": "",
     "journal": "J", "keywords": "", "raw_row_hash": ""},
]

TXT_GOOD = """Methods: We measured urinary arsenic concentrations in 1200 adults
and children recruited in 2015. Results: geometric mean 35.2 ug/L (95% CI 30-41).
Participants were general residents without occupational exposure."""

TXT_ANIMAL = """We measured arsenic concentrations in rats exposed via drinking
water. Geometric mean 3.1 ug/L in 2015."""

TXT_NO_VALUE = """We recruited adult participants and measured urinary arsenic
by ICP-MS in 2015. Detailed results are shown in the supplementary tables."""

TXT_WRONG_YEAR = """Participants: 200 adults. Urinary arsenic geometric mean
12 ug/L. Sampling was performed in 1974 and repeated in 1976."""


def _write_fulltexts(tmp: Path) -> None:
    (tmp / "fulltexts").mkdir(exist_ok=True)
    ft = tmp / "fulltexts"
    (ft / "pubmed-00001.txt").write_text(TXT_GOOD, encoding="utf-8")
    (ft / "wos-00001.txt").write_text(TXT_ANIMAL, encoding="utf-8")
    (ft / "scopus-00001.txt").write_text(TXT_NO_VALUE, encoding="utf-8")


def run_self_test() -> int:
    print("=" * 70)
    print("fulltext_screening.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    def expect_config_error(name, fn, needle=""):
        try:
            fn()
            check(name, False, "未抛出！")
        except ConfigError as e:
            check(name, True, (str(e)[:44] + "…") if needle in str(e) else str(e)[:44])

    from screening_schema import load_fixture_spec
    spec = load_fixture_spec()
    tmp_parent = Path(__import__("tempfile").mkdtemp(prefix="hbm_ft_"))
    _write_fulltexts(tmp_parent)
    ft_dir = tmp_parent / "fulltexts"

    print("\n[1] 全文判定（有全文）")
    r_good = analyze_fulltext("Urinary arsenic in general population", "",
                              TXT_GOOD, spec, (1980, 2024))
    check("人体样本命中", r_good["has_human_samples"] == "TRUE")
    check("分析物命中并留痕", "arsenic" in r_good["analytes_reported"],
          r_good["analytes_reported"])
    check("浓度统计量命中", r_good["has_usable_concentration"] == "TRUE")
    check("候选纳入", r_good["ai_screening_decision"] == "include_candidate")
    check("年份在 period 覆盖区间（2015 ∈ [1980,2024]）",
          r_good["ai_reason_code"] == "")

    print("\n[2] 各排除编码（决策序首中即停）")
    r_animal = analyze_fulltext("Arsenic in rats", "", TXT_ANIMAL, spec, (1980, 2024))
    check("动物全文 → not_human_study（类型判定优先）",
          r_animal["ai_screening_decision"] == "suggest_exclude"
          and r_animal["ai_reason_code"] == "not_human_study",
          f"{r_animal['confirmed_article_type']}/{r_animal['ai_reason_code']}")
    r_noval = analyze_fulltext("Study with participants", "", TXT_NO_VALUE,
                               spec, (1980, 2024))
    check("有人群有分析物但无可用统计量 → no_usable_values",
          r_noval["ai_reason_code"] == "no_usable_values", r_noval["ai_reason_code"])
    r_year = analyze_fulltext("Old cohort", "", TXT_WRONG_YEAR, spec, (1980, 2024))
    check("年份全在区间外 → wrong_year", r_year["ai_reason_code"] == "wrong_year",
          r_year["ai_reason_code"])
    check("排除编码在闭集内",
          all(r["ai_reason_code"] in spec["exclusion_reasons"]
              for r in (r_animal, r_noval, r_year)))
    check("决策值全部在 AI 全文闭集内",
          all(r["ai_screening_decision"] in AI_FT_DECISIONS
              for r in (r_good, r_animal, r_noval, r_year)))

    print("\n[3] 无年份全文不猜（wrong_year 只在'有年份且全在区间外'时判）")
    r_noyear = analyze_fulltext("Some study", "", "Participants provided urine "
                                "samples. Geometric mean reported.", spec, (1980, 2024))
    check("无年份 → 不判 wrong_year（进 include/uncertain 由人裁决）",
          r_noyear["ai_reason_code"] != "wrong_year", r_noyear["ai_reason_code"])

    print("\n[4] 批处理：GATE-S2b 检查点 / unavailable / human 列留空")
    expect_config_error("human_decision 有空值 → 拒绝执行",
                        lambda: build_ft_rows([dict(r) for r in FIXTURE_TA],
                                              FIXTURE_RECORDS, spec, (1980, 2024),
                                              ft_dir),
                        "GATE-S2b")
    ta_ok = [dict(r) for r in FIXTURE_TA]
    for r in ta_ok:
        r["human_decision"] = "proceed_to_fulltext"
    ta_ok[2]["human_decision"] = "exclude"   # 人工排除行：不进入全文筛
    rows, stats = build_ft_rows(ta_ok, [dict(r) for r in FIXTURE_RECORDS],
                                spec, (1980, 2024), ft_dir)
    check("只处理 human_decision=proceed_to_fulltext 行（2 条）",
          len(rows) == 2 and all(r["record_id"] != "scopus-00001" for r in rows),
          f"{len(rows)}")
    check("统计：评估 2 / AI 候选 1 / 建议排除 1",
          stats["fulltext_assessed"] == 2 and stats["ai_include_candidate"] == 1
          and stats["ai_suggest_exclude"] == 1, str(stats))
    check("human_final_decision / reason_code 初始为空",
          all(r["human_final_decision"] == "" and r["human_final_reason_code"] == ""
              for r in rows))
    check("FT 输出字段与 FT_FIELDS 一致", list(rows[0].keys()) == FT_FIELDS)

    empty_dir = tmp_parent / "nope"
    rows2, stats2 = build_ft_rows(ta_ok, [dict(r) for r in FIXTURE_RECORDS],
                                  spec, (1980, 2024), empty_dir)
    check("全文目录不存在 → 全部 unavailable + uncertain（专用编码）",
          all(r["fulltext_available"] == "FALSE"
              and r["ai_screening_decision"] == "uncertain"
              and r["ai_reason_code"] == "fulltext_unavailable" for r in rows2))
    check("unavailable 计数正确", stats2["fulltext_unavailable"] == 2)
    check("meta_disposition 列存在且 AI 留空（人的字段）",
          all(r["meta_disposition"] == "" for r in rows + rows2))

    print("\n[5] HTML 解析与 conference 词")
    html = ("<html><script>var x=1;</script><body><p>Participants: adults.</p>"
            "<p>Urinary arsenic geometric mean 12 ug/L in 2015.</p>"
            "<p>Conference abstract.</p></body></html>")
    text = html_to_text(html)
    check("script 内容被剔除", "var x" not in text)
    r_conf = analyze_fulltext("T", "", text, spec, (1980, 2024))
    check("conference_abstract 编码命中",
          r_conf["ai_reason_code"] == "conference_abstract",
          r_conf["ai_reason_code"])

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


# ===========================================================================
# CLI
# ===========================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="fulltext_screening.py",
        description="全文层 AI 复筛（S2-C）→ fulltext_screening.csv")
    ap.add_argument("--input", type=Path, help="title_abstract_screening.csv")
    ap.add_argument("--records", type=Path, help="dedup_unique.csv（题录元数据）")
    ap.add_argument("--fulltext-dir", type=Path, help="全文目录（{record_id}.pdf/.html/.txt）")
    ap.add_argument("--config", type=Path, help="project.yaml")
    ap.add_argument("--output", type=Path, help="输出 fulltext_screening.csv")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    missing = [a for a, v in (("--input", args.input), ("--records", args.records),
                              ("--fulltext-dir", args.fulltext_dir),
                              ("--config", args.config),
                              ("--output", args.output)) if not v]
    if missing:
        print(f"错误：缺少必需参数 {' '.join(missing)}（--self-test 可单独运行）",
              file=sys.stderr)
        return 2
    for p in (args.input, args.records):
        if not p.exists():
            print(f"错误：输入文件不存在：{p}", file=sys.stderr)
            return 2
    if not args.fulltext_dir.exists():
        print(f"提示：全文目录不存在：{args.fulltext_dir}（所有记录将标记 unavailable）",
              file=sys.stderr)

    try:
        ta_rows = _read_ta(args.input)
        records = read_records_csv(args.records)
        cfg, spec = load_screening_config(args.config)
        prange = period_range_from_cfg(cfg)
        rows, stats = build_ft_rows(ta_rows, records, spec, prange,
                                    args.fulltext_dir)
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # 文件级错误统一归入退出码 2
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    write_csv_dicts(args.output, FT_FIELDS, rows)
    print(f"全文筛完成：评估 {stats['fulltext_assessed']} / "
          f"全文不可得 {stats['fulltext_unavailable']} → {args.output}")
    print(f"  AI 建议：候选纳入 {stats['ai_include_candidate']} / "
          f"建议排除 {stats['ai_suggest_exclude']} / 不确定 {stats['ai_uncertain']}")
    print("  ⚠️ AI 只产建议：最终纳入/排除由人填写 human_final_decision（GATE-S2c）；"
          "被排除记录不删除。")
    return 0


def _read_ta(path: Path) -> list[dict]:
    import csv as _csv
    import io as _io
    with _io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = _csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        if header != TA_FIELDS:
            raise ConfigError(
                f"{path} 表头与 title_abstract_screening 格式不符；"
                "本脚本只读 classify_articles.py 的输出。")
        return [dict(r) for r in reader]


if __name__ == "__main__":
    raise SystemExit(main())
