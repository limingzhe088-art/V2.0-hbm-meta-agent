# Quality Gates — 人工闸门定义（GATE-0 … GATE-5）

> **版本**：`1.0.0`　**状态**：冻结　**配套**：`shared/data-contract.md`、`shared/human-verification-protocol.md`

---

## 0. 为什么要设闸门

人体内暴露 Meta 分析有一个不可回避的结构性风险：**AI 能极大加速提取与计算，但"这个研究该不该纳入""这个数是不是真的"两个判断只有领域研究者能做。** 本项目（砷，384 篇）的实证是：

- AI 初提**必漏字段**（时间、尿校正状态、血基质），必须逐条校对；
- 4 类"疑似违规研究"（职业暴露 35 篇、疾病人群 55 篇、血清/血浆 27 篇、同队列重复）**AI 只能列证据，不能下结论**；
- 若把这两件事交给 AI 自动完成，错误会以"看起来完全合理"的形式静默流入合并结果，且不可追溯。

因此本工作包把流程设计成 **AI 产出 → 人工裁决 → 记录留痕** 的六道闸门。

### 核心原则

| 原则 | 含义 |
|---|---|
| **AI 只产出"疑似/建议/差异"** | AI 不得写 `Exclude`、不得改 `gm_summary`、不得定稿正文数字 |
| **人工只做"裁决"** | 人为每个待决项给出 `Include/Exclude`、`接受/拒绝`、`定稿/重算`，不做批量录入 |
| **未过闸门不得进入下游** | 硬阻断；下游脚本检测到 `gate_status != passed` 直接返回退出码 2 |
| **裁决必须留痕** | 每个裁决写 `decision_log.csv`（谁、何时、依据、涉及 `record_id`） |
| **决策顺序不可颠倒** | 数据真实性 → 人群边界 → 合并合理性（见 `agent/decision-order.md`） |

### 闸门总览

| 闸门 | 名称 | 类型 | 阻断级别 | 未通过后果 |
|---|---|---|---|---|
| GATE-0 | 立项与口径冻结 | 人工设定 | 硬 | 无法启动任何分析 |
| GATE-1 | 检索充分性 | 人工确认 | 硬 | 不得进入筛选 |
| GATE-S2a | S2 原始数据验收 | 人工确认 | 硬 | 不得开始去重与初筛 |
| GATE-S2b | S2 题摘筛选后确认 | 人工确认 | 硬 | 不得进入全文筛 |
| GATE-S2c | S2 全文筛选后确认 | 人工确认 | 硬 | 不得产出正式纳入清单 |
| GATE-2 | 人群边界裁决 | **领域知识** | 硬 | 不得进入标准化 |
| GATE-3 | 数据真实性复核 | **领域知识** | 硬 | 主表不得冻结 |
| GATE-4 | 标准化口径确认 | 人工确认 | 硬 | 不得进入合并 |
| GATE-5 | 去重与敏感性裁决 | **领域知识** | 软（可记录为"未执行"但必须写明） | 结论稳健性不可声明 |
| （贯穿） | 数值审计 | 自动 + 人工确认 | 硬 | 稿件不得定稿 |

---

## GATE-0 · 立项与口径冻结

**触发条件**：项目初始化，`templates/project-scaffold` 已复制、`project.yaml` 已生成。

**AI 产出物**
- `project.yaml` 草案（含 `period_scheme`、`region_map`、`unit_policy`、`edi` 参数）
- `00_charter.md`：PICO / 暴露物 / 基质范围 / 时空范围 / 预期产出清单
- 冲突预判清单：该暴露物已知的**单位陷阱、基质陷阱、人群陷阱**（从 `shared/glossary.md` 与领域知识库匹配）

