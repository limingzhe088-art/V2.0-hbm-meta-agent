#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_pipeline_integration.py — 全流程链路集成测试（S1→S2→S3→S4→S5→S6）

使用 bpa-china-demo 合成案例（★★ 全部数据虚构 ★★）在临时项目目录中，
通过 `scripts/run_pipeline.py` 逐阶段驱动真实脚本链：

  s1  parse×3 → merge → 草稿            （闸门：GATE-0）
  s2a normalize → dedup                 （闸门：GATE-1, GATE-S2a）
  s2b 应用去重裁决 → classify           （人工模拟：dedup_decisions）
  s2c fulltext                          （闸门：GATE-S2b；人工填 human_decision）
  s2d inclusion → prisma                （闸门：GATE-S2c；人工填终裁）
  s3  validate_table（主表）            （闸门：GATE-2）
  s4  convert_units → wan_convert       （闸门：GATE-3）
  s5  weighted_gm → edi                 （闸门：GATE-4）
  s6  dedup_screen → sensitivity（→ audit，缺手稿时显式跳过）

同时验证：
  - 状态机转换：每个阶段前置闸门缺失 → 退出码 3（WAITING）；
    人工写入 gates.json 后重跑续传 → 退出码 0
  - 链路数据传递：all_raw → normalized → dedup_unique_confirmed →
    title_abstract_screening → fulltext_screening → included_studies →
    master_table → standardized → pooled_input → pooled_results/edi_results →
    dedup_report/sensitivity
  - 人工列保护：title_abstract_screening.csv 等人工产物不被重跑覆盖
  - pipeline_log.json 留痕按序追加

用法
  python tests/test_pipeline_integration.py
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "scripts" / "run_pipeline.py"
DEMO = ROOT / "examples" / "bpa-china-demo"

CHECKS: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))


def read_csv(path: Path) -> list[dict]:
    with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def run_pipeline(project: Path, *args, expect=None) -> subprocess.CompletedProcess:
    r = subprocess.run(
        [sys.executable, str(PIPELINE), "--config", str(project / "project.yaml"),
         *args],
        capture_output=True, text=True, errors="replace")
    if expect is not None and r.returncode != expect:
        raise AssertionError(
            f"run_pipeline {' '.join(args)} 退出码 {r.returncode}（期望 {expect}）\n"
            f"stdout：{(r.stdout or '')[-500:]}\nstderr：{(r.stderr or '')[-300:]}")
    return r


def allow_gates(project: Path, names: list[str]) -> None:
    """人工模拟：在 _state/gates.json 把指定闸门置为 passed（只有测试里的'人'能写）。"""
    gf = project / "_state" / "gates.json"
    data = {"gates": {}}
    if gf.exists():
        data = json.loads(gf.read_text(encoding="utf-8"))
    for n in names:
        data.setdefault("gates", {})[n] = {
            "status": "passed", "by": "human (integration test fixture)",
        }
    gf.parent.mkdir(parents=True, exist_ok=True)
    gf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 人工步骤模拟（与 tests/test_e2e_bpa_demo.py 中同一裁决方案）────────

def human_fill_dedup(project: Path) -> None:
    p = project / "02-screening" / "dedup_decisions.csv"
    rows = read_csv(p)
    for r in rows:
        if r["ai_decision"] == "duplicate":
            r["human_decision"] = "duplicate"
        elif r["ai_decision"] == "suspected_duplicate":
            r["human_decision"] = "keep" if r["record_id"] == r["kept_record_id"] \
                else "duplicate"
    write_csv(p, rows)


def human_fill_ta(project: Path) -> None:
    p = project / "02-screening" / "title_abstract_screening.csv"
    rows = read_csv(p)
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
    write_csv(p, rows)


def human_fill_ft(project: Path) -> None:
    p = project / "02-screening" / "fulltext_screening.csv"
    rows = read_csv(p)
    norm = {r["record_id"]: r for r in
            read_csv(project / "02-screening" / "normalized_records.csv")}
    for r in rows:
        title = norm.get(r["record_id"], {}).get("title", "")
        if r["ai_screening_decision"] == "include_candidate":
            if "thyroid" in title.lower():
                # 合成设计：该研究与上海成人研究同一队列 → 人工推翻 AI
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
    write_csv(p, rows)


