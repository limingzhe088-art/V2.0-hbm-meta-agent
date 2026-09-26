#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_pipeline.py — S1→S6 全流程编排器（HBM-Meta-Agent / v2）

定位
  按阶段调度各子技能脚本、在阶段边界检查人工闸门（`_state/gates.json`）、
  把运行留痕写入 `_state/pipeline_log.json`。
  **本脚本只是调度器，不是闸门的开启者**：任何 gate 未通过 → 打印
  "WAITING（等待人工）"并退出码 3，由人完成对应文件/裁决后重跑续传。

阶段与闸门（对应 shared/quality-gates.md；嵌套子阶段支持断点续跑）

  s1   解析（parse_ris/parse_enl/parse_bibtex 按库分派）→ merge_sources
       → generate_search_terms（草稿）
        需要：GATE-0
  s2a  normalize_records → dedup_records
        需要：GATE-1, GATE-S2a
  s2b  应用去重人工裁决（产生 dedup_unique_confirmed.csv）→ classify_articles
        需要：GATE-S2a；暂停点：人工填写 dedup_decisions 的 human_decision、
              人工填写 title_abstract_screening 的 human_decision（GATE-S2b）
  s2c  fulltext_screening（脚本强制 human_decision 无空值）
        需要：GATE-S2b；暂停点：人工填写 human_final_decision（GATE-S2c）
  s2d  generate_inclusion_list → generate_prisma
        需要：GATE-S2c
  s3   validate_table（校验 input.master_table；提取本身是人工/AI 协助环节）
        需要：GATE-S2c, GATE-2
  s4   convert_units → wan_convert（含快照指纹登记）
        需要：GATE-3
  s5   weighted_gm → edi_calculation
        需要：GATE-4
  s6   dedup_screen → sensitivity → audit_numbers（仅当 input.manuscript
        已配置且存在；缺手稿 → 显式跳过并提示）
        需要：GATE-4（GATE-5 由本阶段产出物供人工裁决）
  all  依序 s1→s6；遇到第一个未满足的闸门/人工产物即 WAITING 退出，
       人完成后重跑同一命令续传（人工编辑过的产物不会被覆盖）。

退出码
  0 成功   2 硬错误（脚本失败/配置错误）   3 WAITING（等待人工操作）
  --status 永远返回 0（只读）