**人工裁决项（必须逐项确认）**
1. 暴露物与 `analyte` 取值；基质范围（默认 `Urine/Blood/CordBlood`，是否扩展 `BreastMilk/Nail/Hair`）
2. **分期方案边界**——一旦确认即 `freeze_date` 冻结
3. 地区字典（省级全称写法、大区归属）
4. 单位口径（尿 → `ug/g Cr`？血/脐血 → `ug/L`？）
5. EDI 是否纳入；若纳入，`ABS`、`Vd`、`tau`、体重假设是否接受
6. 权重口径：样本量加权 vs 逆方差

**未通过信号**：`project.yaml` 缺 `period_scheme.freeze_date`、或 `unit_policy` 未指定任一基质目标单位。

**留痕**：`_state/decision_log.csv` 增 6 行；`00_charter.md` 末尾由人签名式标注确认时间。

---

## GATE-1 · 检索充分性

**触发条件**：S1 完成四个以上数据库检索，`01-search/检索记录.csv` 已产出。

**AI 产出物**
- `检索记录.csv`：库 / 检索式原文 / 检索日期 / 命中数
- `检索策略.md`：每个库的语法说明、排除词银行的四类分组、命中量级诊断
- 检索充分性诊断：命中数是否落在该领域合理区间；哪些排除词可能过度杀伤

**人工裁决项**
1. 检索式是否覆盖足够（是否需要增补库：Scopus / Embase / 学位论文库）
2. 排除词是否过度排除（尤其 `NOT occupational` 会连带排除混合人群研究）
3. 是否接受"未预先注册"的诚实声明（若注册过 PROSPERO，登记编号）
4. 命中数是否支持后续工作量（如 > 8000 条需先缩小范围）

**未通过信号**：任一数据库无检索式原文留存；命中数为 0 或异常巨大（> 50000）。

---

## GATE-2 · 人群边界裁决　【领域知识闸门】

**触发条件**：S2 完成题摘筛 + 全文筛，`flag_scan` 与 `dedup_screen` 产出疑似清单。

**AI 产出物**（**只有清单，没有结论**）
| 产出 | 内容 | 本项目实测规模 |
|---|---|---|
| `疑似职业暴露清单` | 标题含职业关键词且无 `non-occupational` 否定词 | 35 个唯一研究 / 41 条记录 |
| `疑似疾病人群清单` | 病例对照、特定疾病、地方病病区 | 55 个唯一研究 |
| `疑似混合人群清单` | 职业与非职业混合，需查原文是否可仅取非职业亚组 | 5 个研究 |
| `疑似血基质清单` | 标题提示 `serum`/`plasma`/`血清`/`血浆` | 41 条记录 / 27 个研究 |
| `同队列/同城聚簇清单` | 同城同基质 ≥4 篇的聚簇，标注可能同队列 | 21 个簇（武汉尿 35 篇最大） |
| `质量评分表` | AHRQ 11 条逐条得分 + 分级（高/中/低） | 高 125 / 中 247 / 低 12 |

**人工裁决项（逐条，不可批量）**
1. 每个疑似职业暴露研究 → `Include` / `Exclude`（依据：原文是否报告非职业亚组）
2. 每个疑似疾病人群研究 → 浓度是否来自一般队列而非病例组
3. `Serum`/`Plasma` 记录 → 保留（并打 `flag_blood_matrix`）或排除
4. 同队列聚簇 → 指定 `cohort_id`；决定是否"同队列同分期只保留样本量最大者"
5. 低质量研究（0–4 分）→ 排除 / 纳入并做敏感性分析

**写入字段**（仅人工可写）：`inclusion_decision`、`exclusion_reason`、`cohort_id`、`dedup_group`

**未通过信号**：任一 `inclusion_decision=Pending`；或 `Exclude` 但 `exclusion_reason` 为空。

**产出文档**：`02-screening/03_去重与纳入标准筛查报告.md`（列出 AI 证据 + 人工结论 + 依据）

> **反模式警告**：本项目审查意见 R1-5/R2-7 正是审稿人要求补这一门。若跳过 GATE-2，"36 万参与者"这一数字会被指出为重复计数，"一般人群"这一表述会被指出不成立。