# ===========================================================================
# 测试步骤
# ===========================================================================

def test_s1_run_with_gate_pause(project: Path):
    """S1：无 GATE-0 → WAITING；人工开启后 → 全部产物落地。"""
    r = run_pipeline(project, "--stage", "s1", expect=3)
    check("缺 GATE-0 → 退出码 3（WAITING 而非错误）", r.returncode == 3)
    check("提示指向 gates.json", "gates.json" in r.stdout)
    allow_gates(project, ["GATE-0"])
    run_pipeline(project, "--stage", "s1", expect=0)
    raw = read_csv(project / "01-search" / "all_raw_records.csv")
    check("S1 合并题录 20 条", len(raw) == 20, f"{len(raw)}")
    check("检索式草稿已生成", (project / "01-search" / "search_strategy_draft.md").exists())
    summary = read_csv(project / "01-search" / "source_summary_pipeline.csv")
    check("机器实算的检索来源汇总（3 库）",
          len([s for s in summary if s["source_database"]]) == 3)


def test_s2a(project: Path):
    """S2-A：闸门 GATE-1 + GATE-S2a；规范化 + 三级去重。"""
    run_pipeline(project, "--stage", "s2a", expect=3)
    allow_gates(project, ["GATE-1", "GATE-S2a"])
    run_pipeline(project, "--stage", "s2a", expect=0)
    check("规范化 20 条", len(read_csv(
        project / "02-screening" / "normalized_records.csv")) == 20)
    dec = read_csv(project / "02-screening" / "dedup_decisions.csv")
    norm = {r["record_id"]: r for r in
            read_csv(project / "02-screening" / "normalized_records.csv")}
    exact = [d for d in dec if d["duplicate_match_type"] == "exact_doi"]
    check("同 DOI（demo.001）的重复被 Level 1 标记 duplicate，保留者为 demo.001 题录",
          len(exact) == 1 and exact[0]["ai_decision"] == "duplicate"
          and norm[exact[0]["kept_record_id"]]["doi"] == "10.1234/demo.001"
          and all(d["human_decision"] == "" for d in exact))
    fuzzy = [d for d in dec if d["ai_decision"] == "suspected_duplicate"]
    check("同题同人同年（无 DOI 的一条）→ 模糊疑似一对（不自动合并）",
          len(fuzzy) == 2 and all(d["duplicate_match_type"] == "fuzzy_title"
                                  for d in fuzzy)
          and norm[fuzzy[0]["record_id"]]["title"].lower()
          == norm[fuzzy[1]["record_id"]]["title"].lower())
    check("AI 去重后唯一 19 条", len(read_csv(
        project / "02-screening" / "dedup_unique.csv")) == 19)


def test_s2b_pending_guard_and_run(project: Path):
    """S2-B：疑似重复未裁决 → WAITING；人工裁决后 → 分类完成。"""
    r = run_pipeline(project, "--stage", "s2b", expect=3)
    check("疑似重复未裁决 → WAITING 并给出待决清单", "未裁决" in r.stdout)
    human_fill_dedup(project)
    run_pipeline(project, "--stage", "s2b", expect=0)
    check("人工确认唯一集 18 条", len(read_csv(
        project / "02-screening" / "dedup_unique_confirmed.csv")) == 18)
    ta = read_csv(project / "02-screening" / "title_abstract_screening.csv")
    check("题摘分类 18 条（8 hbm + 3 review + 3 animal + 2 in vitro + 2 non-hbm）",
          len(ta) == 18, f"{len(ta)}")


