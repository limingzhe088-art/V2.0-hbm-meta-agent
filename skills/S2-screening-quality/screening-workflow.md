# 筛选工作流方法学 — AI 预处理 + 人工闸门

> **配套**：`{skill_root}/SKILL.md`、`shared/quality-gates.md`（GATE-S2a/b/c + GATE-2）、`shared/data-contract.md` §3.4
> **哲学**：AI 做 70%–90% 的机械筛选劳动；纳入/排除的最终决定权永远在人手中。

---

## 1. 为什么"分类"与"纳入决定"必须分字段

一篇文章可以同时是"综述"（不进定量合并）与"背景文献"（保留引用）；
"动物实验"对人群 HBM 合并是排除项，对机制讨论是有用材料。因此：

| 字段 | 含义 | 谁写 |
|---|---|---|
| `ai_article_type` | 文章类型（9 类闭集） | AI（规则引擎） |
| `ai_screening_decision` | AI 建议（proceed/suggest_exclude/uncertain） | AI |
| `human_decision` | 人的裁决 | **人**（AI 初始留空） |
| `human_reason_code` | 人的排除原因（闭集） | **人** |

混用这两层（例如 AI 直接写 `exclude`）会让"排除原因"失去可审计性——
审稿人问"为什么排除了 X"时，答案必须是**人的领域判断**，不是"关键词命中"。

---

## 2. S2-A 去重

**三级策略与边界**

| 级 | 匹配方式 | AI 行为 | dedup_unique.csv |
|---|---|---|---|
| L1 | DOI 精确（normalize 后） | 标 `duplicate`/`keep` | **剔除**（人工可在 decisions 翻案） |
| L2 | PMID 精确 | 同上 | 同上 |
| L3 | 标题词 bigram Jaccard ≥ 阈值 × 第一作者 × 年份 | **一律 `suspected_duplicate`** | **保留**（绝不自动合并） |

**为什么模糊命中永不自动合并**：并查集的传递性会把 A≈B、B≈C 串成 A≈C；
在文献去重里这种"链式等同"经常出错（同名综述的不同卷期、同一方法学的系列论文）。
把自动性只授予强标识（DOI/PMID），把弱标识（标题相似）留给人工，是可审计性与
召回率的平衡点。

**人工复核重点**（GATE-S2b 的一部分）：
- `suspected_duplicate` 组：确认是否同一研究；是 → 指定 `cohort_id`（契约 §3.2）；
- AI 保留建议（`kept_record_id`）仅按"有摘要 → 字段满 → record_id 序"启发，人工可推翻。

**同队列重复 ≠ 题录重复**：同一出生队列的 35 篇论文题录各不相同，题录去重抓不到——
那是 S6 `dedup_screen.py`（GATE-2/GATE-5）的职责。两层缺一不可。

---

## 3. S2-B 题目+摘要初筛

**规则引擎**（`screening.classification_rules`，从 project.yaml 读取）：
- 有序规则，自上而下首中即判 → 规则顺序本身是方法学决策，改动须留痕；
- `require_analyte: true` 抑制泛词误判（"urinary biomonitoring" 必须伴随目标分析物词）；
- 置信度仅为复核排序提示（title 命中 high / abstract 命中 medium / 退路 low），
  **不是统计量**，不得写入稿件。

**人工初筛纪律**（GATE-S2b）：
1. AI 建议纳入（proceed_to_fulltext）：逐条确认；
2. AI 建议排除（suggest_exclude）：**也要复核**——综述/方法学类最易误分；
3. uncertain：全部人工判断，不得攒着不填（脚本检查点会拒绝空值）；
4. 若人工发现系统性误分类 → 修改 `classification_rules`（bump config_version）并复跑。

---

## 4. S2-C 全文筛选

**输入形式**：全文文件按 `{record_id}.pdf / .html / .txt` 命名放入 `--fulltext-dir`；
PDF 解析需 PyMuPDF（`requirements.txt` 条件必需组；缺库且有 PDF 待处理 → 显式报错）。

**判定四问**（词表来自 `screening.fulltext_rules`）：
1. 是否真的有人体样本（human_sample_terms）；
2. 是否测了目标分析物（analyte_terms 命中留痕到 `analytes_reported`）；
3. 是否报告可用浓度统计量（geometric mean / median / μg/L…）；
4. 采样/发表年份是否落在 `period_scheme` 覆盖区间
   （全文**无年份**时不判 wrong_year——交给人工，不猜）。

**排除原因闭集**（`screening.exclusion_reasons`；判定标准见
`references/exclusion_reasons.md`）：`not_human_study / wrong_analyte / no_hbm_data /
no_usable_values / wrong_population / wrong_year / duplicate_cohort /
review_or_comment / conference_abstract / insufficient_info`。

**全文不可得**：标 `insufficient_info` + uncertain，绝不因此自动排除（全文不可得
≠ 不符合纳入标准；PRISMA 需要单独报告 "reports not retrieved"）。

---

## 5. 收口：清单与 PRISMA

- `included_studies.csv`：`study_id`（ST-xxxx）→ record 级唯一；country/population/
  matrix/analyte 留空——这四列是 **S3 提取的产出**，此处占位以衔接主表，不做猜测。
- `screening_log.csv`：PRISMA 各阶段数字；`generate_prisma.py` 做恒等式校验
  （identification / screening / retrieval / included 四条账必须对上），对不上不产出流程图。
- **boundary 提醒**：S2 收口 ≠ GATE-2。人群边界四类标记（`flag_occupational` /
  `flag_disease` / `flag_endemic_area` / `flag_mixed_occupational`）的逐条裁决
  在 S3 前进行——若 S2 词表没有覆盖某类边界人群，GATE-2 是最后一道兜底。

---

## 6. 与其他子技能的衔接

| 交接 | 上游 → 下游 | 校验 |
|---|---|---|
| S1 → S2 | all_raw_records.csv | 表头与 RECORD_FIELDS 一致（硬错误） |
| S2 → S3 | included_studies.csv + 边界标记记录 | GATE-2 通过；无 Pending 的 inclusion_decision |
| S2 → S6 | excluded_studies.csv（留存记录） | 审稿人质疑漏筛时可回溯 AI 证据 |