安全设计
  - 人工编辑过的产物（search_strategy_draft.md、dedup_decisions.csv、
    title_abstract_screening.csv、fulltext_screening.csv）**默认跳过不覆盖**，
    --force 才重生成。
  - 原始导出件（01-search/*.ris 等）只读，本脚本永不写入。

用法
  python scripts/run_pipeline.py --config project.yaml --stage all
  python scripts/run_pipeline.py --config project.yaml --stage s2a
  python scripts/run_pipeline.py --config project.yaml --status
  python scripts/run_pipeline.py --self-test
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "shared"),
           str(_ROOT / "skills" / "S2-screening-quality" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from project_config import ConfigError, load_config  # noqa: E402
    from record_schema import (  # noqa: E402
        RECORD_FIELDS, normalize_source, write_records_csv,
    )
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/ 模块（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


EXIT_OK, EXIT_ERROR, EXIT_WAITING = 0, 2, 3

SKILL = _ROOT / "skills"
S1 = SKILL / "S1-search-strategy" / "scripts"
S2 = SKILL / "S2-screening-quality" / "scripts"
S3 = SKILL / "S3-extraction-standardization" / "scripts"
S4 = SKILL / "S4-unit-statistic-conversion" / "scripts"
S5 = SKILL / "S5-weighted-pooling-edi" / "scripts"
S6 = SKILL / "S6-qc-audit-revision" / "scripts"

# 人工编辑过的产物：默认跳过不覆盖（--force 才重生成）
HUMAN_EDITED = {
    "search_strategy_draft.md",
    "dedup_decisions.csv",
    "title_abstract_screening.csv",
    "fulltext_screening.csv",
}

# 各阶段前置闸门（shared/quality-gates.md；partial 仅 GATE-5 允许——本编排器
# 不把 GATE-5 作为前置，故此处不允许任何 partial）
STAGE_GATES: dict[str, list[str]] = {
    "s1": ["GATE-0"],
    "s2a": ["GATE-1", "GATE-S2a"],
    "s2b": ["GATE-S2a"],
    "s2c": ["GATE-S2b"],
    "s2d": ["GATE-S2c"],
    "s2": ["GATE-1", "GATE-S2a"],
    "s3": ["GATE-S2c", "GATE-2"],
    "s4": ["GATE-3"],
    "s5": ["GATE-4"],
    "s6": ["GATE-4"],
    "all": ["GATE-0"],
}
STAGE_ORDER = ["s1", "s2a", "s2b", "s2c", "s2d", "s3", "s4", "s5", "s6"]
STAGE_TITLES = {
    "s1": "S1 检索与题录导入", "s2a": "S2-A 规范化与去重",
    "s2b": "S2-B 应用去重裁决 + 题摘分类", "s2c": "S2-C 全文筛选",
    "s2d": "S2 收口：纳入清单 + PRISMA", "s3": "S3 主表契约校验",
    "s4": "S4 单位与统计量标准化", "s5": "S5 加权合并 + EDI",
    "s6": "S6 质控审计",
}


# ===========================================================================
# 闸门状态（_state/gates.json；唯一可写 passed 的是人——本脚本只读）
# ===========================================================================

def gates_file(project_dir: Path) -> Path:
    return project_dir / "_state" / "gates.json"


def read_gates(project_dir: Path) -> dict[str, dict]:
    p = gates_file(project_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    gates = data.get("gates", data) if isinstance(data, dict) else {}
    return gates if isinstance(gates, dict) else {}


def gate_satisfied(gates: dict[str, dict], name: str) -> bool:
    entry = gates.get(name) or {}
    return str(entry.get("status", "")).strip().lower() == "passed"


def missing_gates(gates: dict[str, dict], required: list[str]) -> list[str]:
    return [g for g in required if not gate_satisfied(gates, g)]


# ===========================================================================
# 运行日志（_state/pipeline_log.json）
# ===========================================================================

def log_path(project_dir: Path) -> Path:
    return project_dir / "_state" / "pipeline_log.json"


def append_log(project_dir: Path, entry: dict) -> None:
    p = log_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    log: list = []
    if p.exists():
        try:
            log = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(log, list):
                log = []
        except (json.JSONDecodeError, OSError):
            log = []
    log.append(entry)
    p.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def read_log(project_dir: Path) -> list:
    p = log_path(project_dir)
    if not p.exists():
        return []
    try:
        log = json.loads(p.read_text(encoding="utf-8"))
        return log if isinstance(log, list) else []
    except (json.JSONDecodeError, OSError):
        return []


# ===========================================================================
# 步骤执行
# ===========================================================================

class Runner:
    def __init__(self, project_dir: Path, config_path: Path, force: bool = False):
        self.project_dir = project_dir
        self.config = config_path
        self.force = force
        self.steps: list[dict] = []

    def run(self, name: str, script: Path, *args: str,
            human_edited_output: tuple[Path, str | None] | None = None,
            ok_exits: tuple = (0,)) -> int:
        """ok_exits：可接受的退出码集合。
        约定（各子技能脚本统一）：0=干净；1=完成但存在告警/待人工裁决项
        （报告已产出，留痕后继续）；2=前置条件不满足（错误）。
        human_edited_output=(输出路径, 人工列名)：
          - 文件不存在 → 生成（AI 草稿态）
          - 存在且人工列已有填写 → 跳过（人工产物，不得覆盖）
          - 存在但人工列全空 → 重生成（仍是 AI 草稿态）
          - 人工列=None 的自由文档（如检索式草稿）→ 存在即跳过
          --force 覆盖以上全部。"""
        if human_edited_output:
            out_path, guard_column = human_edited_output
            if out_path.exists() and not self.force:
                if guard_column is None:
                    self.steps.append({"step": name,
                                       "status": "skipped_human_edited",
                                       "output": str(out_path)})
                    print(f"  ⏭  {name}：{out_path.name} 已存在（人工文档，跳过）")
                    return EXIT_OK
                rows = _read_csv(out_path)
                touched = rows is not None and any(
                    (r.get(guard_column) or "").strip() for r in rows)
                if touched:
                    self.steps.append({"step": name,
                                       "status": "skipped_human_edited",
                                       "output": str(out_path)})
                    print(f"  ⏭  {name}：{out_path.name} 人工列已有填写（跳过；"
                          "--force 可重生成）")
                    return EXIT_OK
                out_path.unlink()   # 仍是 AI 草稿态 → 重生成
        r = subprocess_run([sys.executable, str(script), *[str(a) for a in args]])
        if r.returncode in ok_exits:
            status = "ok" if r.returncode == 0 else "ok_with_warnings"
            self.steps.append({"step": name, "status": status,
                               "exit": r.returncode})
            mark = "✅" if r.returncode == 0 else "🟡"
            print(f"  {mark} {name}" +
                  ("（完成，含告警/待裁决项——详见输出报告）"
                   if r.returncode != 0 else ""))
            return EXIT_OK
        self.steps.append({"step": name, "status": "error", "exit": r.returncode})
        print(r.stdout[-600:], r.stderr[-600:], sep="\n")
        print(f"  ❌ {name} 失败（退出码 {r.returncode}）")
        return EXIT_ERROR

    def internal(self, name: str, ok: bool, detail: str = "") -> int:
        self.steps.append({"step": name, "status": "ok" if ok else "error",
                           "detail": detail})
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
        return EXIT_OK if ok else EXIT_ERROR


def subprocess_run(cmd: list[str]) -> "subprocess.CompletedProcess[str]":
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


# ===========================================================================
# 各阶段实现
# ===========================================================================

def phase_s1(runner: Runner, cfg) -> int:
    rc = EXIT_OK
    parsed: list[Path] = []
    # 1) 按库解析导出件（显式收集本运行产物，避免误吞 01-search 下其他 CSV）
    for entry in cfg.raw.get("search", {}).get("databases", []) or []:
        name = (entry.get("name", "") if isinstance(entry, dict) else str(entry))
        fmt = (entry.get("export_format", "ris") if isinstance(entry, dict) else "ris")
        file_rel = (entry.get("export_file", "") if isinstance(entry, dict) else "")
        if not file_rel:
            print(f"  ⏭  {name}：未登记 export_file（人工执行检索并导出后填写）")
            continue
        src_file = runner.project_dir / file_rel
        if not src_file.exists():
            print(f"  ⏭  {name}：导出件不存在 {file_rel}（等待人工导出）")
            continue
        try:
            slug = normalize_source(str(name).lower())
        except Exception:
            print(f"  ⏭  {name}：无法归一来源名，跳过解析")
            continue
        if fmt == "ris":
            script, extra = S1 / "parse_ris.py", []
        elif fmt == "bib":
            script, extra = S1 / "parse_bibtex.py", []
        elif fmt in ("xml", "enl", "txt"):
            script, extra = S1 / "parse_enl.py", []
        else:
            print(f"  ⏭  {name}：未知导出格式 `{fmt}`（允许 ris/enl/xml/bib/txt）")
            continue
        out = runner.project_dir / "01-search" / f"raw_{slug.lower()}.csv"
        r = subprocess_run([sys.executable, str(script), "--input", str(src_file),
                            "--output", str(out), "--source", slug.lower(),
                            "--validate", *extra])
        runner.steps.append({"step": f"s1.parse[{slug}]", "status":
                             "ok" if r.returncode == 0 else "error",
                             "exit": r.returncode})
        if r.returncode != 0:
            print(r.stderr[-500:])
            print(f"  ❌ s1.parse[{slug}] 失败")
            rc = EXIT_ERROR
        else:
            print(f"  ✅ s1.parse[{slug}] → {out.name}")
            parsed.append(out)
    if rc != EXIT_OK:
        return rc
    # 2) 合并（只合并本运行解析出的 raw_*.csv）
    raws = parsed
    if not raws:
        print("  ⏭  暂无已解析题录（等待人工导出/解析）")
        return EXIT_WAITING
    rc = runner.run("s1.merge", S1 / "merge_sources.py",
                    "--inputs", *[str(p) for p in raws],
                    "--output", runner.project_dir / "01-search" / "all_raw_records.csv",
                    "--summary", runner.project_dir / "01-search" / "source_summary_pipeline.csv")
    if rc != EXIT_OK:
        return rc
    # 3) 检索式草稿（人工编辑产物）
    return runner.run("s1.draft", S1 / "generate_search_terms.py",
                      "--config", runner.config,
                      "--output", runner.project_dir / "01-search" / "search_strategy_draft.md",
                      human_edited_output=(runner.project_dir / "01-search"
                                           / "search_strategy_draft.md", None))


def _apply_dedup_decisions(project_dir: Path) -> tuple[bool, str]:
    """应用人工去重裁决（机械过滤，不做裁决）：suspected 未裁决 → 待人工。"""
    s2_dir = project_dir / "02-screening"
    decisions = _read_csv(s2_dir / "dedup_decisions.csv")
    unique = _read_csv(s2_dir / "dedup_unique.csv")
    if decisions is None or unique is None:
        return False, "dedup_decisions.csv / dedup_unique.csv 缺失（先运行 s2a）"
    by_id = {d.get("record_id", ""): d for d in decisions}
    pending = [d["record_id"] for d in decisions
               if d.get("ai_decision") == "suspected_duplicate"
               and not (d.get("human_decision") or "").strip()]
    if pending:
        return False, (f"{len(pending)} 条疑似重复未裁决（如 {pending[:3]}…）。"
                       "请在 dedup_decisions.csv 的 human_decision 填写 "
                       "keep/duplicate 后重跑。")
    confirmed = [r for r in unique
                 if (by_id.get(r.get("record_id", "")) or {}).get("human_decision")
                 != "duplicate"]
    out = s2_dir / "dedup_unique_confirmed.csv"
    write_records_csv(out, [{f: r.get(f, "") for f in RECORD_FIELDS}
                            for r in confirmed])
    return True, f"{len(confirmed)} 条确认唯一（剔除人工判定的重复）"


def _read_csv(path: Path):
    import csv as _csv
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in _csv.DictReader(fh)]


def phase_s2a(runner: Runner) -> int:
    rc = runner.run("s2a.normalize", S2 / "normalize_records.py",
                    "--input", runner.project_dir / "01-search" / "all_raw_records.csv",
                    "--output", runner.project_dir / "02-screening" / "normalized_records.csv",
                    "--validate")
    if rc != EXIT_OK:
        return rc
    return runner.run("s2a.dedup", S2 / "dedup_records.py",
                      "--input", runner.project_dir / "02-screening" / "normalized_records.csv",
                      "--config", runner.config,
                      "--output-dir", runner.project_dir / "02-screening")


def phase_s2b(runner: Runner) -> int:
    ok, detail = _apply_dedup_decisions(runner.project_dir)
    if runner.internal("s2b.apply_dedup_decisions", ok, detail) != EXIT_OK:
        return EXIT_WAITING  # 未裁决 = 等待人工，不是错误
    return runner.run("s2b.classify", S2 / "classify_articles.py",
                      "--input", runner.project_dir / "02-screening" / "dedup_unique_confirmed.csv",
                      "--config", runner.config,
                      "--output", runner.project_dir / "02-screening" / "title_abstract_screening.csv",
                      human_edited_output=(runner.project_dir / "02-screening"
                                           / "title_abstract_screening.csv",
                                           "human_decision"))


def _column_filled(path: Path, column: str) -> tuple[bool, int]:
    rows = _read_csv(path)
    if rows is None:
        return False, 0
    empty = sum(1 for r in rows if not (r.get(column) or "").strip())
    return empty == 0, empty


def phase_s2c(runner: Runner) -> int:
    ta = runner.project_dir / "02-screening" / "title_abstract_screening.csv"
    filled, empty = _column_filled(ta, "human_decision")
    if not filled:
        print(f"  ⏳ WAITING：title_abstract_screening.csv 有 {empty} 行 "
              "human_decision 为空 —— 请人工逐条裁决（GATE-S2b）后重跑。")
        return EXIT_WAITING
    return runner.run("s2c.fulltext", S2 / "fulltext_screening.py",
                      "--input", ta,
                      "--records", runner.project_dir / "02-screening" / "normalized_records.csv",
                      "--fulltext-dir", runner.project_dir / "02-screening" / "fulltexts",
                      "--config", runner.config,
                      "--output", runner.project_dir / "02-screening" / "fulltext_screening.csv",
                      human_edited_output=(runner.project_dir / "02-screening"
                                           / "fulltext_screening.csv",
                                           "human_final_decision"))


def phase_s2d(runner: Runner) -> int:
    ft = runner.project_dir / "02-screening" / "fulltext_screening.csv"
    for col in ("human_final_decision",):
        filled, empty = _column_filled(ft, col)
        if not filled:
            print(f"  ⏳ WAITING：fulltext_screening.csv 有 {empty} 行 "
                  f"{col} 为空 —— 请人工终裁（GATE-S2c）后重跑。")
            return EXIT_WAITING
    rc = runner.run("s2d.inclusion", S2 / "generate_inclusion_list.py",
                    "--input", ft,
                    "--records", runner.project_dir / "02-screening" / "normalized_records.csv",
                    "--ta-screening", runner.project_dir / "02-screening" / "title_abstract_screening.csv",
                    "--config", runner.config,
                    "--output-dir", runner.project_dir / "02-screening")
    if rc != EXIT_OK:
        return rc
    return runner.run("s2d.prisma", S2 / "generate_prisma.py",
                      "--input", runner.project_dir / "02-screening" / "screening_log.csv",
                      "--config", runner.config,
                      "--output", runner.project_dir / "02-screening" / "prisma_flow.md")


def _master_table(cfg, project_dir: Path) -> Path | None:
    rel = (cfg.raw.get("input") or {}).get("master_table")
    if not rel:
        return None
    p = project_dir / str(rel)
    return p if p.exists() else None


def phase_s3(runner: Runner, cfg) -> int:
    master = _master_table(cfg, runner.project_dir)
    if master is None:
        print("  ⏳ WAITING：未找到主表（project.yaml → input.master_table）。"
              "请先对 included_studies.csv 的研究完成人工/AI 协助提取并落表。")
        return EXIT_WAITING
    return runner.run("s3.validate", S3 / "validate_table.py",
                      "--input", master, "--config", runner.config,
                      "--out", runner.project_dir / "03-extraction" / "validate_report.md")


def phase_s4(runner: Runner, cfg) -> int:
    master = _master_table(cfg, runner.project_dir)
    if master is None:
        print("  ⏳ WAITING：未找到主表（input.master_table）。")
        return EXIT_WAITING
    rc = runner.run("s4.convert_units", S4 / "convert_units.py",
                    "--input", master, "--config", runner.config,
                    "--out", runner.project_dir / "04-standardization" / "standardized.csv",
                    "--trace", runner.project_dir / "04-standardization" / "conversion_trace.csv",
                    "--snapshot-registry", runner.project_dir / "_state" / "snapshots.csv",
                    ok_exits=(0, 1))
    if rc != EXIT_OK:
        return rc
    return runner.run("s4.wan_convert", S4 / "wan_convert.py",
                      "--input", runner.project_dir / "04-standardization" / "standardized.csv",
                      "--config", runner.config,
                      "--out", runner.project_dir / "05-pooling-edi" / "pooled_input.csv",
                      "--trace", runner.project_dir / "04-standardization" / "wan_trace.csv",
                      "--flags-derivation", runner.project_dir / "04-standardization" / "flags_derivation.csv",
                      ok_exits=(0, 1))


def phase_s5(runner: Runner) -> int:
    pooled_in = runner.project_dir / "05-pooling-edi" / "pooled_input.csv"
    rc = runner.run("s5.weighted_gm", S5 / "weighted_gm.py",
                    "--input", pooled_in, "--config", runner.config,
                    "--dims", "sample_type,period,region",
                    "--out", runner.project_dir / "05-pooling-edi" / "pooled_results.csv",
                    ok_exits=(0, 1))
    if rc != EXIT_OK:
        return rc
    return runner.run("s5.edi", S5 / "edi_calculation.py",
                      "--input", pooled_in, "--config", runner.config,
                      "--out", runner.project_dir / "05-pooling-edi" / "edi_results.csv",
                      ok_exits=(0, 1))


def phase_s6(runner: Runner, cfg) -> int:
    master = _master_table(cfg, runner.project_dir)
    if master is None:
        print("  ⏳ WAITING：未找到主表（input.master_table）。")
        return EXIT_WAITING
    pooled_in = runner.project_dir / "05-pooling-edi" / "pooled_input.csv"
    matrix = (cfg.matrix_scope or ["Urine"])[0]
    rc = runner.run("s6.dedup_screen", S6 / "dedup_screen.py",
                    "--input", master, "--config", runner.config,
                    "--out", runner.project_dir / "06-qc-audit" / "dedup_report.md",
                    ok_exits=(0, 1))
    if rc != EXIT_OK:
        return rc
    if not pooled_in.exists():
        print("  ⏳ WAITING：缺少 05-pooling-edi/pooled_input.csv（先运行 s4）。")
        return EXIT_WAITING
    rc = runner.run("s6.sensitivity", S6 / "sensitivity.py",
                    "--input", pooled_in, "--config", runner.config,
                    "--matrix", matrix,
                    "--out", runner.project_dir / "06-qc-audit" / "sensitivity.md",
                    ok_exits=(0, 1))
    if rc != EXIT_OK:
        return rc
    manuscript_rel = (cfg.raw.get("input") or {}).get("manuscript")
    if not manuscript_rel:
        print("  ⏭  s6.audit_numbers：未配置手稿（input.manuscript）——数值审计待"
              "成稿后单独运行。GATE-5（敏感性裁决）的输入已由 sensitivity.md 产出。")
        runner.steps.append({"step": "s6.audit_numbers", "status":
                             "skipped_no_manuscript"})
        return EXIT_OK
    manuscript = runner.project_dir / str(manuscript_rel)
    if not manuscript.exists():
        print(f"  ⏳ WAITING：手稿不存在 {manuscript_rel}（已配置 input.manuscript）。")
        return EXIT_WAITING
    return runner.run("s6.audit_numbers", S6 / "audit_numbers.py",
                      "--input", manuscript, "--master-table", master,
                      "--config", runner.config,
                      "--out", runner.project_dir / "06-qc-audit" / "audit_report.md",
                      ok_exits=(0, 1))


PHASES = {
    "s1": lambda r, c: phase_s1(r, c),
    "s2a": lambda r, c: phase_s2a(r),
    "s2b": lambda r, c: phase_s2b(r),
    "s2c": lambda r, c: phase_s2c(r),
    "s2d": lambda r, c: phase_s2d(r),
    "s3": lambda r, c: phase_s3(r, c),
    "s4": lambda r, c: phase_s4(r, c),
    "s5": lambda r, c: phase_s5(r),
    "s6": lambda r, c: phase_s6(r, c),
}


# ===========================================================================
# --status
# ===========================================================================

def print_status(cfg, project_dir: Path) -> int:
    gates = read_gates(project_dir)
    print("=" * 72)
    print(f"项目状态（{cfg.summary()}）")
    print("=" * 72)
    print("\n[闸门]")
    all_gates = ["GATE-0", "GATE-1", "GATE-S2a", "GATE-S2b", "GATE-S2c",
                 "GATE-2", "GATE-3", "GATE-4", "GATE-5"]
    for g in all_gates:
        status = (gates.get(g) or {}).get("status", "pending")
        mark = "✅" if status == "passed" else ("🟡" if status == "partial" else "⬜")
        print(f"  {mark} {g}: {status}")
    print("\n[关键产物]")
    artifacts = [
        ("01-search/all_raw_records.csv", "S1 合并题录"),
        ("01-search/search_strategy_draft.md", "S1 检索式草稿"),
        ("02-screening/normalized_records.csv", "S2 规范化题录"),
        ("02-screening/dedup_decisions.csv", "S2 去重裁决表"),
        ("02-screening/dedup_unique_confirmed.csv", "S2 人工确认唯一集"),
        ("02-screening/title_abstract_screening.csv", "S2 题摘筛选表"),
        ("02-screening/fulltext_screening.csv", "S2 全文筛选表"),
        ("02-screening/included_studies.csv", "S2 纳入清单"),
        ("02-screening/prisma_flow.md", "S2 PRISMA 流程图"),
        ((cfg.raw.get("input") or {}).get("master_table", "") or "-", "S3 主表"),
        ("04-standardization/standardized.csv", "S4 标准化输出"),
        ("05-pooling-edi/pooled_results.csv", "S5 合并结果"),
        ("05-pooling-edi/edi_results.csv", "S5 EDI 结果"),
        ("06-qc-audit/sensitivity.md", "S6 敏感性报告"),
        ("06-qc-audit/audit_report.md", "S6 数值审计报告"),
    ]
    for rel, label in artifacts:
        p = project_dir / rel if rel != "-" else None
        exists = p is not None and p.exists()
        print(f"  {'✅' if exists else '⬜'} {label}: {rel}")
    log = read_log(project_dir)
    print(f"\n[运行日志] 共 {len(log)} 条")
    for entry in log[-3:]:
        print(f"  - {entry.get('time', '?')} stage={entry.get('stage')} "
              f"status={entry.get('status')}")
    return EXIT_OK


# ===========================================================================
# self-test
# ===========================================================================

def run_self_test() -> int:
    print("=" * 70)
    print("run_pipeline.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="hbm_pipe_"))

    print("\n[1] 阶段表与闸门映射")
    check("9 个子阶段均有标题", set(STAGE_ORDER) <= set(STAGE_TITLES))
    check("闸门映射覆盖全部子阶段", set(STAGE_GATES) >= set(STAGE_ORDER))
    check("s2 前置 = GATE-1 + GATE-S2a",
          STAGE_GATES["s2"] == ["GATE-1", "GATE-S2a"])
    check("s3 前置含 GATE-2 与 GATE-S2c",
          set(STAGE_GATES["s3"]) == {"GATE-S2c", "GATE-2"})
    check("s4 前置 GATE-3 / s5 前置 GATE-4 / s6 前置 GATE-4",
          STAGE_GATES["s4"] == ["GATE-3"] and STAGE_GATES["s5"] == ["GATE-4"]
          and STAGE_GATES["s6"] == ["GATE-4"])
    check("人机边界：编排器不把 GATE-5 设为任何阶段前置（其输入由 s6 产出）",
          all("GATE-5" not in g for g in STAGE_GATES.values()))

    print("\n[2] 闸门读取与判定")
    gates = read_gates(tmp)
    check("无 gates.json → 空表", gates == {})
    (tmp / "_state").mkdir(exist_ok=True)
    gates_file(tmp).write_text(
        '{"gates": {"GATE-0": {"status": "passed"}, '
        '"GATE-3": {"status": "partial"}}}', encoding="utf-8")
    gates = read_gates(tmp)
    check("passed 判定为满足", gate_satisfied(gates, "GATE-0"))
    check("partial 不算满足（编排器只认 passed）",
          not gate_satisfied(gates, "GATE-3"))
    check("未登记闸门 → 不满足", not gate_satisfied(gates, "GATE-2"))
    check("缺失闸门清单", missing_gates(gates, ["GATE-0", "GATE-2"]) == ["GATE-2"])
    gates_file(tmp).write_text("not-json", encoding="utf-8")
    check("损坏的 gates.json → 容错为空表（等待人工而非崩溃）",
          read_gates(tmp) == {})
    gates_file(tmp).write_text(
        '{"gates": {"GATE-0": {"status": "passed"}}}', encoding="utf-8")

    print("\n[3] 日志读写（追加式，损坏容错）")
    append_log(tmp, {"time": "t1", "stage": "s1", "status": "ok"})
    append_log(tmp, {"time": "t2", "stage": "s2a", "status": "waiting"})
    log = read_log(tmp)
    check("两条日志按序追加", [e["time"] for e in log] == ["t1", "t2"])
    log_path(tmp).write_text("broken", encoding="utf-8")
    append_log(tmp, {"time": "t3", "stage": "s2b", "status": "ok"})
    check("日志损坏后可自愈重建", [e["time"] for e in read_log(tmp)] == ["t3"])

    print("\n[4] 人工编辑产物保护")
    check("保护清单含四类人工产物",
          HUMAN_EDITED == {"search_strategy_draft.md", "dedup_decisions.csv",
                           "title_abstract_screening.csv", "fulltext_screening.csv"})

    print("\n[5] 去重裁决应用（纯机械过滤）")
    import csv as _csv
    s2dir = tmp / "02-screening"
    s2dir.mkdir(exist_ok=True)
    fields = ["record_id", "ai_decision", "human_decision"]
    with io.open(s2dir / "dedup_decisions.csv", "w", encoding="utf-8",
                 newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows([
            {"record_id": "a", "ai_decision": "unique", "human_decision": ""},
            {"record_id": "b", "ai_decision": "suspected_duplicate",
             "human_decision": "duplicate"},
            {"record_id": "c", "ai_decision": "suspected_duplicate",
             "human_decision": "keep"},
        ])
    from record_schema import write_records_csv as _wrc
    urows = [{"record_id": "a", "title": "A"}, {"record_id": "b", "title": "B"},
             {"record_id": "c", "title": "C"}]
    _wrc(s2dir / "dedup_unique.csv", urows)
    ok, detail = _apply_dedup_decisions(tmp)
    check("裁决应用成功", ok, detail)
    confirmed = _read_csv(s2dir / "dedup_unique_confirmed.csv")
    check("人工判 duplicate 的记录被剔除（b 剔除，a/c 保留）",
          {r["record_id"] for r in confirmed} == {"a", "c"})
    with io.open(s2dir / "dedup_decisions.csv", "w", encoding="utf-8",
                 newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows([
            {"record_id": "a", "ai_decision": "unique", "human_decision": ""},
            {"record_id": "b", "ai_decision": "suspected_duplicate",
             "human_decision": ""},   # 未裁决
        ])
    ok, detail = _apply_dedup_decisions(tmp)
    check("疑似重复未裁决 → 待人工（WAITING 而非错误）",
          not ok and "未裁决" in detail, detail[:40])

    print("\n[6] 退出码约定")
    check("0 成功 / 2 错误 / 3 等待人工",
          (EXIT_OK, EXIT_ERROR, EXIT_WAITING) == (0, 2, 3))

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
        prog="run_pipeline.py",
        description="S1→S6 全流程编排器（闸门检查 + 断点续跑 + 运行留痕）")
    ap.add_argument("--config", type=Path, help="project.yaml 路径")
    ap.add_argument("--stage", choices=STAGE_ORDER + ["s2", "all", "status"],
                    help="要运行的阶段（all = s1→s6，遇人工点暂停续跑）")
    ap.add_argument("--force", action="store_true",
                    help="重生成人工编辑过的产物（默认跳过）")
    ap.add_argument("--status", action="store_true", help="查看当前状态")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()
    if not args.config:
        print("错误：缺少 --config（--status 亦需要）。", file=sys.stderr)
        return 2
    if not args.config.exists():
        print(f"错误：project.yaml 不存在：{args.config}", file=sys.stderr)
        return 2

    project_dir = args.config.resolve().parent
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if args.status or args.stage == "status":
        return print_status(cfg, project_dir)
    if not args.stage:
        print("错误：缺少 --stage（可选："
              f"{' | '.join(STAGE_ORDER)} | s2 | all | status）。", file=sys.stderr)
        return 2

    # 阶段展开
    if args.stage == "all":
        stages = list(STAGE_ORDER)
    elif args.stage == "s2":
        stages = ["s2a", "s2b", "s2c", "s2d"]
    else:
        stages = [args.stage]

    gates = read_gates(project_dir)
    # --stage all 的前置只查 GATE-0；逐阶段推进时各自查闸门
    runner = Runner(project_dir, args.config, force=args.force)

    overall = EXIT_OK
    for stage in stages:
        required = STAGE_GATES.get(stage, [])
        if args.stage == "all" and stage == "s1":
            required = STAGE_GATES["s1"]
        miss = missing_gates(gates, required)
        if miss:
            print("=" * 72)
            print(f"⏳ WAITING — 阶段 {stage} 的前置闸门未通过：{', '.join(miss)}")
            print(f"   请人工确认后在 {gates_file(project_dir)} 把对应闸门"
                  " status 置为 \"passed\"（只有人可以开门），然后重跑本命令续传。")
            overall = EXIT_WAITING
            break
        print(f"\n[{stage}] {STAGE_TITLES.get(stage, stage)}")
        try:
            rc = PHASES[stage](runner, cfg)
        except ConfigError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            rc = EXIT_ERROR
        if rc != EXIT_OK:
            overall = rc
            if rc == EXIT_WAITING:
                print(f"\n⏳ 阶段 {stage} 暂停：等待人工操作后重跑同一命令续传。")
            break

    append_log(project_dir, {
        "time": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "stage": args.stage,
        "status": {EXIT_OK: "ok", EXIT_ERROR: "error",
                   EXIT_WAITING: "waiting"}.get(overall, "unknown"),
        "steps": runner.steps,
    })
    if overall == EXIT_WAITING:
        print("\n（状态查询：python scripts/run_pipeline.py --config "
              f"{args.config} --status）")
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