def test_s2c_gate_and_run(project: Path):
    """S2-C：闸门 GATE-S2b；人工把全文按 record_id 归位后全文判定。"""
    human_fill_ta(project)
    # 人工模拟：演示全文按 R-id 命名，管线题录是解析器 id —— 人工按标题
    # 对应关系把全文文件复制为 {record_id}.txt（真实流程即此操作）
    rid_title = {r["record_id"]: r["title"].casefold()
                 for r in read_csv(project / "01-search" / "raw_records_example.csv")}
    pid_title = {r["record_id"]: r["title"].casefold()
                 for r in read_csv(project / "02-screening" / "normalized_records.csv")}
    src_dir = DEMO / "02-screening" / "fulltexts"
    n_copied = 0
    for pid, title in pid_title.items():
        for rid, rtitle in rid_title.items():
            if rtitle == title and (src_dir / f"{rid}.txt").exists():
                shutil.copy(src_dir / f"{rid}.txt",
                            project / "02-screening" / "fulltexts" / f"{pid}.txt")
                n_copied += 1
                break
    check("人工按标题对应归位 ≥8 份全文（record_id 命名；R018 与 R001 同题各一份）",
          n_copied >= 8, f"{n_copied}")
    run_pipeline(project, "--stage", "s2c", expect=3)   # GATE-S2b 未开
    allow_gates(project, ["GATE-S2b"])
    run_pipeline(project, "--stage", "s2c", expect=0)
    ft = read_csv(project / "02-screening" / "fulltext_screening.csv")
    check("8 条进入全文判定", len(ft) == 8, f"{len(ft)}")
    check("AI 判定：7 include_candidate + 1 no_usable_values",
          sum(1 for r in ft if r["ai_screening_decision"] == "include_candidate") == 7
          and sum(1 for r in ft if r["ai_reason_code"] == "no_usable_values") == 1)


def test_s2d(project: Path):
    """S2 收口：闸门 GATE-S2c；终裁后清单 + PRISMA。"""
    run_pipeline(project, "--stage", "s2d", expect=3)
    human_fill_ft(project)
    allow_gates(project, ["GATE-S2c"])
    run_pipeline(project, "--stage", "s2d", expect=0)
    check("纳入 6 条", len(read_csv(
        project / "02-screening" / "included_studies.csv")) == 6)
    exc = read_csv(project / "02-screening" / "excluded_studies.csv")
    check("排除 2 条（duplicate_cohort + no_usable_values）",
          sorted(r["exclusion_reason"] for r in exc)
          == ["duplicate_cohort", "no_usable_values"])
    check("PRISMA 流程图已生成", (project / "02-screening" / "prisma_flow.md").exists())


def test_s3(project: Path):
    """S3：闸门 GATE-2；主表契约校验 PASS。"""
    run_pipeline(project, "--stage", "s3", expect=3)
    allow_gates(project, ["GATE-2"])
    run_pipeline(project, "--stage", "s3", expect=0)
    # validate_table 退出码 0 = 无 ERROR（WARN 不阻断）；报告落盘即审计留痕
    check("validate_table 退出码 0（ERROR=0 → PASS）", True)
    check("校验报告已落盘", (project / "03-extraction" / "validate_report.md").exists())


def test_s4(project: Path):
    """S4：闸门 GATE-3；单位换算 → Wan 统计量换算（链式）。"""
    run_pipeline(project, "--stage", "s4", expect=3)
    allow_gates(project, ["GATE-3"])
    run_pipeline(project, "--stage", "s4", expect=0)
    for rel in ("04-standardization/standardized.csv",
                "04-standardization/conversion_trace.csv",
                "04-standardization/wan_trace.csv",
                "05-pooling-edi/pooled_input.csv"):
        check(f"S4 产物 {rel}", (project / rel).exists())


def test_s5(project: Path):
    """S5：闸门 GATE-4；加权合并 + EDI。"""
    run_pipeline(project, "--stage", "s5", expect=3)
    allow_gates(project, ["GATE-4"])
    run_pipeline(project, "--stage", "s5", expect=0)
    pooled = read_csv(project / "05-pooling-edi" / "pooled_results.csv")
    check("合并结果非空且含 gm 列", len(pooled) >= 1 and "gm" in pooled[0])
    edi = read_csv(project / "05-pooling-edi" / "edi_results.csv")
    check("EDI 结果非空", len(edi) >= 1)


def test_s6(project: Path):
    """S6：去重筛查 + 敏感性（S5 输入链路）；audit 缺手稿显式跳过。"""
    r = run_pipeline(project, "--stage", "s6", expect=0)
    check("去重筛查报告落地", (project / "06-qc-audit" / "dedup_report.md").exists())
    check("敏感性报告落地（S4 pooled_input → S6 链路）",
          (project / "06-qc-audit" / "sensitivity.md").exists())
    check("audit_numbers 显式跳过（未配置手稿）且不阻塞", "未配置手稿" in r.stdout)
    check("GATE-5 未被编排器自行置 passed（人闸铁律）",
          json.loads((project / "_state" / "gates.json").read_text(encoding="utf-8"))
          .get("gates", {}).get("GATE-5") is None)


