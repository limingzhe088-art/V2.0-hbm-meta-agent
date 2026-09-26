# 全文排除原因编码 — 判定标准与易错案例

> **配套**：`{skill_root}/screening-workflow.md` §4、`project.yaml → screening.exclusion_reasons`（闭集来源）
> **原则**：编码由**人**在 `human_final_reason_code` 填写（GATE-S2c）；AI 只在
> `ai_reason_code` 给建议。排除的记录不删除。

---

## 编码清单

| 编码 | 判定标准 | 易错案例（不要这样做） |
|---|---|---|
| `not_human_study` | 样本非人体（动物/环境介质/体外/食品） | 标题含 "rat" 但正文有职业工人亚组 → 应查亚组而不是直接排除 |
| `wrong_analyte` | 测的不是目标暴露物（或形态不可合并，如只测 iAs 而课题要总砷——**该决策属 GATE-2/合并口径，不要在此偷换**） | "seafood arsenic"（砷甜菜碱）与无机砷课题的分析物口径冲突，须记录形态说明 |
| `no_hbm_data` | 有人群但无内暴露浓度（只有外暴露/问卷/环境监测） | 用 BMI、膳食问卷估计"暴露指数"的研究不是 HBM |
| `no_usable_values` | 有浓度但无可用统计量（只有 p 值/相关系数/检出率，或图无法读数） | 只有检出率（%<LOD）没有浓度水平 → 本编码；从图读数属 `value_estimated_from_figure`（S3 标记），不在全文筛阶段做 |
| `wrong_population` | 人群不符合纳入标准（非一般人群且无非职业/非病例亚组可用） | 判定依据不足时先用 uncertain，交 GATE-2 裁决；不要用本编码兜"感觉不对" |
| `wrong_year` | 采样/发表年份在窗口外 | 注意：检索窗口按**发表年**过滤；本编码按课题协议对**采样年**的约束判断，两者不一致时以协议为准并在 notes 说明 |
| `duplicate_cohort` | 与已纳入研究同一队列（ participants 重复） | 这是**队列级**判断：须指定 `cohort_id` 并与 GATE-2/GATE-5 的去重策略一致，不要只凭"同一作者" |
| `review_or_comment` | 综述/系统综述/Meta 分析/评论/社论 | 综述的参考文献是检索线索（citation mining），排除前可登记为背景文献 |
| `conference_abstract` | 会议摘要（通常无足够方法学细节） | 数据后来以全文发表的，应纳入全文版本而不是两个都留 |
| `insufficient_info` | 全文不可得 / 信息不足无法判断 | **这是保留编码**：全文不可得 ≠ 不符合纳入标准；PRISMA 单列 "reports not retrieved" |

---

## 使用规则

1. **一一对应**：`human_final_decision = exclude` 时本编码必填且必须在闭集内
   （`generate_inclusion_list.py` 强制校验，越界即拒绝）。
2. **不确定不硬编**：拿不准就留在 uncertain 由人继续查，不要"先编一个相近的"。
3. **组合情形取主因**：一篇研究可能同时踩多条（如动物 + 无可用数值），
   填**最主要**的排除原因（决定性的那条），其余写进研究笔记。
4. **闭集扩展**：新增编码 = 修改 `project.yaml → screening.exclusion_reasons`
   （bump config_version）+ 在本文件补判定标准行 + 复跑受影响阶段的脚本。