---

## GATE-3 · 数据真实性复核　【领域知识闸门】

**触发条件**：S3 完成 AI 初提 + 自动校验，`validate_table.py` 无 `ERROR`。

**AI 产出物**
- 主表草案（每条 `extracted_by=AI`、`verified=FALSE`）
- `缺字段清单`：哪条记录的哪个必填/条件必填字段为空
- `可疑值清单`：超量级、单位疑似错、GM 与 AM 关系反常（GM > AM 且差异 > 50%）
- `推断标记汇总`：§6 字典中每个标记命中的记录数
- `待人工逐条核对任务表`：按记录分组的核对 checklist

**人工裁决项（逐条，AI 不得代劳）**
1. 补全 AI 漏提字段，特别是：`time`（采样年，不是发表年）、`adjusted`（尿校正状态）、`blood_matrix`、`stat_type`、`sample_size`、`unit_original`
2. 判定可疑值为"原文如此"还是"提取错误"
3. 确认每条 `inferred_flags` 是否恰当（该标未标 = 违规）
4. 对无法确定的值：**留空 + `notes` 写明**，禁止"补一个合理值"
5. 逐条把 `extracted_by` 改为 `AI+Human`、`verified=TRUE`

**写入字段**：全部字段（人工修订）+ `verified`、`verified_by`

**未通过信号**：`verified=TRUE` 比例 < 100%（进入 S5 前要求 100%；进入 S4 允许 ≥ 90% 但未验证记录不得用于主结论）。

**产出文档**：`03-extraction/校对日志.md`（列明每条被修改的字段、原值、新值、修改依据）

> **本项目实证**：未做逐条校对的记录里，`time` 缺失与"用发表年份替代采样年份"是最常见错误；该错误会同时污染时间趋势与分期结果，且难以事后发现。

---

## GATE-4 · 标准化口径确认

**触发条件**：S4 完成单位与统计量换算，`conversion_path` 全覆盖，主表可冻结。

**AI 产出物**
- `换算痕迹表`：每条记录的原值/原单位/换算路径/参数/结果
- `换算路径分布`：各 `stat_type → GM` 路径的记录数（如 `AM_SD→Wan_S1→GM: 210 条`）
- `尿校正状态分布`：`Creatinine` / `SpecificGravity` / `No` / `ReferenceConversion` 各多少条
- `敏感性影响预判`：哪些分层（校正方式、直接 GM 子集）样本量足够做敏感性分析

**人工裁决项**
1. 尿校正优先规则是否符合本领域（同一研究多口径时取哪个）
2. 血基质假定是否接受（未说明者假定全血 + 标记）
3. 形态假定是否接受（总砷 vs iAs）
4. 换算后量级是否合理（对比已知文献区间，如尿砷 μg/g Cr 个位数到几十）
5. 是否批准主表冻结 → 生成 `snapshot_tag` 与 `row_hash`

**未通过信号**：存在 `conversion_path` 为空但有 `gm_summary` 的记录；或 `inferred_flags` 为空却使用了 Wan 公式。

---

## GATE-5 · 去重与敏感性裁决　【领域知识闸门】

**触发条件**：S5 完成主分析，S6 完成去重筛查与敏感性分析。

**AI 产出物**
- `敏感性分析结果`：① 按尿校正方式分层；② 仅直接报告 GM 子集；③ 排除职业/疾病人群后
- `主形态一致性判定`：三套结果的主趋势形态是否一致（如"下降—回升—稳定"）
- `重复计数影响量估计`：若按 `dedup_group` 去重，参与者总数变化多少
- `数值审计对照表`：手稿每个数字 vs 快照重算值

