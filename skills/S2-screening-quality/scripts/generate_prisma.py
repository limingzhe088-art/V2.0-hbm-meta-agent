#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_prisma.py — 从 screening_log.csv 生成 PRISMA 2020 流程图（Mermaid + 表格）

输入
  generate_inclusion_list.py 产出的 screening_log.csv（metric,value 长表）。

数字自洽校验（★ 防止"编不出账"的流程图进入稿件）
  identification:  records_identified_total = records_after_dedup + duplicates_removed
  screening:       ta_screened = ta_excluded + ta_uncertain_unresolved + ta_proceeded_fulltext
  retrieval:       fulltext_assessed = fulltext_retrieved + fulltext_unavailable
  included:        fulltext_retrieved = fulltext_excluded + fulltext_included
  任一不平衡 → 退出码 1 并列出差异（流程图必须能对上账）。

输出
  prisma_flow.md —— Mermaid flowchart 代码块 + 数字核对表

用法
  python generate_prisma.py --input screening_log.csv --output prisma_flow.md
  python generate_prisma.py --self-test

退出码
  0 成功   1 数字不平衡   2 前置条件不满足
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

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_ROOT / "shared") not in sys.path:
    sys.path.insert(0, str(_ROOT / "shared"))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from screening_schema import LOG_FIELDS, ConfigError, read_csv_dicts  # noqa: E402
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 screening_schema.py（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


REQUIRED_METRICS = [
    "records_identified_total", "records_after_dedup", "duplicates_removed",
    "ta_screened", "ta_excluded", "ta_uncertain_unresolved",
    "ta_proceeded_fulltext", "fulltext_assessed", "fulltext_unavailable",
    "fulltext_retrieved", "fulltext_excluded", "fulltext_included",
]

BALANCE_RULES = [
    ("identification", "records_identified_total",
     ["records_after_dedup", "duplicates_removed"]),
    ("screening", "ta_screened",
     ["ta_excluded", "ta_uncertain_unresolved", "ta_proceeded_fulltext"]),
    ("retrieval", "fulltext_assessed",
     ["fulltext_retrieved", "fulltext_unavailable"]),
    ("included", "fulltext_retrieved",
     ["fulltext_excluded", "fulltext_included"]),
]


def load_log(path: Path) -> dict[str, int]:
    rows = read_csv_dicts(path)
    if rows and set(LOG_FIELDS) - set(rows[0].keys()):
        raise ConfigError(f"{path} 不是 screening_log.csv（列应为 {LOG_FIELDS}）。")
    log: dict[str, int] = {}
    for r in rows:
        metric = r.get("metric", "").strip()
        value = r.get("value", "").strip()
        if not metric:
            continue
        if not value.lstrip("-").isdigit():
            raise ConfigError(f"{path}：metric `{metric}` 的值 `{value}` 不是整数。")
        log[metric] = int(value)
    missing = [m for m in REQUIRED_METRICS if m not in log]
    if missing:
        raise ConfigError(f"{path} 缺少必需指标：{missing}。"
                          "请用 generate_inclusion_list.py 生成完整日志。")
    return log


def check_balance(log: dict[str, int]) -> list[str]:
    """返回不平衡差异清单（空 = 通过）。"""
    issues: list[str] = []
    for stage, total_key, parts in BALANCE_RULES:
        total = log[total_key]
        part_sum = sum(log[p] for p in parts)
        if total != part_sum:
            issues.append(
                f"{stage}：{total_key}={total} ≠ {' + '.join(parts)}"
                f" = {' + '.join(str(log[p]) for p in parts)} = {part_sum}")
    return issues


def render_prisma(log: dict[str, int], project_summary: str = "") -> str:
    """Mermaid flowchart + 数字核对表。确定性输出（不含时间戳）。"""
    lines: list[str] = []
    lines.append("# PRISMA 2020 流程图（Identification → Screening → Included）")
    lines.append("")
    if project_summary:
        lines.append(f"- 项目：{project_summary}")
        lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart TD")
    lines.append(f'    A["检索识别的记录<br/>n = {log["records_identified_total"]}"]')
    lines.append(f'    B["去重前记录<br/>n = {log["records_identified_total"]}"]')
    lines.append(f'    C["去重后记录<br/>n = {log["records_after_dedup"]}"]')
    lines.append(f'    D["重复记录被移除<br/>n = {log["duplicates_removed"]}"]')
    lines.append(f'    E["题目+摘要人工筛选<br/>n = {log["ta_screened"]}"]')
    lines.append(f'    F["题摘阶段排除<br/>n = {log["ta_excluded"]}"]')
    lines.append(f'    G["题摘阶段未决/不确定<br/>n = {log["ta_uncertain_unresolved"]}"]')
    lines.append(f'    H["进入全文获取<br/>n = {log["ta_proceeded_fulltext"]}"]')
    lines.append(f'    I["全文评估<br/>n = {log["fulltext_assessed"]}"]')
    lines.append(f'    J["全文未获取<br/>n = {log["fulltext_unavailable"]}"]')
    lines.append(f'    K["全文人工复筛<br/>n = {log["fulltext_retrieved"]}"]')
    lines.append(f'    L["全文排除（见排除原因分项）<br/>n = {log["fulltext_excluded"]}"]')
    lines.append(f'    M["最终纳入<br/>n = {log["fulltext_included"]}"]')
    lines.append("    A --> B")
    lines.append("    B --> C")
    lines.append("    B --> D")
    lines.append("    C --> E")
    lines.append("    E --> F")
    lines.append("    E --> G")
    lines.append("    E --> H")
    lines.append("    H --> I")
    lines.append("    I --> J")
    lines.append("    I --> K")
    lines.append("    K --> L")
    lines.append("    K --> M")
    lines.append("```")
    lines.append("")
    lines.append("## 数字核对表")
    lines.append("")
    lines.append("| 阶段 | 恒等式 | 数值 |")
    lines.append("|---|---|---|")
    for stage, total_key, parts in BALANCE_RULES:
        lines.append(f"| {stage} | {total_key} = {' + '.join(parts)} | "
                     f"{log[total_key]} = {' + '.join(str(log[p]) for p in parts)} |")
    lines.append("")
    lines.append("## 各指标数值")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    for metric in REQUIRED_METRICS:
        lines.append(f"| {metric} | {log[metric]} |")
    lines.append("")
    lines.append("> 被排除的记录不物理删除（见 excluded_studies.csv）；"
                 "GATE-S2c 由人工确认后，纳入清单方可进入 S3。")
    return "\n".join(lines)


