#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screening_schema.py — ★ S2 筛选质控的**单一来源**（枚举 / 配置校验 / 相似度 / 夹具）

动机
  S2 的 7 个脚本要共享同一套闭集（文章类型、AI/人工决策、排除原因编码）、
  同一套配置校验、同一种标题相似度算法。若各自实现，就会重蹈
  `inferred_flags` 曾在 5 处各写一份的覆辙。本模块把定义收敛为单一来源。

设计要点
  1. **闭集可扩展、但必须显式**：article_types / classification_rules /
     exclusion_reasons 全部从 `project.yaml → screening` 读取；缺失或非法
     → ConfigError 硬错误（不做兜底）。
  2. **AI 只标记，人裁决**：所有 AI 输出列的取值域都以 `uncertain` 为退路；
     human_* 列初始为空，由人工填写（GATE-S2b/S2c 的脚本检查点会拒绝空值）。
  3. **状态机**（被排除的记录不物理删除，只标记 excluded）：
     raw → deduplicated → title_abstract_reviewed → fulltext_retrieved
        → fulltext_reviewed → included / excluded；中间可进入 uncertain。

用法（其他 S2 脚本）
  from screening_schema import (
      STATE_ENUM, AI_TA_DECISIONS, AI_FT_DECISIONS,
      HUMAN_TA_DECISIONS, HUMAN_FT_DECISIONS, DEDUP_AI_DECISIONS,
      validate_screening, load_screening_config, normalize_doi,
      normalize_pmid, first_author, jaccard_bigram, load_fixture_spec,
  )

自检
  python screening_schema.py --self-test
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# shared/project_config.py —— 唯一配置入口（禁止本模块自带口径默认值）
_SHARED = Path(__file__).resolve().parent.parent.parent.parent / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    from project_config import ConfigError, load_config  # noqa: E402
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/project_config.py（{_exc}）", file=sys.stderr)
    raise SystemExit(2)


CONTRACT_VERSION = "1.0.0"

# ===========================================================================
# 1. 闭集枚举（结构性常量；词表内容来自 project.yaml）
# ===========================================================================

# 记录状态机（被排除的记录不物理删除，只标记 excluded；uncertain 是中间态）
STATE_ENUM = [
    "raw", "deduplicated", "title_abstract_reviewed", "fulltext_retrieved",
    "fulltext_reviewed", "uncertain", "included", "excluded",
]

# 题摘阶段 AI 决策（ai_screening_decision）
AI_TA_DECISIONS = ["proceed_to_fulltext", "suggest_exclude", "uncertain"]
# 题摘阶段人工决策（human_decision；GATE-S2b 要求非空）
HUMAN_TA_DECISIONS = ["proceed_to_fulltext", "exclude", "uncertain"]
# 全文阶段 AI 决策
AI_FT_DECISIONS = ["include_candidate", "suggest_exclude", "uncertain"]
# 全文阶段人工最终决策（GATE-S2c 要求非空且不得为 uncertain）
HUMAN_FT_DECISIONS = ["include", "exclude", "uncertain"]
# 去重阶段 AI 决策（dedup_decisions.ai_decision）
DEDUP_AI_DECISIONS = ["unique", "keep", "duplicate", "suspected_duplicate"]
# 去重匹配方式（dedup_decisions.duplicate_match_type；空串 = 无重复组）
DEDUP_MATCH_TYPES = ["exact_doi", "exact_pmid", "fuzzy_title", ""]
# AI 置信度
AI_CONFIDENCE = ["high", "medium", "low"]

# 两个保留文章类型（任何配置都必须包含；见 validate_screening）
RESERVED_ARTICLE_TYPES = ["other", "insufficient_info"]

TA_FIELDS = ["record_id", "ai_article_type", "ai_confidence",
             "ai_screening_decision", "ai_reason_code", "ai_evidence_quote",
             "human_decision", "human_reason_code"]
FT_FIELDS = ["record_id", "fulltext_available", "confirmed_article_type",
             "has_human_samples", "analytes_reported", "has_usable_concentration",
             "ai_screening_decision", "ai_reason_code",
             "human_final_decision", "human_final_reason_code"]
DEDUP_DECISION_FIELDS = ["record_id", "duplicate_group_id", "duplicate_match_type",
                         "kept_record_id", "ai_decision", "human_decision"]