def test_status_and_log(project: Path):
    """--status 与 pipeline_log 状态机留痕。"""
    r = run_pipeline(project, "--status", expect=0)
    check("--status 列出闸门与产物", "闸门" in r.stdout and "关键产物" in r.stdout)
    log = json.loads((project / "_state" / "pipeline_log.json").read_text(
        encoding="utf-8"))
    seq = [(e["stage"], e["status"]) for e in log]
    # 状态机验证：每阶段先 waiting（闸门/人工点）后成功；最终一次运行成功。
    # waiting 痕迹是断点续跑设计的证据，不是缺陷。
    first_order = list(dict.fromkeys(s for s, _ in seq))
    last_status = {}
    for s, st in seq:
        last_status[s] = st
    check("阶段推进顺序 s1→s2a→s2b→s2c→s2d→s3→s4→s5→s6",
          first_order == ["s1", "s2a", "s2b", "s2c", "s2d",
                          "s3", "s4", "s5", "s6"], str(first_order))
    check("每阶段最终一次运行为成功（ok / ok_with_warnings）",
          all(st in ("ok", "ok_with_warnings") for st in last_status.values()),
          str(last_status))
    check("存在 waiting 痕迹（人工闸门暂停被留痕）",
          any(st == "waiting" for _, st in seq))


def test_stage_all_pauses_without_gates(project=None):
    """--stage all：全新项目、无任何闸门 → 在 s1 之前 WAITING（断点续跑语义）。"""
    tmp = Path(tempfile.mkdtemp(prefix="hbm_pipe_all_"))
    project = tmp / "proj"
    shutil.copytree(DEMO, project)
    r = run_pipeline(project, "--stage", "all", expect=3)
    check("--stage all 在首个未满足闸门处暂停", "WAITING" in r.stdout)
    log = json.loads((project / "_state" / "pipeline_log.json").read_text(
        encoding="utf-8"))
    check("暂停也留痕（status=waiting）", log[-1]["status"] == "waiting")
    shutil.rmtree(tmp, ignore_errors=True)


def test_human_artifacts_not_overwritten(project: Path):
    """人工列保护：重跑 s2b/s2c 不覆盖人工编辑过的筛选表。"""
    ta_path = project / "02-screening" / "title_abstract_screening.csv"
    before = ta_path.read_text(encoding="utf-8")
    run_pipeline(project, "--stage", "s2b", expect=0)   # 无 --force
    check("重跑 s2b 不覆盖人工填写的题摘筛选表",
          ta_path.read_text(encoding="utf-8") == before)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hbm_pipe_int_"))
    project = tmp / "proj"
    shutil.copytree(DEMO, project)
    # 02-screening/ 下的 CSV/MD 是 e2e 快照（R001 id 命名空间）——
    # 管线项目必须"从零开始"，否则人工产物保护会跳过重生成造成 id 断层
    for f in (project / "02-screening").glob("*.csv"):
        f.unlink()
    (project / "02-screening" / "prisma_flow.md").unlink(missing_ok=True)
    steps = [test_s1_run_with_gate_pause, test_s2a,
             test_s2b_pending_guard_and_run, test_s2c_gate_and_run,
             test_s2d, test_s3, test_s4, test_s5, test_s6,
             test_status_and_log, test_human_artifacts_not_overwritten,
             test_stage_all_pauses_without_gates]
    for step in steps:
        print(f"\n[{step.__name__}] {step.__doc__.strip().splitlines()[0]}")
        try:
            step(project)
        except AssertionError as exc:
            check(f"{step.__name__} 执行成功", False, str(exc)[:200])
    print("\n" + "-" * 70)
    failed = [c for c in CHECKS if not c[1]]
    for name, _ok, detail in failed:
        print(f"  ❌ {name}  {detail}")
    if failed:
        print(f"集成测试失败：{len(failed)}/{len(CHECKS)} 项未通过")
        return 1
    print(f"集成测试通过：{len(CHECKS)}/{len(CHECKS)} 项全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