# ===========================================================================
# self-test
# ===========================================================================

def _fixture_log() -> dict[str, int]:
    return {
        "records_identified_total": 10, "records_after_dedup": 8,
        "duplicates_removed": 2, "ta_screened": 8, "ta_excluded": 4,
        "ta_uncertain_unresolved": 2, "ta_proceeded_fulltext": 2,
        "fulltext_assessed": 2, "fulltext_unavailable": 1,
        "fulltext_retrieved": 1, "fulltext_excluded": 0, "fulltext_included": 1,
    }


def run_self_test() -> int:
    print("=" * 70)
    print("generate_prisma.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="hbm_prisma_"))

    print("\n[1] 日志加载")
    log = _fixture_log()
    p = tmp / "screening_log.csv"
    write_log(p, log)
    loaded = load_log(p)
    check("12 个必需指标齐全", len(loaded) == 12)
    check("数值正确", loaded["fulltext_included"] == 1)
    check("不平衡校验通过", check_balance(loaded) == [])

    print("\n[2] 数字不平衡被拦下（流程图必须能对上账）")
    bad = dict(loaded, fulltext_included=2)
    issues = check_balance(bad)
    check("included 恒等式破坏被报出", len(issues) == 1 and "included" in issues[0],
          issues[0][:50] if issues else "")
    bad2 = dict(loaded, ta_excluded=5)
    check("screening 恒等式破坏被报出",
          any("screening" in x for x in check_balance(bad2)))
    bad3 = dict(loaded, duplicates_removed=3)
    check("identification 恒等式破坏被报出",
          any("identification" in x for x in check_balance(bad3)))

    print("\n[3] 显式拒绝（缺指标 / 非整数）")
    p_missing = tmp / "missing.csv"
    write_log(p_missing, {k: v for k, v in log.items()
                          if k != "fulltext_included"})
    try:
        load_log(p_missing)
        check("缺指标 → ConfigError", False, "未抛出！")
    except ConfigError as e:
        check("缺指标 → ConfigError", True, str(e)[:40] + "…")
    p_bad = tmp / "bad.csv"
    p_bad.write_text("metric,value\nfulltext_included,many\n", encoding="utf-8")
    try:
        load_log(p_bad)
        check("非整数值 → ConfigError", False, "未抛出！")
    except ConfigError:
        check("非整数值 → ConfigError", True)

    print("\n[4] 渲染（Mermaid + 核对表，确定性）")
    md1 = render_prisma(loaded, "analyte=As country=China")
    md2 = render_prisma(loaded, "analyte=As country=China")
    check("两次渲染完全一致（无时间戳）", md1 == md2)
    check("含 mermaid 代码块", "```mermaid" in md1)
    check("含最终纳入数字", "n = 1" in md1)
    check("含数字核对表", "## 数字核对表" in md1)
    check("含'不物理删除'与 GATE-S2c 提示",
          "不物理删除" in md1 and "GATE-S2c" in md1)

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


def write_log(path: Path, log: dict[str, int]) -> None:
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(LOG_FIELDS)
        for k, v in log.items():
            writer.writerow([k, v])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="generate_prisma.py",
        description="从 screening_log.csv 生成 PRISMA 2020 流程图（S2）")
    ap.add_argument("--input", type=Path, help="screening_log.csv")
    ap.add_argument("--config", type=Path, help="project.yaml（可选：补充项目信息）")
    ap.add_argument("--output", type=Path, help="输出 prisma_flow.md")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    missing = [a for a, v in (("--input", args.input), ("--output", args.output))
               if not v]
    if missing:
        print(f"错误：缺少必需参数 {' '.join(missing)}（--self-test 可单独运行）",
              file=sys.stderr)
        return 2
    if not args.input.exists():
        print(f"错误：输入文件不存在：{args.input}", file=sys.stderr)
        return 2

    try:
        log = load_log(args.input)
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    issues = check_balance(log)
    if issues:
        print("错误：PRISMA 数字不平衡（流程图必须能对上账）：", file=sys.stderr)
        for x in issues:
            print(f"  - {x}", file=sys.stderr)
        return 1

    summary = ""
    if args.config:
        try:
            from screening_schema import load_screening_config
            cfg, _spec = load_screening_config(args.config)
            summary = cfg.summary()
        except ConfigError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2

    with io.open(args.output, "w", encoding="utf-8", newline="") as fh:
        fh.write(render_prisma(log, summary))
    print(f"PRISMA 流程图已生成 → {args.output}")
    print("提醒：流程图数字来自 screening_log.csv；"
          "GATE-S2c 由人工确认后方可在稿件中使用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
