#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screening_summary.py — 题摘筛选汇总（S2-B 输出统计 + PRISMA 中间数据）

输入
  classify_articles.py 产出、并经人工填写 human_decision 的
  title_abstract_screening.csv（GATE-S2b 之前运行会得到"人工未决"提示，
  但不阻断 —— 本脚本本身是只读统计）。

输出
  screening_summary.csv（scope,category,count 长表）：
    scope=total          —— 记录总数
    scope=article_type   —— 9 类文章类型计数（含 0，闭集全展开）
    scope=ta_ai_decision —— AI 三种建议计数（含 0）
    scope=ta_human_decision —— 人工决策计数 + pending（空值）计数
       （pending > 0 = GATE-S2b 检查点未过：human_decision 存在空值）

用法
  python screening_summary.py --input title_abstract_screening.csv \
      --config project.yaml --output screening_summary.csv
  python screening_summary.py --self-test

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
    from screening_schema import (  # noqa: E402  ★ S2 单一来源
        AI_TA_DECISIONS, HUMAN_TA_DECISIONS, SUMMARY_FIELDS, TA_FIELDS,
        ConfigError, load_screening_config, normalize_text, write_csv_dicts,
    )
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 screening_schema.py（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


def build_summary(rows: list[dict], spec: dict) -> list[dict]:
    """构造 scope,category,count 长表（闭集全展开，0 计数也列出）。"""
    out: list[dict] = [{"scope": "total", "category": "records",
                        "count": str(len(rows))}]

    by_type: dict[str, int] = {t: 0 for t in spec["article_types"]}
    by_ai: dict[str, int] = {d: 0 for d in AI_TA_DECISIONS}
    by_human: dict[str, int] = {d: 0 for d in HUMAN_TA_DECISIONS}
    human_pending = 0
    for r in rows:
        at = normalize_text(r.get("ai_article_type", ""))
        if at in by_type:
            by_type[at] += 1
        ad = normalize_text(r.get("ai_screening_decision", ""))
        if ad in by_ai:
            by_ai[ad] += 1
        hd = normalize_text(r.get("human_decision", ""))
        if not hd or hd not in by_human:
            # 空值或越界值都计入 pending（越界 = 需要人工重新处理，不静默丢弃）
            human_pending += 1
        else:
            by_human[hd] += 1

    for t in spec["article_types"]:
        out.append({"scope": "article_type", "category": t, "count": str(by_type[t])})
    for d in AI_TA_DECISIONS:
        out.append({"scope": "ta_ai_decision", "category": d, "count": str(by_ai[d])})
    for d in HUMAN_TA_DECISIONS:
        out.append({"scope": "ta_human_decision", "category": d,
                    "count": str(by_human[d])})
    out.append({"scope": "ta_human_decision", "category": "pending",
                "count": str(human_pending)})
    return out


def run_self_test() -> int:
    print("=" * 70)
    print("screening_summary.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    from screening_schema import load_fixture_spec
    spec = load_fixture_spec()
    fixture_rows = [
        {"record_id": "a", "ai_article_type": "human_hbm_original",
         "ai_screening_decision": "proceed_to_fulltext", "human_decision": "proceed_to_fulltext"},
        {"record_id": "b", "ai_article_type": "review",
         "ai_screening_decision": "suggest_exclude", "human_decision": "exclude"},
        {"record_id": "c", "ai_article_type": "review",
         "ai_screening_decision": "suggest_exclude", "human_decision": ""},
        {"record_id": "d", "ai_article_type": "insufficient_info",
         "ai_screening_decision": "uncertain", "human_decision": ""},
    ]
    summary = build_summary([dict(r) for r in fixture_rows], spec)
    by = {(s["scope"], s["category"]): s["count"] for s in summary}

    print("\n[1] 计数正确性")
    check("总数 = 4", by[("total", "records")] == "4")
    check("review = 2", by[("article_type", "review")] == "2")
    check("闭集全展开（9 类型都在，未出现的为 0）",
          len([s for s in summary if s["scope"] == "article_type"]) == 9
          and by[("article_type", "animal_study")] == "0")
    check("AI 决策计数", by[("ta_ai_decision", "proceed_to_fulltext")] == "1"
          and by[("ta_ai_decision", "suggest_exclude")] == "2"
          and by[("ta_ai_decision", "uncertain")] == "1")
    check("人工决策计数", by[("ta_human_decision", "proceed_to_fulltext")] == "1"
          and by[("ta_human_decision", "exclude")] == "1")
    check("pending = 2（human_decision 空值计数）",
          by[("ta_human_decision", "pending")] == "2")

    print("\n[2] 结构与闭集")
    check("列 = scope,category,count", list(summary[0].keys()) == SUMMARY_FIELDS)
    check("空输入 → 全 0 长表", all(
        s["count"] == "0" for s in build_summary([], spec)
        if s["scope"] != "total") and build_summary([], spec)[0]["count"] == "0")

    print("\n[3] 确定性与边界")
    check("同输入两次一致",
          build_summary([dict(r) for r in fixture_rows], spec) == summary)
    weird = build_summary([{"record_id": "x", "ai_article_type": "not_in_closed_set",
                            "ai_screening_decision": "bogus", "human_decision": "?"}],
                          spec)
    byw = {(s["scope"], s["category"]): s["count"] for s in weird}
    check("越界类型不产生计数行（闭集全展开不含未声明值）",
          ("article_type", "not_in_closed_set") not in byw
          and byw[("ta_human_decision", "pending")] == "1")
    check("human 越界值不计入枚举档位",
          all(byw[("ta_human_decision", d)] == "0" for d in HUMAN_TA_DECISIONS))

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
        prog="screening_summary.py",
        description="题摘筛选汇总（S2-B 统计 + PRISMA 中间数据）")
    ap.add_argument("--input", type=Path, help="title_abstract_screening.csv")
    ap.add_argument("--config", type=Path, help="project.yaml")
    ap.add_argument("--output", type=Path, help="输出 screening_summary.csv")
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

    import csv as _csv
    import io as _io
    try:
        with _io.open(args.input, "r", encoding="utf-8-sig", newline="") as fh:
            reader = _csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            if header != TA_FIELDS:
                print(f"错误：{args.input} 表头与 title_abstract_screening 格式不符"
                      f"（{header[:3]}…）。本脚本只读 classify_articles.py 的输出。",
                      file=sys.stderr)
                return 2
            rows = [dict(r) for r in reader]
    except OSError as exc:
        print(f"错误：无法读取 {args.input}：{exc}", file=sys.stderr)
        return 2

    try:
        _, spec = load_screening_config(args.config)
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    summary = build_summary(rows, spec)
    write_csv_dicts(args.output, SUMMARY_FIELDS, summary)
    pending = int(next(s["count"] for s in summary
                       if s["scope"] == "ta_human_decision" and s["category"] == "pending"))
    print(f"汇总已写入 → {args.output}")
    if pending:
        print(f"  ⚠️ {pending} 条记录 human_decision 为空 —— "
              "GATE-S2b 检查点未过：请先完成人工逐条裁决。")
    else:
        print("  ✅ human_decision 无空值（满足 GATE-S2b 检查点之一）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