SUMMARY_FIELDS = ["scope", "category", "count"]
LOG_FIELDS = ["metric", "value"]
INCLUDED_FIELDS = ["study_id", "record_id", "title", "first_author", "year", "doi",
                   "country", "population", "matrix", "analyte",
                   "screening_status", "human_gate_status"]
EXCLUDED_FIELDS = INCLUDED_FIELDS + ["exclusion_reason"]


# ===========================================================================
# 2. 配置校验（screening 区块；缺失/非法 → ConfigError，不做兜底）
# ===========================================================================

REQUIRED_SCREENING_KEYS = ["dedup", "min_text_chars", "analyte_terms",
                           "article_types", "type_decision_map",
                           "classification_rules", "exclusion_reasons"]


def _str_list(value, where: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"screening.{where} 必须是非空列表。")
    out = []
    for item in value:
        s = str(item).strip()
        if not s:
            raise ConfigError(f"screening.{where} 含空字符串项。")
        out.append(s)
    return out


def validate_screening(scr: dict) -> dict:
    """校验 project.yaml → screening 区块。任一问题 → ConfigError。"""
    if not isinstance(scr, dict):
        raise ConfigError("screening 区块必须是映射。")
    missing = [k for k in REQUIRED_SCREENING_KEYS if k not in scr]
    if missing:
        raise ConfigError(
            f"project.yaml 缺少 screening 区块的必需键：{', '.join(missing)}。"
            "模板见 templates/project.yaml 的 screening 区块。")

    # dedup
    dedup = scr.get("dedup") or {}
    if not isinstance(dedup, dict):
        raise ConfigError("screening.dedup 必须是映射。")
    for k in ("title_similarity_threshold", "author_match_required", "year_tolerance"):
        if k not in dedup:
            raise ConfigError(f"screening.dedup 缺少 `{k}`（必填，不做默认值兜底）。")
    thr = dedup["title_similarity_threshold"]
    if not isinstance(thr, (int, float)) or not (0 < thr <= 1):
        raise ConfigError(
            f"screening.dedup.title_similarity_threshold = {thr!r} 非法；"
            "必须是 (0, 1] 的数值（Jaccard 相似度阈值）。")
    if not isinstance(dedup["author_match_required"], bool):
        raise ConfigError("screening.dedup.author_match_required 必须是布尔值。")
    yt = dedup["year_tolerance"]
    if not isinstance(yt, int) or yt < 0:
        raise ConfigError("screening.dedup.year_tolerance 必须是非负整数。")

    min_chars = scr["min_text_chars"]
    if not isinstance(min_chars, int) or min_chars < 0:
        raise ConfigError("screening.min_text_chars 必须是非负整数。")

    analyte_terms = _str_list(scr["analyte_terms"], "analyte_terms")

    # article_types（闭集）
    article_types = _str_list(scr["article_types"], "article_types")
    if len(article_types) != len(set(article_types)):
        raise ConfigError("screening.article_types 含重复项。")
    for rt in RESERVED_ARTICLE_TYPES:
        if rt not in article_types:
            raise ConfigError(
                f"screening.article_types 缺少保留类型 `{rt}`"
                "（规则未命中时的退路，任何配置都必须包含）。")

    # type_decision_map（必须覆盖全部 article_types）
    tdm = scr["type_decision_map"]
    if not isinstance(tdm, dict):
        raise ConfigError("screening.type_decision_map 必须是映射。")
    unmapped = [t for t in article_types if t not in tdm]
    if unmapped:
        raise ConfigError(
            f"screening.type_decision_map 未覆盖文章类型：{unmapped}。"
            "每个 article_type 都必须有显式的题摘决策映射。")
    unknown_vals = {v for v in tdm.values() if v not in AI_TA_DECISIONS}
    if unknown_vals:
        raise ConfigError(
            f"screening.type_decision_map 含非法决策值 {sorted(unknown_vals)}；"
            f"允许取值：{', '.join(AI_TA_DECISIONS)}。")
    extra_types = [k for k in tdm if k not in article_types]
    if extra_types:
        raise ConfigError(
            f"screening.type_decision_map 含未在 article_types 中声明的键：{extra_types}。")

    # classification_rules（有序规则引擎）
    rules_raw = scr["classification_rules"]
    if not isinstance(rules_raw, list) or not rules_raw:
        raise ConfigError("screening.classification_rules 必须是非空列表。")
    rules = []
    for i, r in enumerate(rules_raw):
        where = f"classification_rules[{i}]"
        if not isinstance(r, dict):
            raise ConfigError(f"screening.{where} 必须是映射。")
        rid = str(r.get("id") or f"rule_{i + 1:02d}").strip()
        rtype = str(r.get("type") or "").strip()
        if rtype not in article_types:
            raise ConfigError(
                f"screening.{where}.type = `{rtype}` 不在 article_types 闭集内。")
        title_any = [str(x).strip() for x in (r.get("title_any") or []) if str(x).strip()]
        abstract_any = [str(x).strip() for x in (r.get("abstract_any") or [])
                        if str(x).strip()]
        if not title_any and not abstract_any:
            raise ConfigError(
                f"screening.{where}（{rid}）的 title_any 与 abstract_any 不能同时为空。")
        rules.append({
            "id": rid, "type": rtype,
            "title_any": title_any, "abstract_any": abstract_any,
            "require_analyte": bool(r.get("require_analyte", False)),
        })
    seen_ids = [r["id"] for r in rules]
    if len(seen_ids) != len(set(seen_ids)):
        raise ConfigError("screening.classification_rules 的 id 重复。")

    # exclusion_reasons（闭集）
    reasons = _str_list(scr["exclusion_reasons"], "exclusion_reasons")
    if len(reasons) != len(set(reasons)):
        raise ConfigError("screening.exclusion_reasons 含重复项。")
    if "insufficient_info" not in reasons:
        raise ConfigError(
            "screening.exclusion_reasons 缺少保留编码 `insufficient_info`"
            "（全文不可得时的退路）。")

    ftr = scr.get("fulltext_rules") or {}
    if not isinstance(ftr, dict):
        raise ConfigError("screening.fulltext_rules 必须是映射。")
    for k in ("human_sample_terms", "concentration_stat_terms"):
        if k not in ftr:
            raise ConfigError(f"screening.fulltext_rules 缺少 `{k}`（必填）。")

    return {
        "dedup": {"title_similarity_threshold": float(thr),
                  "author_match_required": dedup["author_match_required"],
                  "year_tolerance": yt},
        "min_text_chars": min_chars,
        "analyte_terms": analyte_terms,
        "article_types": article_types,
        "type_decision_map": dict(tdm),
        "classification_rules": rules,
        "exclusion_reasons": reasons,
        "fulltext_rules": {
            "human_sample_terms": _str_list(ftr["human_sample_terms"],
                                            "fulltext_rules.human_sample_terms"),
            "concentration_stat_terms": _str_list(
                ftr["concentration_stat_terms"],
                "fulltext_rules.concentration_stat_terms"),
        },
    }


