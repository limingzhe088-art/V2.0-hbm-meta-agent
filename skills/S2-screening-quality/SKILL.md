---
name: hbm-screening-quality
description: "AI-assisted screening and quality workflow for human biomonitoring meta-analyses: normalizes and deduplicates database exports (exact DOI/PMID matching plus word-bigram Jaccard fuzzy title clustering with a configuration-driven threshold), classifies records into a closed article-type set (human HBM original, human non-HBM, review, animal, in vitro, methodology, policy/comment, other, insufficient info) via an extensible keyword rule engine read from project.yaml, performs full-text verification with human-sample/analyte/concentration-statistic/year checks and a closed exclusion-reason code set, then produces human-confirmed included and excluded study lists with a PRISMA 2020 flow diagram whose numbers are balance-checked. Every AI output is a suggestion only: human_decision columns stay empty until a person fills them, excluded records are flagged never deleted, and three human gates (raw-data acceptance, title/abstract confirmation, full-text confirmation) gate the transitions. Triggers on: screening, deduplication, title abstract screening, full text screening, article classification, inclusion criteria, exclusion reasons, PRISMA flow, included studies list, suspect duplicates, 文献筛选, 去重, 题目摘要初筛, 全文筛选, 文章分类, 纳入排除, 排除原因, 纳入清单, PRISMA 流程图, 疑似重复, 人工闸门."
license: MIT (code) / CC-BY-4.0 (docs)
compatibility: opencode claude-code dsh
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash
  - Task
  - TodoWrite
  - AskUserQuestion
metadata:
  parent_skill: hbm-meta
  version: "1.0.0"
  last_updated: "2026-09-26"
  status: active
  stage: 2
  gate: "GATE-S2a/S2b/S2c（阶段内人工确认点）+ GATE-2（人群边界裁决，见 shared/quality-gates.md）"
  output_gate: "GATE-2 通过后方可进入 S3 提取"
  contract_version: "1.0.0"
  task_type: open-ended
---

# S2 · Screening & Quality — AI 预处理 + 人工闸门筛选

**一句话**：AI 做去重、分类、初筛建议与证据整理（**70%–90% 的机械劳动**），人做最终纳入/排除与清单确认——**分类不等于裁决，AI 永不写 human_decision**。

---

## 1. 加载条件与前置依赖

| 项 | 要求 |
|---|---|
| **前置闸门** | **GATE-1 必须为 `passed`**（检索充分性已由人确认） |
| 前置产物 | `all_raw_records.csv`（S1 `merge_sources.py` 输出）+ `source_summary.csv` |
| 配置前置 | `project.yaml` 含 `screening` 区块（**条件必需**，缺失即硬错误） |
| 负责人 | **A2 `screening-agent`**（权限 L1：只读 + 新建报告；不得写 `inclusion_decision` 实体结论，只可写 `Pending`） |
| 产出目录 | `02-screening/` |
| 人工闸门 | **GATE-S2a / GATE-S2b / GATE-S2c**（阶段内确认点）→ **GATE-2**（人群边界裁决，契约级） |

---

## 2. 三阶段流水线与记录状态机

```
S2-A 去重            S2-B 题摘初筛            S2-C 全文筛            收口
normalize → dedup → classify → (人工S2b) → fulltext → (人工S2c) → included/excluded 清单
                                                                      + PRISMA 流程图
raw → deduplicated → title_abstract_reviewed → fulltext_retrieved
    → fulltext_reviewed → included / excluded（uncertain 为中间态）
```

**状态机铁律**：被排除的记录**不物理删除**，只标记 `excluded`（可随时审计 AI 是否漏筛）。

---

## 3. 脚本清单（均含 `--self-test`）

| 阶段 | 脚本 | 输入 → 输出 |
|---|---|---|
| A | `normalize_records.py` | all_raw_records.csv → `normalized_records.csv`（清洗 + 哈希重算） |
| A | `dedup_records.py` | normalized → `dedup_decisions.csv` + `dedup_unique.csv` + `dedup_summary.csv` |
| B | `classify_articles.py` | dedup_unique → `title_abstract_screening.csv` |
| B | `screening_summary.py` | title_abstract_screening → `screening_summary.csv` |
| C | `fulltext_screening.py` | title_abstract_screening（人工已裁决）+ 全文目录 → `fulltext_screening.csv` |
| 收口 | `generate_inclusion_list.py` | fulltext_screening（人工终裁）→ `included_studies.csv` + `excluded_studies.csv` + `screening_log.csv` |
| 收口 | `generate_prisma.py` | screening_log → `prisma_flow.md`（数字自洽校验） |
| 共享 | `screening_schema.py` | 闭集枚举 / 配置校验 / 相似度算法的**单一来源** |

### 关键命令

