# HBM-Meta-Agent

**做「人体内暴露浓度」meta 分析的 Agent 工作包** — 从检索策略到返修闭环的完整流水线。

**An agent work package for meta-analyses of human internal-exposure biomarker concentrations** — a full pipeline from search-strategy construction to closed-loop revision.

**当前版本 v2.0.0：S1–S6 六个子技能全部实现**（v1.0.0 登记的 S1/S2 缺口已清偿，见 [CHANGELOG](CHANGELOG.md)）。

[![validate](https://github.com/limingzhe088-art/hbm-meta-agent/actions/workflows/validate.yml/badge.svg)](https://github.com/limingzhe088-art/hbm-meta-agent/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![License: CC BY 4.0](https://img.shields.io/badge/docs-CC%20BY%204.0-lightgrey.svg)](LICENSE)
![version](https://img.shields.io/badge/version-2.0.0-green)

---

## 30 秒上手 / Quick Start

```bash
# 1) 安装依赖（单栈 Python，无 R）
pip install -r requirements.txt

# 2) 复制配置模板
cp templates/project.yaml ./project.yaml
#    按课题修改：analyte / country / period_scheme / unit_policy
#    条件必填区块：用 S1 时配 search，用 S2 时配 screening（缺失即硬错误）

# 3) 自检：确认环境正确（应 11/11 通过）
python tests/ci_local.py

# 4) 全流程编排（分阶段驱动真实脚本，闸门处暂停等人）
python scripts/run_pipeline.py --config project.yaml --stage all
python scripts/run_pipeline.py --config project.yaml --status

# 5) 在 Agent 中说出： 「开始内暴露 meta 分析，暴露物是 PFOA」
```

> **只要你的研究对象是「把多篇文献里的人体生物样本浓度合并成总体水平」，本包适用。**
> 如果你做的是效应量合并（OR/RR/HR）、单个体的原始数据、或非人体样本，本包**不适用**。

---

## 快速开始：用 BPA 合成案例做端到端测试 / Quick Start with the BPA Demo

`examples/bpa-china-demo/` 是一个**全部数据虚构**的合成案例（20 条合成题录、
8 份合成全文、6 行合成主表），专用于功能验证与 CI——可安全公开、可直接跑通：

```bash
# A) 端到端测试（S1 解析/合并 → S2 全链路 → S2→S3 交接 + 快照比对，52 项断言）
python tests/test_e2e_bpa_demo.py

# B) 全流程集成测试（S1→S6 经 run_pipeline，闸门暂停/续传 + 状态机验证，37 项断言）
python tests/test_pipeline_integration.py

# C) 手动分阶段走一遍（在临时目录复制案例后）
cp -r examples/bpa-china-demo /tmp/my-demo && cd /tmp/my-demo
python scripts/run_pipeline.py --config project.yaml --stage s1
#   … 人工确认检索充分性（GATE-1）与原始数据（GATE-S2a）后：
python scripts/run_pipeline.py --config project.yaml --stage s2a
#   … 人工填写去重裁决与题摘裁决（GATE-S2b）→ s2b → s2c →（GATE-S2c）→ s2d
python scripts/run_pipeline.py --config project.yaml --stage s3   # 依次 s4 / s5 / s6
python scripts/run_pipeline.py --config project.yaml --status
```

---

## 它解决什么坑 / What It Solves

内暴露浓度 meta 分析的研究者反复踩同一组坑。本工作包把**一类真实项目的四类历史事故**
编码成机制（而非写在文档里建议）：

| 事故 | 表现 | 机制化对策 |
|---|---|---|
| **快照漂移** | 手稿数值基于旧数据表，返修时才发现时段/地区数值系统性过时 | **快照指纹**（sha256 + 时间 + 记录数）；手稿数字追溯不到来源即判 `STALE_MANUSCRIPT` |
| **分期不一致** | 描述性分析用一套时段边界、EDI 脚本用另一套、手稿混用 | `project.yaml → period_scheme` **单点定义 + 冻结日**；改动强制下游重算 |
| **单位口径混用** | 尿样的 μg/L、μg/g Cr、比重校正三类记录被直接合并 | 契约把 `unit_original`（原文留痕）与 `unit_final`（标准化输出）**分列建模** |
| **重复计数** | 同一出生队列被 35 篇论文报告，参与者总数被重复累加 | `cohort_id` + `dedup_group` 字段；GATE-2/GATE-5 强制队列聚簇裁决 |

**再加两条用血换来的教训**：
- **AI 初提必漏字段**（采样年份、尿校正状态、血基质、统计量类型）→ GATE-3 要求**逐条人工校对**，`verified` 不足的记录不得用于主结论。
- **绝不允许推断未报告的数据**：`gm_summary` / `sample_size` / `time` / `sample_type` 四个字段**禁止插补**。

**v2.0.0 新增机制**：筛选阶段（S2）实现"**AI 预处理 + 人工闸门**"——AI 完成去重、
分类、初筛建议等 70%–90% 的机械劳动，但纳入/排除的最终决定权、以及全部
`human_*` 列，只属于人；被排除的记录不物理删除，可随时审计 AI 是否漏筛。

---

## 核心资产 / Core Assets

| 资产 | 位置 | 内容 |
|---|---|---|
| **数据契约** | `shared/data-contract.md` | 72 字段主表定义、单位口径、快照指纹、`project.yaml` 规范、20 项推断标记字典、**§10 筛选层七张数据表**（v2） |
| **人工闸门** | `shared/quality-gates.md` | GATE-0…GATE-5 + **S2 阶段闸门 GATE-S2a/S2b/S2c**（v2）：触发条件、脚本检查点、人工裁决项、状态机 |
| **唯一配置入口** | `shared/project_config.py` | 配置缺失即**硬错误**（不允许兜底）；search/screening 区块存在时做结构防御 |
| **标记单一来源** | `shared/inferred_flags.py` | 23 项注册表 + 使用处覆盖核对 + 文档项数一致性 |
| **题录字段单一来源** | `shared/record_schema.py` | S1 统一题录 11 字段 + 来源枚举 + `raw_row_hash` 算法（v2） |
| **快照指纹** | `shared/snapshot.py` | 字节级 sha256、漂移判定、追加式登记表 |
| **全流程编排器** | `scripts/run_pipeline.py` | S1→S6 分阶段调度、闸门检查、断点续跑、`--status`、运行留痕（v2） |
| **六个子技能** | `skills/S1–S6/` | 检索 → 筛选 → 提取 → 标准化 → 合并+EDI → 质控返修（全部实现） |
| **可执行脚本** | `skills/*/scripts/` + `scripts/` | 30 个脚本，**全部含 `--self-test`**（累计 >900 项断言） |
| **质控体系** | `tests/` | 跨脚本一致性 / 结构校验 / 本地 CI 预演（11 步）/ E2E / 集成测试 / **注入失败验证 CI** |
| **示例案例** | `examples/` | `arsenic-china-1980-2024/`（真实课题脱敏示例）+ `bpa-china-demo/`（合成演示案例，v2） |

### 六个子技能 / Six Sub-Skills

| 子技能 | 阶段 | 闸门 | 一句话 |
|---|---|---|---|
| `S1-search-strategy` | 1 | GATE-1 | 检索式草稿生成（六库语法）+ 题录解析（RIS/EndNote/BibTeX）+ 多库合并；命中数只认人工登记 |
| `S2-screening-quality` | 2 | GATE-S2a/b/c → GATE-2 | AI 预处理 + 人工闸门：三级去重、关键词分类、全文四问、纳入/排除清单、PRISMA 流程图 |
| `S3-extraction-standardization` | 3 | GATE-3 | AI 初提 + **逐条人工校对**，把论文变成可追溯的主表 |
| `S4-unit-statistic-conversion` | 4 | GATE-4 | **三级标准化**：统一指标 → 统一单位 → 统一为 GM（Wan S1–S7 + ICRP 89） |
| `S5-weighted-pooling-edi` | 5 | — | 样本量加权 GM/GSD + EDI（尿双路径 + 血修正公式） |
| `S6-qc-audit-revision` | 6 | GATE-5 | 数值审计 + 去重 + 敏感性 + 文献链 + 审稿闭环 |

---

## 目录结构 / Layout

```
hbm-meta-agent/
├── SKILL.md                    总编排 + 子技能加载规则（含实现状态表）
├── agent/routing.md            6 个子 Agent 路由表 + 权限原则
├── shared/                     数据契约 / 闸门 / 配置入口 / 单一来源模块
├── skills/S1–S6/               六个子技能（SKILL.md + references + scripts + templates）
├── scripts/run_pipeline.py     ★ 全流程编排器（闸门检查 + 断点续跑 + --status）
├── templates/project.yaml      项目配置模板（search / screening 条件必需区块）
├── examples/                   arsenic（脱敏教学）+ bpa-china-demo（合成 E2E 案例）
├── tests/                      ci_local（11 步）/ E2E / 集成测试 / 结构与一致性校验
└── .github/workflows/validate.yml   CI：C1–C12
```

---

## 通用性 / Generality

**可变项与不变项在架构上分离** —— 换暴露物 / 换人群**不用重写代码**：

| 每次会变 | 改哪里 |
|---|---|
| 暴露物（砷 → BPA / PFOA / 抗生素 / 药物浓度） | `project.yaml → analyte` + `search.analytes` 词表 + 单位换算表 |
| 人群（中国一般人群 → 全球 / 特定人群） | `project.yaml → country` |
| 时空（1980–2024 中国 → 任意） | `project.yaml → period_scheme` + `region_map` |
| 检索式与筛选闭集 | `project.yaml → search` / `screening` 区块（词表、规则、阈值全部显式可扩展） |

| 不变（核心资产） |
|---|
| 基质枚举（尿/血/脐血，可扩展母乳/指甲/头发） |
| 提取逻辑（AI 初提 + 人工逐条校对） |
| 三级标准化流程 |
| 样本量加权合并 |
| 质控与返修体系（数值审计/去重/敏感性/DOI 核验/审稿闭环） |

---

## 依赖 / Requirements

```bash
pip install -r requirements.txt
```

| 包 | 必需 | 用途 |
|---|---|---|
| **PyYAML** | ✅ | 解析 `project.yaml`（缺失即硬错误） |
| **openpyxl** | ✅ | 读取主表 `.xlsx` |
| **PyMuPDF** | 条件必需 | S2 全文筛解析 PDF（无 PDF 全文时可不装；缺失且有 PDF → 显式报错） |
| pandas / numpy / scipy | ❌ 可选 | 当前实现用标准库完成；安装后可交叉验证 |

**Python ≥ 3.11。无 R 依赖。**

---

## 质量保障 / Quality Assurance

```bash
python tests/ci_local.py            # 本地 CI 预演：11/11 应通过
python tests/verify_consistency.py  # 跨脚本一致性：6/6 应通过
python tests/verify_structure.py    # 结构校验：6/6 应通过
python tests/verify_repo_clean.py   # 仓库洁净度（字节码/CRLF）
python tests/inject_failure.py --inject --check   # 验证 CI 真能拦住失败
```

CI（`.github/workflows/validate.yml`）执行 C1–C12：
**全部脚本自检 → 模板一致性 → 结构 → 跨脚本一致性 → 源码卫生 → 随包文件 →
仓库洁净度 → BPA E2E → S1 自检 → S2 自检 → 全流程集成测试**。

> 源码卫生检查含三个探针：损坏字符、硬编码路径、口径兜底（`DEFAULT_*`）。
> 详见 `agent/routing.md` **§2.6 源码改动纪律**。

---

## ⚠️ Known Limitations

| 项 | 状态 | 计划 |
|---|---|---|
| `.docx` 稿件原生解析（含修订痕迹/批注） | 未实现（`.md`/`.txt` 占位/线索模式可用；RIS/EndNote/BibTeX 已由 S1 实现） | F3 残留 |
| `wan_variant="plus"` | **显式拒绝**（不做静默等价） | F1 |
| 脐血 EDI | 方案 A：不计算并计数 | F2 |
| `inverse_variance` 加权 / 随机效应合并 | 未实现（声明即显式拒绝） | F4 / v2.1 |
| AHRQ 11 条质量评分 | 无脚本自动化（人工/AI 协助填表） | v2.1 |
| S2 边界人群标记自动扫描（`flag_occupational` 等） | 未脚本化（人工按契约 §3.4 裁决） | v2.1 |
| 去重人工裁决的批量应用助手 | 编排器已实现机械过滤；独立脚本化待定 | v2.1 |

> ~~S1 / S2 仅有目录骨架~~ —— **v2.0.0 已清偿**（见 CHANGELOG 的 Fixed 段）。

---

## 贡献 / Contributing

请先读 **`CONTRIBUTING.md`**，特别是：
- **§2 源码改动纪律**（不要用 shell 往返改写源码——本项目真实因此损坏过 3 个文件）
- **§4 新增 `inferred_flags` 标记的四步**（单一来源 + 契约 + 文档 + 验证）
- **§8 数据与隐私红线**（不得提交原始数据 / 可识别信息 / 凭证）

---

## 引用 / Citation

```bibtex
@software{hbm_meta_agent_2026,
  title  = {HBM-Meta-Agent: Human Biomonitoring Meta-Analysis Agent},
  year   = {2026},
  version= {2.0.0},
  license= {MIT},
  url    = {https://github.com/limingzhe088-art/hbm-meta-agent}
}
```

见 `CITATION.cff`（含 Wan 2014 与 PRISMA 2020 的引用信息）。

---

## 许可 / License

- **代码**（`.py` / `.yml` / `.yaml`）：**MIT**
- **文档**（`.md`）：**CC BY 4.0**

详见 `LICENSE`。

---

## 设计基石 / The One Rule to Remember

> **本工作包的每一处「自动」，都必须有一处「人工裁决」与之配对。**
>
> Every automated step in this work package is paired with a human decision point.
>
> 六道闸门与 S2 的三道阶段闸门**只由人开启**——AI 只能产出"疑似 / 建议 / 差异"，
> 所有 `human_*` 列初始为空。这不是能力限制，而是设计选择：
> **"这个研究该不该纳入""这个数是不是真的"只有领域研究者能做。**