def load_screening_config(path):
    """加载 project.yaml（require screening）→ (Config, 校验后的 screening spec)。"""
    cfg = load_config(path, require=["screening"])
    return cfg, validate_screening(cfg.raw["screening"])


# ===========================================================================
# 3. 文本/标识规范化与相似度（dedup 与 normalize 共用）
# ===========================================================================

_DOI_URL_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.I)
_DOI_PREFIX_RE = re.compile(r"^doi\s*:\s*", re.I)
_DOI_SHAPE_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def normalize_doi(value: str) -> str:
    """
    DOI 规范化：去 URL 前缀（https://doi.org/）、去 `doi:` 前缀、小写、去首尾标点。
    识别不出 10.xxxx/xxx 形态时**原样返回**（交人工核对，不猜测改写）。
    """
    v = normalize_text(value)
    if not v:
        return ""
    v = _DOI_URL_RE.sub("", v)
    v = _DOI_PREFIX_RE.sub("", v)
    v = v.strip(".,;")
    v = v.lower()
    if not _DOI_SHAPE_RE.match(v):
        return v  # 形态异常 → 原样保留（normalize 脚本会记 warning）
    return v


def normalize_pmid(value: str) -> str:
    """PMID 规范化：仅保留纯数字；其余原样返回空？—— 不猜：非纯数字返回原文本。"""
    v = normalize_text(value)
    if not v:
        return ""
    if v.isdigit():
        return v
    return v  # 形态异常原样保留，由调用方记 warning