**人工裁决项**
1. 是否执行去重策略；若执行，采用"保留样本量最大者"还是"合并"
2. 敏感性分析中哪些子集因样本量不足而**不得**用于结论
3. 若排除后主结论改变 → 决定如何改写正文叙述（而非掩盖）
4. 批准"参与者总数"的措辞（`record-level sums` / `participant records`）
5. 是否声明"未预先注册"

**未通过信号**：结论稳健性被写入正文，但敏感性分析未执行或未留痕。

**产出文档**：`06-qc-audit/{dedup_report.md, sensitivity_report.md, number_audit.md}`

---

## 贯穿闸门 · 数值审计（Number Audit）

**触发条件**：每次稿件数值变更、每次快照更新、投稿前、返修时。

**AI 产出物**
- `数值审计对照表`：`手稿原值 | 快照重算值 | 差值 | 状态 | 连带需改的叙述`
- 状态枚举：`OK` / `STALE_MANUSCRIPT`（快照哈希不匹配）/ `MISMATCH`（值不同）/ `NO_SOURCE`（追溯不到来源）
- 自动定位需连带修改的句子（段落 + 原句）

**人工裁决项**
1. 每个 `MISMATCH` → 采信重算值还是修正换算（**AI 不得自行决定**）
2. 每个 `NO_SOURCE` → 补来源或删除该数字
3. 批准"全部 `OK`"后方可定稿

**硬规则**：**正文中不允许出现无法追溯的"孤儿数字"。** 这是本项目审稿意见 R1-3（r=0.069 与 P 不匹配）、R2-2（时段 GM 过时）、R3-1（分期不统一）三条的共同根因。

**产出文档**：`06-qc-audit/02_数值审计对照表.md`

---

## S2 阶段闸门（GATE-S2a / GATE-S2b / GATE-S2c）　★ v2 新增

> **定位**：S2（AI 预处理 + 人工闸门筛选，`skills/S2-screening-quality/`）内部的三个
> 人工确认点。它们不取代 **GATE-2**（人群边界裁决，契约级领域知识闸门）——S2c 确认
> "筛选流程走完且人拍板"，GATE-2 在 S3 提取前对边界人群记录逐条裁决；两者都通过
> 才能启动 A3。三个闸门的**脚本检查点由 S2 脚本强制执行**（不满足 → 退出码 2），
> 但 `passed` 状态仍只能由人写入 `_state/gates.json`（权限铁律不变）。

### GATE-S2a · 原始数据验收

**触发条件**：S1 完成，`all_raw_records.csv` 已合并。
**AI 产出物**：`all_raw_records.csv` + `source_summary.csv`（检索式/命中数由人工回填）。
**脚本验证命令**（检查点；非零退出码 = 未过）

```bash
# 表头与 record_id 唯一性校验 + 字段完整性（GATE-S2a 的机器检查点）
python skills/S2-screening-quality/scripts/normalize_records.py \
    --input 01-search/all_raw_records.csv \
    --output 02-screening/normalized_records.csv --validate
```

**人工确认清单**
- [ ] 数据库检索已完成（各库检索式原文 + 日期 + 命中数齐备）
- [ ] 导出文件完整（hit_count vs exported_count 差异可解释，已写入 note）
- [ ] 各库导出格式与字段集已登记（含摘要字段）
- [ ] 未预先注册的诚实声明 / PROSPERO 编号已确认

**未通过信号**：任一库缺检索式原文；命中数与导出数差异无法解释。

### GATE-S2b · 题目摘要筛选后确认

**触发条件**：S2-A 去重 + S2-B 分类完成，`title_abstract_screening.csv` 已产出。
**AI 产出物**（只有建议与证据，没有结论）：`dedup_decisions.csv`（AI 保留建议 +
疑似重复清单）、`title_abstract_screening.csv`（9 类文章类型 + 三值建议 +
规则 id 与命中证据）、`screening_summary.csv`。
**脚本验证命令**（检查点；`fulltext_screening.py` 启动时强制）

