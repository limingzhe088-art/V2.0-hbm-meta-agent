#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
classify_articles.py — 题目+摘要 AI 初筛（S2-B：AI 分类与建议，人不缺席）

输入
  dedup_records.py 产出的 dedup_unique.csv（RECORD_FIELDS 同构）。

规则引擎（词表与规则**全部**来自 project.yaml → screening；缺失即硬错误）
  - classification_rules：有序规则列表，自上而下第一条命中即判；
    每条规则 = {id, type, title_any, abstract_any, require_analyte}
  - require_analyte=true 时还需 analyte_terms 之一出现在 title+abstract 中
  - 未命中任何规则：文本长度 < min_text_chars → insufficient_info；否则 → other
  - ai_screening_decision 由 type_decision_map 显式映射（必须全覆盖）
  - 置信度：命中 title_any → high；仅命中 abstract_any → medium；退路 → low

边界（★ 铁律）
  - AI 只产分类与建议（proceed_to_fulltext / suggest_exclude / uncertain），
    **不写最终纳入/排除** —— human_decision / human_reason_code 初始为空，
    由人工逐条填写（GATE-S2b 的脚本检查点会拒绝空值）。
  - 关键词匹配是启发式，不是理解；规则误判由人工复核兜底。

输出
  title_abstract_screening.csv
    record_id, ai_article_type, ai_confidence, ai_screening_decision,
    ai_reason_code, ai_evidence_quote, human_decision(空), human_reason_code(空)

用法
  python classify_articles.py --input dedup_unique.csv --config project.yaml \
      --output title_abstract_screening.csv
  python classify_articles.py --self-test

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
    from record_schema import RECORD_FIELDS, SchemaError, read_records_csv  # noqa: E402
    from screening_schema import (  # noqa: E402  ★ S2 单一来源
        AI_CONFIDENCE, AI_TA_DECISIONS, TA_FIELDS, ConfigError,
        load_screening_config, normalize_text, write_csv_dicts,
    )
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/record_schema.py 或 screening_schema.py（{_exc}）",
          file=sys.stderr)
    raise SystemExit(2)


# ===========================================================================
# 1. 规则引擎（供本脚本与 fulltext_screening / gen_templates 复用）
# ===========================================================================

def classify_text(title: str, abstract: str, spec: dict) -> dict:
    """
    对一段 title+abstract 文本跑规则引擎 →
      {type, confidence, decision, reason_code, evidence}
    确定性：同输入两次调用结果完全一致（self-test 断言）。
    """
    t = normalize_text(title).lower()
    a = normalize_text(abstract).lower()
    whole = f"{t} {a}".strip()
    analyte_hit = any(term.lower() in whole for term in spec["analyte_terms"])

    for rule in spec["classification_rules"]:
        for field, text, conf in (("title_any", t, "high"),
                                  ("abstract_any", a, "medium")):
            for kw in rule[field]:
                if kw.lower() in text:
                    if rule["require_analyte"] and not analyte_hit:
                        continue
                    return {
                        "type": rule["type"],
                        "confidence": conf,
                        "decision": spec["type_decision_map"][rule["type"]],
                        "reason_code": rule["id"],
                        "evidence": f"{field}:{kw}",
                    }
    # 未命中任何规则 → 退路（不猜类型）
    if len(whole) < spec["min_text_chars"]:
        etype = "insufficient_info"
        reason = "insufficient_text"
    else:
        etype = "other"
        reason = "no_rule_matched"
    return {
        "type": etype,
        "confidence": "low",
        "decision": spec["type_decision_map"][etype],
        "reason_code": reason,
        "evidence": "",
    }


def classify_records(rows: list[dict], spec: dict) -> list[dict]:
    """批量分类 → title_abstract_screening 行（human 列留空）。"""
    out: list[dict] = []
    for row in rows:
        verdict = classify_text(row.get("title", ""), row.get("abstract", ""), spec)
        out.append({
            "record_id": row.get("record_id", ""),
            "ai_article_type": verdict["type"],
            "ai_confidence": verdict["confidence"],
            "ai_screening_decision": verdict["decision"],
            "ai_reason_code": verdict["reason_code"],
            "ai_evidence_quote": verdict["evidence"],
            "human_decision": "",
            "human_reason_code": "",
        })
    return out


# ===========================================================================
# 2. self-test
# ===========================================================================

FIXTURE_ROWS = [
    {"record_id": "pubmed-00001", "source_database": "PubMed",
     "title": "Urinary arsenic in general population of China",
     "abstract": "A national biomonitoring survey reporting geometric mean "
                 "urinary concentrations.", "authors": "Zhang, San", "year": "2020",
     "doi": "10.1234/a", "pmid": "1", "journal": "J", "keywords": "", "raw_row_hash": ""},
    {"record_id": "wos-00001", "source_database": "WebOfScience",
     "title": "Systematic review of arsenic exposure assessment methods",
     "abstract": "", "authors": "Li, Si", "year": "2021",
     "doi": "10.1234/b", "pmid": "", "journal": "J", "keywords": "", "raw_row_hash": ""},
    {"record_id": "scopus-00001", "source_database": "Scopus",
     "title": "Effects of arsenic in mice",
     "abstract": "Animal experiment.", "authors": "Wang, Wu", "year": "2019",
     "doi": "", "pmid": "", "journal": "J", "keywords": "", "raw_row_hash": ""},
    {"record_id": "embase-00001", "source_database": "Embase",
     "title": "In vitro effects on HepG2 cells",
     "abstract": "", "authors": "Zhao, Liu", "year": "2018",
     "doi": "", "pmid": "", "journal": "J", "keywords": "", "raw_row_hash": ""},
    {"record_id": "cnki-00001", "source_database": "CNKI",
     "title": "某省居民尿砷调查", "abstract": "对居民砷内暴露水平的描述性研究。",
     "authors": "张三", "year": "2017",
     "doi": "", "pmid": "", "journal": "中华杂志", "keywords": "", "raw_row_hash": ""},
    {"record_id": "wanfang-00001", "source_database": "Wanfang",
     "title": "Short note", "abstract": "",
     "authors": "Sun, Qi", "year": "2016",
     "doi": "", "pmid": "", "journal": "J", "keywords": "", "raw_row_hash": ""},
]