def first_author(authors: str) -> str:
    """作者串的第一作者（';' 与 ' and ' 都算分隔）。"""
    v = normalize_text(authors)
    if not v:
        return ""
    for sep in (";", " and ", "&"):
        if sep in v.lower():
            idx = v.lower().find(sep)
            return normalize_text(v[:idx])
    return v


def title_tokens(title: str) -> list[str]:
    """标题分词：小写、去标点、去单字符与纯数字词。"""
    t = _PUNCT_RE.sub(" ", normalize_text(title).lower())
    return [w for w in t.split() if len(w) > 1 and not w.isdigit()]


def jaccard_bigram(title_a: str, title_b: str) -> float:
    """
    标题相似度：词 bigram 的 Jaccard 系数（bigram 比词集对语序更稳）。
    两标题都分不出 bigram 时：完全相等 → 1.0，否则 0.0（不猜）。
    """
    toks_a, toks_b = title_tokens(title_a), title_tokens(title_b)
    if len(toks_a) < 2 or len(toks_b) < 2:
        return 1.0 if normalize_text(title_a).lower() == normalize_text(title_b).lower() \
            and bool(normalize_text(title_a)) else 0.0
    sh_a = {tuple(toks_a[i:i + 2]) for i in range(len(toks_a) - 1)}
    sh_b = {tuple(toks_b[i:i + 2]) for i in range(len(toks_b) - 1)}
    union = sh_a | sh_b
    if not union:
        return 0.0
    return len(sh_a & sh_b) / len(union)


# ===========================================================================
# 4. 通用 CSV 读写（S2 各输出共用；表头必须精确匹配）
# ===========================================================================

def read_csv_dicts(path: Path, encoding: str = "utf-8-sig") -> list[dict]:
    try:
        with io.open(path, "r", encoding=encoding, newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path} 无法以 {encoding} 解码（{exc}）。") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取 {path}：{exc}") from exc