```bash
# 汇总并显示 pending（human_decision 空值）计数
python skills/S2-screening-quality/scripts/screening_summary.py \
    --input 02-screening/title_abstract_screening.csv \
    --config project.yaml --output 02-screening/screening_summary.csv

# ★ 硬检查点：human_decision 有空值 → 退出码 2，拒绝进入全文筛
python skills/S2-screening-quality/scripts/fulltext_screening.py \
    --input 02-screening/title_abstract_screening.csv \
    --records 02-screening/dedup_unique.csv --fulltext-dir 02-screening/fulltexts/ \
    --config project.yaml --output 02-screening/fulltext_screening.csv
```

**人工确认清单**
- [ ] 去重结果可接受（翻案了需要翻案的 `duplicate`/`suspected_duplicate` 建议）
- [ ] 进入全文的文献集可接受（`human_decision = proceed_to_fulltext` 已逐条确认）
- [ ] **AI 建议排除的记录也已复核**（漏筛检查；综述/方法学类重点抽查）
- [ ] `uncertain` 记录全部处置（不得攒着不填）
- [ ] 同队列聚簇已指定 `cohort_id`（与 GATE-2 衔接）

**写入字段**（仅人工可写）：`human_decision`、`human_reason_code`、`cohort_id`。
**未通过信号**：human_decision 存在空值；AI 排除记录未复核即放行。

### GATE-S2c · 全文筛选后确认

**触发条件**：S2-C 全文筛完成，`fulltext_screening.csv` 已产出。
**AI 产出物**：全文证据表（人体样本/分析物/统计量/年份命中）+ 三值建议 +
排除编码建议（闭集）+ `fulltext_unavailable` 清单。
**脚本验证命令**（检查点；越权/漏项 → 退出码 2，数字不平衡 → 退出码 1）

```bash
# ★ 硬检查点：human_final_decision 全填、无 uncertain、
#   排除编码与 meta_disposition 在闭集内
python skills/S2-screening-quality/scripts/generate_inclusion_list.py \
    --input 02-screening/fulltext_screening.csv \
    --records 02-screening/dedup_unique.csv \
    --ta-screening 02-screening/title_abstract_screening.csv \
    --config project.yaml --output-dir 02-screening/

# ★ PRISMA 四条恒等式校验（不平衡 → 退出码 1，不产出流程图）
python skills/S2-screening-quality/scripts/generate_prisma.py \
    --input 02-screening/screening_log.csv --config project.yaml \
    --output 02-screening/prisma_flow.md
```

**人工确认清单**
- [ ] 最终纳入清单（included_studies.csv）逐条确认
- [ ] 最终排除清单 + 排除原因（闭集内）逐条确认
- [ ] `uncertain` / `fulltext_unavailable` 记录全部处置
- [ ] 重复研究处理规则已定（`cohort_id`；与 GATE-2/GATE-5 去重策略一致）
- [ ] 背景文献与定量分析文献已分离（`meta_disposition` 已填）
- [ ] screening_log 数字与两份清单一致（PRISMA 对账通过）

**写入字段**（仅人工可写）：`human_final_decision`、`human_final_reason_code`、
`meta_disposition`。
**未通过信号**：清单中混入未裁决记录；screening_log 数字对不上账。

**通过后的状态标记**（`_state/gates.json`，由主 Agent 在人工明确确认后写入；
子 Agent 无权写入——权限铁律 §2.4 不变）：

```json
{
  "GATE-S2a": {"status": "passed", "by": "human", "at": "...",
               "artifacts": ["01-search/all_raw_records.csv", "01-search/source_summary.csv"]},
  "GATE-S2b": {"status": "passed", "by": "human", "at": "...", "decisions": 130,
               "artifacts": ["02-screening/title_abstract_screening.csv",
                              "02-screening/dedup_decisions.csv"]},
  "GATE-S2c": {"status": "passed", "by": "human", "at": "...",
               "included": 38, "excluded": 92,
               "artifacts": ["02-screening/included_studies.csv",
                            "02-screening/excluded_studies.csv",
                            "02-screening/screening_log.csv"]}
}
```