def run_self_test() -> int:
    print("=" * 70)
    print("classify_articles.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    from screening_schema import load_fixture_spec
    spec = load_fixture_spec()
    rows = classify_records([dict(r) for r in FIXTURE_ROWS], spec)
    by = {r["record_id"]: r for r in rows}

    print("\n[1] 分类正确性（规则按序首中）")
    check("HBM 题名规则（require_analyte 满足）→ human_hbm_original/high",
          by["pubmed-00001"]["ai_article_type"] == "human_hbm_original"
          and by["pubmed-00001"]["ai_confidence"] == "high")
    check("HBM 决策映射为 proceed_to_fulltext",
          by["pubmed-00001"]["ai_screening_decision"] == "proceed_to_fulltext")
    check("review 规则先于 HBM 规则（标题含 review）",
          by["wos-00001"]["ai_article_type"] == "review"
          and by["wos-00001"]["ai_screening_decision"] == "suggest_exclude")
    check("动物规则 → animal_study/suggest_exclude",
          by["scopus-00001"]["ai_article_type"] == "animal_study")
    check("体外规则 → in_vitro_study", by["embase-00001"]["ai_article_type"] == "in_vitro_study")
    check("中文规则命中（某省居民尿砷调查）",
          by["cnki-00001"]["ai_article_type"] == "human_hbm_original",
          by["cnki-00001"]["ai_reason_code"])
    check("短文本 → insufficient_info/uncertain",
          by["wanfang-00001"]["ai_article_type"] == "insufficient_info"
          and by["wanfang-00001"]["ai_screening_decision"] == "uncertain")

    print("\n[2] 证据与理由可审计")
    check("reason_code 是规则 id", by["wos-00001"]["ai_reason_code"] == "review_title")
    check("evidence 含字段与关键词",
          by["wos-00001"]["ai_evidence_quote"].startswith("title_any:"),
          by["wos-00001"]["ai_evidence_quote"])
    check("退路 reason_code 无词表依赖",
          by["wanfang-00001"]["ai_reason_code"] == "insufficient_text")

    print("\n[3] require_analyte 抑制（无分析物词的泛题名）")
    verdict = classify_text(
        "Urinary biomonitoring survey conducted among residents of industrial cities",
        "", spec)
    check("泛 biomonitoring 标题无分析物 → 不判 HBM（落到 other）",
          verdict["type"] == "other", f"{verdict['type']}/{verdict['reason_code']}")
    verdict2 = classify_text("Urinary biomonitoring survey of arsenic", "", spec)
    check("补充分析物后命中 HBM", verdict2["type"] == "human_hbm_original")

    print("\n[4] 决策闭集与人工列")
    check("AI 决策全部在闭集内",
          {r["ai_screening_decision"] for r in rows} <= set(AI_TA_DECISIONS))
    check("置信度全部在闭集内",
          {r["ai_confidence"] for r in rows} <= set(AI_CONFIDENCE))
    check("文章类型全部在配置闭集内",
          {r["ai_article_type"] for r in rows} <= set(spec["article_types"]))
    check("human_decision / human_reason_code 初始为空（GATE-S2b 前置）",
          all(r["human_decision"] == "" and r["human_reason_code"] == "" for r in rows))
    check("输出字段与 TA_FIELDS 一致",
          list(rows[0].keys()) == TA_FIELDS)

    print("\n[5] 确定性")
    rows2 = classify_records([dict(r) for r in FIXTURE_ROWS], spec)
    check("同输入两次分类完全一致", rows2 == rows)

    print("\n[6] 规则顺序敏感性（abstract 命中置信度 medium）")
    v3 = classify_text("Untitled study", "We performed a systematic review of arsenic.",
                       spec)
    check("仅摘要命中 → medium", v3["confidence"] == "medium", v3["reason_code"])

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
        prog="classify_articles.py",
        description="题目+摘要 AI 初筛（S2-B）→ title_abstract_screening.csv")
    ap.add_argument("--input", type=Path, help="dedup_unique.csv")
    ap.add_argument("--config", type=Path, help="project.yaml（读 screening 区块）")
    ap.add_argument("--output", type=Path, help="输出 title_abstract_screening.csv")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    missing = [a for a, v in (("--input", args.input), ("--config", args.config),
                              ("--output", args.output)) if not v]
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

    ta_rows = classify_records(rows, spec)
    write_csv_dicts(args.output, TA_FIELDS, ta_rows)
    n_decide = sum(1 for r in ta_rows if r["ai_screening_decision"] != "uncertain")
    print(f"已分类 {len(ta_rows)} 条 → {args.output}")
    print(f"  AI 建议：进全文 "
          f"{sum(1 for r in ta_rows if r['ai_screening_decision'] == 'proceed_to_fulltext')}"
          f" / 建议排除 "
          f"{sum(1 for r in ta_rows if r['ai_screening_decision'] == 'suggest_exclude')}"
          f" / 不确定 "
          f"{sum(1 for r in ta_rows if r['ai_screening_decision'] == 'uncertain')}")
    print("  ⚠️ AI 只产建议：请人工逐条填写 human_decision（GATE-S2b 检查点要求无空值）；"
          f"{n_decide} 条为高/中置信建议，不确定条目必须人工判断。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