def write_csv_dicts(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(fieldnames)
        for row in rows:
            writer.writerow([row.get(f, "") for f in fieldnames])


# ===========================================================================
# 5. 自检/模板生成共用夹具（唯一来源；内容与 templates/project.yaml 的
#    screening 区块保持同构 —— 改一处须同步另一处并跑全量自检）
# ===========================================================================

FIXTURE_SCREENING_YAML = """
project:
  name: "Fixture screening project"
  analyte: "As"
  country: "China"
  matrix_scope: ["Urine", "Blood"]
  config_version: "1.0.0"

period_scheme:
  boundaries:
    - {label: "1980-2000", start: 1980, end: 2000}
    - {label: "2001-2010", start: 2001, end: 2010}
  freeze_date: "2026-07-19"

region_map:
  Southwest: ["云南省"]

unit_policy:
  Urine: {target: "ug/g Cr"}
  Blood: {target: "ug/L"}

standardization:
  median_to_gm_strategy: "as_gm"
  wan_variant: "minus"
  creatinine_path_mode: "CC_direct"

screening:
  dedup:
    title_similarity_threshold: 0.85
    author_match_required: true
    year_tolerance: 0
  min_text_chars: 40
  analyte_terms: ["arsenic", "砷"]
  article_types: [human_hbm_original, human_non_hbm, review, animal_study,
                  in_vitro_study, methodology, policy_or_comment, other,
                  insufficient_info]
  type_decision_map:
    human_hbm_original: "proceed_to_fulltext"
    human_non_hbm: "uncertain"
    review: "suggest_exclude"
    animal_study: "suggest_exclude"
    in_vitro_study: "suggest_exclude"
    methodology: "uncertain"
    policy_or_comment: "suggest_exclude"
    other: "uncertain"
    insufficient_info: "uncertain"
  classification_rules:
    - {id: review_title, type: review,
       title_any: ["systematic review", "meta-analysis", "scoping review", "综述"]}
    - {id: review_abstract, type: review,
       abstract_any: ["systematic review", "meta-analysis"]}
    - {id: animal_title, type: animal_study,
       title_any: ["mice", "mouse", "rats", "zebrafish", "rabbits", "大鼠", "小鼠", "动物实验"]}
    - {id: invitro_title, type: in_vitro_study,
       title_any: ["in vitro", "cell line", "cell culture", "hepg2", "体外"]}
    - {id: policy_title, type: policy_or_comment,
       title_any: ["editorial", "commentary", "guideline", "policy", "述评", "指南"]}
    - {id: method_title, type: methodology,
       title_any: ["analytical method", "method validation", "determination of",
                    "speciation", "检测方法"]}
    - {id: hbm_title, type: human_hbm_original,
       title_any: ["biomonitoring", "urinary", "blood", "cord blood",
                    "internal exposure", "生物监测", "内暴露", "尿砷", "血砷"],
       require_analyte: true}
    - {id: hbm_abstract, type: human_hbm_original,
       abstract_any: ["biomonitoring", "geometric mean", "urinary concentrations",
                      "biological monitoring"],
       require_analyte: true}
  exclusion_reasons: [not_human_study, wrong_analyte, no_hbm_data,
                      no_usable_values, wrong_population, wrong_year,
                      duplicate_cohort, review_or_comment, conference_abstract,
                      insufficient_info]
  fulltext_rules:
    human_sample_terms: ["participants", "subjects", "residents", "pregnant",
                         "children", "adults", "newborns", "volunteers",
                         "人群", "居民", "孕妇", "儿童", "受试者"]
    concentration_stat_terms: ["geometric mean", "arithmetic mean", "median",
                               "95% ci", "standard deviation", "ug/l", "μg/l",
                               "ng/ml", "μg/g", "ng/g", "几何均值", "中位数", "均值"]
"""


def load_fixture_spec() -> dict:
    """把夹具 YAML 写入临时文件并走正式 load_config 路径 → 校验后的 spec。"""
    tmp = Path(tempfile.mkdtemp(prefix="hbm_s2_fix_"))
    p = tmp / "fixture_project.yaml"
    p.write_text(FIXTURE_SCREENING_YAML.lstrip(), encoding="utf-8")
    _, spec = load_screening_config(p)
    return spec


# ===========================================================================
# 6. self-test
# ===========================================================================

def run_self_test() -> int:
    print("=" * 70)
    print("screening_schema.py --self-test（S2 单一来源）")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    def expect_error(name, fn, needle=""):
        try:
            fn()
            check(name, False, "未抛出！")
        except ConfigError as e:
            check(name, True, (str(e)[:44] + "…") if needle in str(e) else str(e)[:44])

    print("\n[1] 闭集枚举")
    check("状态机 8 态且含 uncertain", len(STATE_ENUM) == 8 and "uncertain" in STATE_ENUM)
    check("状态机终点是 included/excluded",
          STATE_ENUM[-2:] == ["included", "excluded"], str(STATE_ENUM[-2:]))
    check("题摘 AI 决策 3 值", set(AI_TA_DECISIONS) ==
          {"proceed_to_fulltext", "suggest_exclude", "uncertain"})
    check("全文 AI 决策含 include_candidate",
          "include_candidate" in AI_FT_DECISIONS)
    check("去重 AI 决策含 suspected_duplicate（不自动合并）",
          "suspected_duplicate" in DEDUP_AI_DECISIONS)
    check("所有枚举内无重复", all(len(x) == len(set(x)) for x in
          (STATE_ENUM, AI_TA_DECISIONS, AI_FT_DECISIONS,
           HUMAN_TA_DECISIONS, HUMAN_FT_DECISIONS, DEDUP_AI_DECISIONS)))

    print("\n[2] 夹具配置走正式加载路径")
    spec = load_fixture_spec()
    check("夹具加载成功且 9 个文章类型", len(spec["article_types"]) == 9)
    check("保留类型在册", {"other", "insufficient_info"} <= set(spec["article_types"]))
    check("type_decision_map 全覆盖",
          set(spec["type_decision_map"]) == set(spec["article_types"]))
    check("规则引擎 8 条且 id 唯一",
          len(spec["classification_rules"]) == 8
          and len({r["id"] for r in spec["classification_rules"]}) == 8)
    check("排除原因 10 项闭集", len(spec["exclusion_reasons"]) == 10)
    check("dedup 阈值 = 0.85", spec["dedup"]["title_similarity_threshold"] == 0.85)

    print("\n[3] 配置校验的显式拒绝")
    def mutate(transform):
        text = FIXTURE_SCREENING_YAML
        text = transform(text)
        tmp = Path(tempfile.mkdtemp(prefix="hbm_s2_bad_"))
        p = tmp / "bad.yaml"
        p.write_text(text.lstrip(), encoding="utf-8")
        load_screening_config(p)

    expect_error("缺 screening 区块 → ConfigError",
                 lambda: mutate(lambda t: t.split("screening:")[0]))
    expect_error("缺 dedup 阈值 → ConfigError",
                 lambda: mutate(lambda t: t.replace(
                     "    title_similarity_threshold: 0.85\n", "")))
    expect_error("阈值 > 1 → ConfigError",
                 lambda: mutate(lambda t: t.replace(
                     "title_similarity_threshold: 0.85",
                     "title_similarity_threshold: 1.5")))
    expect_error("type_decision_map 漏类型 → ConfigError",
                 lambda: mutate(lambda t: t.replace(
                     '    methodology: "uncertain"\n', "")))
    expect_error("规则 type 越界 → ConfigError",
                 lambda: mutate(lambda t: t.replace(
                     "id: policy_title, type: policy_or_comment",
                     "id: policy_title, type: politics")))
    expect_error("规则词表全空 → ConfigError",
                 lambda: mutate(lambda t: t.replace(
                     'title_any: ["editorial", "commentary", "guideline", "policy", "述评", "指南"]',
                     "title_any: []")))
    expect_error("缺保留排除编码 → ConfigError",
                 lambda: mutate(lambda t: t.replace(
                     "conference_abstract,\n                      insufficient_info",
                     "conference_abstract")))

    print("\n[4] DOI / PMID / 作者规范化")
    check("URL 形态 DOI 归一", normalize_doi("https://doi.org/10.1234/ABC.def") ==
          "10.1234/abc.def")
    check("doi: 前缀剥离", normalize_doi("doi: 10.1234/x") == "10.1234/x")
    check("尾标点剥离", normalize_doi("10.1234/x.") == "10.1234/x")
    check("形态异常原样保留（不猜）", normalize_doi("not-a-doi") == "not-a-doi")
    check("空 → 空串", normalize_doi("") == "")
    check("纯数字 PMID 保留", normalize_pmid("32456789") == "32456789")
    check("非数字 PMID 原样保留", normalize_pmid("PMID:32456789") == "PMID:32456789")
    check("first_author（分号分隔）", first_author("Zhang, San; Li, Si") == "Zhang, San")
    check("first_author（and 分隔）", first_author("Zhang, San and Li, Si") == "Zhang, San")
    check("first_author（单作者）", first_author("Wang, Wu") == "Wang, Wu")

    print("\n[5] 标题相似度（Jaccard bigram）")
    check("完全相同 → 1.0",
          jaccard_bigram("Urinary arsenic in adults",
                         "Urinary arsenic in adults") == 1.0)
    check("大小写与标点不敏感",
          jaccard_bigram("Urinary Arsenic, in Adults!",
                         "urinary arsenic in adults") == 1.0)
    check("完全无关 → 0.0",
          jaccard_bigram("Blood mercury in parturients",
                         "Dietary cadmium from rice") == 0.0)
    partial = jaccard_bigram("Urinary arsenic in general population of China",
                             "Urinary arsenic in general population of Japan")
    check("部分相似落在 (0,1)", 0 < partial < 1, f"{partial:.3f}")
    check("空标题 vs 有标题 → 0.0", jaccard_bigram("", "some title") == 0.0)

    print("\n[6] CSV 读写往返")
    import tempfile as _tf
    tmp = Path(_tf.mkdtemp(prefix="hbm_s2_csv_"))
    rows = [{"record_id": "a-00001", "ai_article_type": "review"},
            {"record_id": "a-00002", "ai_article_type": "other"}]
    p = tmp / "ta.csv"
    write_csv_dicts(p, TA_FIELDS, rows)
    back = read_csv_dicts(p)
    check("往返行数一致", len(back) == 2)
    check("缺失列补空串", back[1]["ai_confidence"] == "")
    check("保留字段值", back[0]["ai_article_type"] == "review")

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
        prog="screening_schema.py",
        description="S2 筛选质控的单一来源（枚举/配置校验/相似度）（HBM-Meta-Agent / S2）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return run_self_test()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