```bash
# S2-A
python skills/S2-screening-quality/scripts/normalize_records.py \
    --input all_raw_records.csv --output normalized_records.csv
python skills/S2-screening-quality/scripts/dedup_records.py \
    --input normalized_records.csv --config project.yaml --output-dir 02-screening/

# S2-B
python skills/S2-screening-quality/scripts/classify_articles.py \
    --input dedup_unique.csv --config project.yaml \
    --output 02-screening/title_abstract_screening.csv
# ★ 人工逐条填写 human_decision / human_reason_code（GATE-S2b）
python skills/S2-screening-quality/scripts/screening_summary.py \
    --input title_abstract_screening.csv --config project.yaml \
    --output 02-screening/screening_summary.csv

# S2-C（全文文件命名为 {record_id}.pdf/.html/.txt；PDF 需 PyMuPDF）
python skills/S2-screening-quality/scripts/fulltext_screening.py \
    --input title_abstract_screening.csv --records dedup_unique.csv \
    --fulltext-dir fulltexts/ --config project.yaml \
    --output 02-screening/fulltext_screening.csv
# ★ 人工终裁 human_final_decision（GATE-S2c）

# 收口
python skills/S2-screening-quality/scripts/generate_inclusion_list.py \
    --input fulltext_screening.csv --records dedup_unique.csv \
    --ta-screening title_abstract_screening.csv --config project.yaml \
    --output-dir 02-screening/
python skills/S2-screening-quality/scripts/generate_prisma.py \
    --input screening_log.csv --config project.yaml \
    --output 02-screening/prisma_flow.md
```

---

## 4. AI 与人的分工（本子技能的核心契约）

| 环节 | AI 做（机械劳动） | 人做（领域裁决） |
|---|---|---|
| 去重 | DOI/PMID 精确匹配；标题 Jaccard 聚簇；保留建议 | 翻案任何 AI 建议（human_decision）；裁决 suspected_duplicate |
| 分类 | 按 `screening.classification_rules` 打 `ai_article_type` | 修正误分类；决定 AI 建议排除的记录是否真排除 |
| 题摘初筛 | 三值建议：proceed_to_fulltext / suggest_exclude / uncertain | **AI 建议排除的记录也要复核**（漏筛检查），填 human_decision |
| 全文筛 | 证据表：人体样本/分析物/统计量/年份命中 + 排除编码 | 终裁 include / exclude + 排除原因（GATE-S2c） |
| 清单 | 汇总生成 included/excluded/screening_log | 确认清单；在 `_state/gates.json` 开启闸门 |

**两条高频提醒**
1. AI `suggest_exclude` ≠ 排除：综述/动物/方法学类文献若可能含可用亚组数据，人工应回捞。
2. `uncertain` 是 AI 的诚实退路（信息不足/全文不可得），必须由人处置，不得被 AI 兜成 exclude。

---

## 5. 闭集与配置（全部来自 `project.yaml → screening`）

| 配置键 | 内容 |
|---|---|
| `dedup.title_similarity_threshold` | 模糊去重 Jaccard 阈值（模板 0.85；代码无默认值） |
| `dedup.author_match_required` / `year_tolerance` | 模糊聚簇的第一作者/年份约束 |
| `article_types` | 9 类文章类型闭集（含保留类型 other / insufficient_info） |
| `classification_rules` | 有序关键词规则（id/type/title_any/abstract_any/require_analyte） |
| `type_decision_map` | 文章类型 → 题摘 AI 建议的显式映射（必须全覆盖） |
| `exclusion_reasons` | 全文排除原因编码闭集（含保留编码 insufficient_info） |
| `fulltext_rules` | 人体样本词表 / 浓度统计量词表 |

改这些键 = 改筛选口径：须 bump `config_version` 并复跑 S2 全流程（stale 回滚机制，routing.md §3.4）。

---

## 6. 人工闸门（详见 `shared/quality-gates.md` §S2）

| 闸门 | 时机 | 脚本检查点（不满足 → 退出码 2） |
|---|---|---|
| **GATE-S2a** 原始数据验收 | normalize 之前 | `all_raw_records.csv` 存在且非空；`source_summary.csv` 记录完整（S1 已产） |
| **GATE-S2b** 题摘筛选后确认 | classify 之后、fulltext 之前 | `title_abstract_screening.csv` 的 human_decision 无空值（`fulltext_screening.py` 强制） |
| **GATE-S2c** 全文筛选后确认 | fulltext 之后、清单确认 | `human_final_decision` 全填且无 uncertain；exclude 原因在闭集内（`generate_inclusion_list.py` 强制）；`included_studies.csv` 与 `screening_log.csv` 数字一致（`generate_prisma.py` 恒等式校验） |

> 与 **GATE-2**（人群边界裁决）的关系：S2c 确认的是"筛选流程走完且人拍板"；
> GATE-2 是契约级裁决门（`flag_occupational` 等四类边界标记 + `inclusion_decision`），
> 在 S3 提取前由人工对边界人群记录逐条裁决。两者都通过才启动 A3。

---

## 7. 方法学文档

| 文档 | 内容 |
|---|---|
| `screening-workflow.md` | 三阶段方法学：去重策略、分类规则设计、初筛复核要求、PRISMA 对应关系 |
| `references/exclusion_reasons.md` | 排除原因编码的判定标准与易错案例 |

## 8. 自检

```bash
python skills/S2-screening-quality/scripts/screening_schema.py --self-test
python skills/S2-screening-quality/scripts/normalize_records.py --self-test
python skills/S2-screening-quality/scripts/dedup_records.py --self-test
python skills/S2-screening-quality/scripts/classify_articles.py --self-test
python skills/S2-screening-quality/scripts/screening_summary.py --self-test
python skills/S2-screening-quality/scripts/fulltext_screening.py --self-test
python skills/S2-screening-quality/scripts/generate_inclusion_list.py --self-test
python skills/S2-screening-quality/scripts/generate_prisma.py --self-test
```

输出 CSV 模板（`templates/` 下 8 份）由 `skills/S5-weighted-pooling-edi/scripts/gen_templates.py` 生成 —— **禁止手写**（`--check` 进 CI）。