> 与 **GATE-2**（人群边界裁决）的关系：S2c 确认的是"筛选流程走完且人拍板"；
> GATE-2 是契约级裁决门（`flag_occupational` 等四类边界标记 + `inclusion_decision`），
> 在 S3 提取前由人工对边界人群记录逐条裁决。两者都通过才启动 A3。

---

## 闸门状态机（写入 `_state/gates.json`）

```json
{
  "contract_version": "1.0.0",
  "config_version": "1.0.0",
  "snapshot_tag": "As_2026-06-05",
  "snapshot_sha256": "3f1a9c...",
  "gates": {
    "GATE-0": {"status": "passed", "by": "human", "at": "2026-01-10T09:00:00+08:00", "artifacts": ["project.yaml","00_charter.md"]},
    "GATE-1": {"status": "passed", "by": "human", "at": "2026-01-20T...", "artifacts": ["01-search/检索记录.csv"]},
    "GATE-S2a": {"status": "passed", "by": "human", "at": "...", "artifacts": ["01-search/all_raw_records.csv"]},
    "GATE-S2b": {"status": "passed", "by": "human", "at": "...", "decisions": 130, "artifacts": ["02-screening/title_abstract_screening.csv"]},
    "GATE-S2c": {"status": "passed", "by": "human", "at": "...", "included": 38, "excluded": 92},
    "GATE-2": {"status": "passed", "by": "human", "at": "2026-07-19T...", "decisions": 130, "artifacts": ["...筛查报告.md"]},
    "GATE-3": {"status": "passed", "by": "human", "verified_ratio": 1.0, "records_edited": 412},
    "GATE-4": {"status": "passed", "by": "human", "at": "..."},
    "GATE-5": {"status": "partial", "by": "human", "note": "去重决策待定 (R1-5)"}
  },
  "stale_artifacts": []
}
```

**状态取值**：`pending` / `in_review` / `passed` / `partial`（明确记录未完成项）/ `blocked`

**下游脚本的读取规则**
- `status != passed` 且不是被明确允许的 `partial` → **拒绝执行**，输出提示"请先完成 GATE-x"，退出码 2
- 允许 `partial` 的唯一情形：GATE-5 未决时仍可出**探索性**结果，但所有产出必须带 `EXPLORATORY` 水印，且不得用于结论

---

## 各 Skill 与子 Agent 的闸门职责

| 角色 | 负责产出的闸门输入 | 可写 `gate_status`？ |
|---|---|---|
| `search-agent` (A1) | GATE-1 全部 AI 产出物 | ❌ 只写 `pending` |
| `screening-agent` (A2) | GATE-2 疑似清单、评分表 | ❌ |
| `extraction-agent` (A3) | GATE-3 缺字段/可疑值/任务表 | ❌ |
| `statistics-agent` (A4) | GATE-4/5 换算痕迹、分层结果 | ❌ |
| `audit-agent` (A5) | 数值审计、去重、敏感性、DOI 核验 | ❌ |
| `revision-agent` (A6) | 审稿闭环文档 | ❌ |
| **主 Agent / 人** | — | ✅ **唯一可写者** |

> **权限铁律**：任何子 Agent 都不得把闸门从 `pending` 改为 `passed`。这一条由 `agent/routing.md` 的权限原则强制执行。

---

## 变更控制

| 变更 | 要求 |
|---|---|
| 新增闸门 | 版本 minor +1，并更新六个子 Skill 的产出物清单 |
| 调整裁决项 | 版本 minor +1，需说明为何某项可由 AI 代劳（一般不允许） |
| 放宽 `partial` 允许范围 | 版本 major +1，且必须在 README 显著位置声明风险 |

**维护者**：HBM-Meta-Agent
