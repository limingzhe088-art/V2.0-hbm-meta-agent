# 更新日志（Changelog）

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [2.0.0] — 2026-09-26

**S1–S6 六个子技能全部实现**的版本：补齐 v1.0.0 登记的两大缺口（S1 检索策略、
S2 筛选质控），新增全流程编排器与合成端到端测试。配套版本：契约 1.0.0 → **1.1.0**
（additive，新增 §10 筛选层七表；主表 72 字段语义不变）、配置口径 1.0.0 → **2.0.0**
（新增 search / screening 条件必需区块）。

### Fixed · v1.0.0 缺口清偿

- **F5（S1/S2 仅有骨架、未实现）→ 已完整实现**：两个子技能各自形成
  SKILL.md + 方法学文档 + references + 脚本（含 `--self-test`）+ 模板的完整结构；
  `verify_structure.py` 的 `PLANNED_SUB_SKILLS` 清空（6/6 子技能实现）
- **F3（题录格式解析缺失）→ 大部分实现**：RIS / EndNote（RefMan XML、
  制表符 TXT/CSV）/ BibTeX 解析器以纯标准库落地；`.docx` 原生解析仍为待办
- **筛选阶段无自动化 → "AI 预处理 + 人工闸门"完整落地**：题录归一、三级去重、
  关键词分类、全文四问判定、纳入/排除清单、PRISMA 2020 流程图全部脚本化；
  人工裁决点固化为 GATE-S2a / GATE-S2b / GATE-S2c（脚本强制检查点，
  `passed` 仍只由人写入）
- **S1→S6 无编排入口 → `scripts/run_pipeline.py`**：分阶段调度 + 闸门检查 +
  断点续跑 + 运行留痕（详见下方 Added）
- **CI 源码卫生步骤自指误报（v1.0.0 遗留）→ 已修复**：validate.yml 的
  损坏字符/硬编码路径 grep 未排除 `tests/ci_local.py`（其自身包含探针定义），
  结构上导致 GitHub CI 的 hygiene 步骤恒红；现以 `--exclude` 与本地
  `SELF_EXCLUDE` 对齐

### Added · S1→S6 全流程编排器与集成测试

- `scripts/run_pipeline.py` — **全流程编排器**（根目录 scripts/）：
  - 阶段：`s1`（按库解析→合并→检索式草稿）/ `s2a`（规范化+去重）/
    `s2b`（应用去重裁决+题摘分类）/ `s2c`（全文筛）/ `s2d`（清单+PRISMA）/
    `s3`（主表契约校验）/ `s4`（convert_units→wan_convert）/ `s5`（weighted_gm→edi）/
    `s6`（dedup_screen→sensitivity→audit_numbers）/ `s2` / `all` / `--status`
  - **闸门检查**：每阶段前置闸门从项目 `_state/gates.json` 读取，未通过 →
    退出码 3（WAITING）并提示人工操作位置——编排器只读闸门，永不写入
    （"只有人可以开门"铁律）；缺手稿时 audit 显式跳过
  - **断点续跑**：人工编辑产物（检索式草稿 / dedup_decisions /
    title_abstract_screening / fulltext_screening）有"人工列已填写"即跳过
    不覆盖，AI 草稿态可重生成；`--force` 显式覆盖
  - **留痕**：`_state/pipeline_log.json` 追加式记录每步状态（含 waiting）；
    退出码约定 0 成功 / 2 硬错误 / 3 等待人工；脚本退出码 1（"完成但含告警/
    待裁决项"）判为可继续并标记 ok_with_warnings
  - `--self-test` 19 项；源码卫生扫描范围扩至根目录 `scripts/`
- `tests/test_pipeline_integration.py` — **全链路集成测试（37 项断言）**：
  在临时项目目录用 bpa-china-demo 逐阶段驱动真实脚本链
  （parse→merge→normalize→dedup→classify→fulltext→inclusion→prisma→
  validate_table→convert_units→wan_convert→weighted_gm→edi→dedup_screen→
  sensitivity），验证：闸门暂停/续传语义（未开闸 → 退出码 3；人工写
  gates.json 后重跑成功）、疑似重复未裁决阻断（WAITING）、人工裁决应用
  （dedup_unique_confirmed）、人工列保护（重跑不覆盖）、`--stage all`
  首个人工点暂停、pipeline_log 状态机留痕（每阶段 waiting→ok）
- `examples/bpa-china-demo/project.yaml` 新增 `input.master_table` 区块
  （S3/S4/S6 的主表入口）
- `generate_inclusion_list.py`：study_id 编号修正——纳入集 `ST-xxxx` 与
  排除集 `EX-xxxx` 分命名空间各自连续（原全表连续编号使 included 出现
  ST-0007）；data-contract §10.6 补记
- CI：validate.yml 新增 **C9（S1 自检）/ C10（S2 自检）/ C11（全流程集成）**；
  ci_local 对齐新增步骤 9/10/11；C1 增 `scripts/run_pipeline.py`；
  源码卫生 grep 扫描范围扩至 `scripts/`
- 全文文件按 record_id 命名的真实流程约束在集成测试中以"人工按标题归位"
  步骤演示（真实项目即此操作）

### Changed · 项目配置与共享资产扩展（支持 S1/S2）

- `templates/project.yaml`（示例配置同步）：
  - `search` 区块升级为 v2 结构——`databases` 支持映射形态（name + 人工登记的
    检索执行元数据 search_string/records_exported/export_format/export_file；
    权威执行记录仍是检索记录 CSV）、`date_range`（start_year/end_year，
    旧形态 year_from/year_to 仍兼容）、`language_filter`（en/zh，PubMed/WoS
    草稿自动接入）、检索词表保留
  - `screening` 区块升级——`dedup.similarity_threshold`（别名
    title_similarity_threshold，二选一）与声明式 `match_levels`（method 闭集
    校验，未实现方法显式拒绝）、`meta_analysis_disposition`（定量处理意见
    5 值闭集，与文章类型分字段——"分类 ≠ 处理意见"）、
    `exclusion_reason_codes`（旧名 exclusion_reasons 兼容；新增保留编码
    `fulltext_unavailable`，11 项）、`classification_rules` 支持
    **关键词组格式**（review/animal/in_vitro/hbm_keywords，按固定顺序编译）
    与有序规则列表格式（二选一，后者支持 require_analyte 门控）
- `shared/data-contract.md` 新增 **§10 S1/S2 阶段数据表**（raw_records /
  dedup_decisions / title_abstract_screening / fulltext_screening /
  included_studies / excluded_studies / screening_log：粒度、字段、AI/人工
  写入边界、PRISMA 恒等式）；§5 补 search/screening 区块规范指引；
  §9 依赖关系表补 S1/S2。纯增量内容，主表 72 字段契约与契约版本号不变
- `shared/quality-gates.md` GATE-S2a/S2b/S2c 增补：脚本验证命令、人工确认
  清单（checkbox）、`_state/gates.json` 状态标记示例；状态机示例 JSON 补
  三个 S2 闸门
- `shared/project_config.py`：`CONDITIONAL_SECTIONS` 登记 search/screening
  （错误信息指明归属子技能）；区块**存在时**做轻量结构防御校验
  （深层语义校验单一来源仍在 S1/S2 脚本，避免两套校验器漂移）；52 项自检
- `agent/routing.md`：A1/A2 输出契约对齐 v2 产物（all_raw_records /
  dedup/included/excluded/screening_log 等）；交接契约表 A1→A2、A2→A3 更新；
  登记实现状态与 AHRQ 评分自动化缺口
- 根 `SKILL.md`：目录树对齐实际结构；新增 §3.1 子技能实现状态表（S1–S6 全部 ✅
  + 已知缺口诚实登记：AHRQ 评分、S2 边界标记自动扫描）；§6 快速上手修正
  （去除不存在的 project-scaffold 引用）
- `skills/S1-search-strategy/scripts/generate_search_terms.py`：支持字典型
  databases（"Web of Science" 等显示名归一）、date_range 别名、language_filter
  （PubMed `[Language]` / WoS `LA=` 草稿接入；未支持库输出提示）；35 项自检
- `skills/S2-screening-quality/scripts/screening_schema.py`：配置别名
  （similarity_threshold / exclusion_reason_codes）、关键词组格式编译、
  meta_analysis_disposition 与保留编码校验；46 项自检
- `fulltext_screening.py`：全文不可得的排除编码改用专用 `fulltext_unavailable`
  （原 insufficient_info 语义保留给"信息不足"）；输出表新增人工字段
  `meta_disposition`（AI 留空）；21 项自检
- `generate_inclusion_list.py`：meta_disposition 透传到 included/excluded
  清单并校验闭集（未配置闭集却填值 → 拒绝）；23 项自检
- S2 八份模板随 `meta_disposition` 列重生成（gen_templates 64 项自检）

### Added · S2-screening-quality 完整实现（v2 核心：AI 预处理 + 人工闸门）

- `skills/S2-screening-quality/scripts/screening_schema.py` — S2 **单一来源**：
  闭集枚举（状态机 / AI·人工决策 / 置信度）、`project.yaml → screening` 配置校验
  （缺失或非法 → ConfigError 硬错误）、词 bigram Jaccard 标题相似度、DOI/PMID/
  作者规范化、夹具配置（37 项自检）
- **S2-A 去重**：`normalize_records.py`（清洗 + 哈希重算，21 项）；
  `dedup_records.py`（DOI/PMID 精确 + 标题模糊三级去重；**模糊命中一律
  `suspected_duplicate` 不自动合并**；精确合并才从 dedup_unique 剔除，28 项）
- **S2-B 题摘初筛**：`classify_articles.py`（有序关键词规则引擎，词表/映射全部
  来自配置；AI 三值建议 + 规则 id + 命中证据，19 项）；`screening_summary.py`
  （闭集全展开计数 + GATE-S2b pending 提示，11 项）
- **S2-C 全文筛**：`fulltext_screening.py`（PDF 走 PyMuPDF / HTML / TXT；
  人体样本·分析物·统计量·年份四问；排除原因闭集；**GATE-S2b 检查点：
  human_decision 有空值即拒绝执行**，20 项）
- **收口**：`generate_inclusion_list.py`（**GATE-S2c 检查点：未终裁 / uncertain
  残留 / 排除编码越界 → 拒绝**；included/excluded/screening_log 三表，20 项）；
  `generate_prisma.py`（PRISMA 2020 Mermaid 流程图 + **四条恒等式数字自洽校验**，
  13 项）
- 文档：`SKILL.md`、`screening-workflow.md`（三阶段方法学）、
  `references/exclusion_reasons.md`（10 个排除编码的判定标准与易错案例）
- `templates/project.yaml` 与示例配置新增 `screening` 区块（9 类文章类型 /
  8 条分类规则 / 10 个排除编码 / 去重阈值——全部显式可扩展）
- `gen_templates.py` 新增 **8 份 S2 模板**（值由 S2 引擎对内置夹具真实运行算出，
  64 项自检）
- `quality-gates.md` 新增 **GATE-S2a/S2b/S2c**（S2 阶段人工确认点，脚本检查点
  由 S2 脚本强制；GATE-2 保持不变，两者衔接关系已写明）
- `requirements.txt` 新增条件必需组 **PyMuPDF**（PDF 全文解析；缺库且有 PDF
  待处理 → 显式报错，不做静默降级）；CI 安装组同步
- CI：`validate.yml` / `ci_local.py` 新增 8 个 S2 自检目标与 8 份随包模板断言；
  `verify_structure.py` 将 S2 移入已实现子技能（**6/6 子技能全部实现**）

### Added · S1-search-strategy 完整实现（STEP4-TODO.md F5 之 S1 部分 / F3 的 RIS·EndNote·BibTeX 部分）

- `shared/record_schema.py` — 统一题录字段 / 来源枚举 / `raw_row_hash` 算法的**单一来源**
  （11 字段；`SUMMARY_FIELDS` 与待回填提示语同源于此），自带 41 项自检
- `skills/S1-search-strategy/scripts/`
  - `generate_search_terms.py` — 从 `project.yaml → search`（条件必需区块，缺失即硬错误）
    生成 PubMed/WoS/Scopus/Embase/CNKI/万方检索式**草稿** + GATE-1 人工检查清单；
    中文词缺失时输出"待人工补充"占位，不编造中文词
  - `parse_ris.py` / `parse_enl.py` / `parse_bibtex.py` — 题录解析器 → 统一格式 CSV；
    手解析（无新依赖）；`.enl` 二进制等不支持格式**显式拒绝**；warnings 不中断
  - `merge_sources.py` — 多库合并 + `source_summary.csv`（导出数实算；
    检索式/命中数**仅取自人工检索日志**，AI 不代填）；疑似重复只计数，去重裁决在 S2
- `skills/S1-search-strategy/` 文档 — `SKILL.md`、`search-strategy.md`
  （可复现检索记录规范 + PRISMA identification 要求）、
  `references/database_syntax.md`（六库语法差异 + 导出格式建议）、
  `references/exclusion_word_bank.md`（四组排除词银行 + 过度杀伤预警）
- `templates/project.yaml` 与示例配置新增 `search` 区块（砷示例词表）
- `gen_templates.py`（唯一模板生成器）新增 S1 模板：`raw_records_template.csv`、
  `source_summary_template.csv`（禁止手写，`--check` 进 CI）
- CI：`ci_local.py` / `validate.yml` 新增 6 个自检目标与 2 个随包模板断言；
  `verify_structure.py` 将 S1 移入已实现子技能

### 说明

- S2（AI 预处理 + 人工闸门筛选）仍未实现，见 STEP4-TODO.md F5
- 主表契约（`data-contract.md`）与闸门定义无变更；S1 的题录 `source_database`
  枚举含 Embase，与主表列枚举是两个层级（见 `record_schema.py` 模块注释）

---

## [1.0.0] — 2026-07-19

首个可用版本。**从想法到文章**的完整流水线：把一类真实项目（中国一般人群内暴露砷
时空分布，384 篇文献 / 688 条记录）的方法学经验，固化为可复用的 Agent 工作包。

### Added · 核心资产

**数据契约（`shared/`）**
- `data-contract.md` — **72 字段主表契约**：类型、必填性、枚举、单位口径；
  §4 **快照指纹**（sha256 + 生成时间 + 配置版本）；§5 `project.yaml` 完整规范；
  §6 **20 项 `inferred_flags` 标记字典**；§7 8 条校验顺序
- `quality-gates.md` — **六道人工闸门**（GATE-0…GATE-5）：触发条件、AI 产出物、
  人工裁决项、未通过信号、状态机；含"唯一能把闸门改为 `passed` 的是人"的权限铁律
- `project_config.py` — **唯一配置加载入口**：`project.yaml` 缺失/解析失败/缺区块 →
  硬错误（**不允许任何兜底**）
- `inferred_flags.py` — 标记字典**单一来源**（23 项 = 契约 20 + 脚本内部 3），
  自带使用处覆盖核对与文档项数一致性检查
- `snapshot.py` — 快照指纹（`{analyte}_{YYYY-MM-DD}` + 字节级 sha256 + 记录/研究计数
  + 漂移判定 + 追加式登记表）

**六个子技能**
- `S3-extraction-standardization` — AI 初提 + **逐条人工校对**；提取提示词模板
  （环境/人群/复核三轮）、校对优先级 P1–P9、AI 易漏字段 15 项黑名单、可疑值模式 B1–B5
- `S4-unit-statistic-conversion` — **三级标准化流程**；Wan 2014 公式 **S1–S7** 保真实现；
  ICRP 89 参数选择决策树；`inferred_flags` 反推规则表；**S4 输入契约**明确化
  （S3 原始提取表 vs S4 输出 vs S5 输出的区分）
- `S5-weighted-pooling-edi` — 样本量加权 GM/GSD；**分期零字面量**；权重占比诊断；
  EDI 尿路**双路径交叉验证** + 血路**修正后公式**（旧公式显式拒绝）；
  **脐血方案 A**（不计算 EDI 并计数）；记录数 vs 唯一参与者措辞规则
- `S6-qc-audit-revision` — **数值审计**（从主表真正重算 + 四状态分类 + 连带修改定位）；
  重复计数筛查（三级聚簇 + 三种去重策略影响估计）；敏感性分析（分层/子集/排除后 +
  形态判定 + 降级规则）；文献链核验（DOI / 引用对照 / 插入表）；审稿闭环状态机
- `S1-search-strategy`、`S2-screening-quality` — **目录骨架**（见下方 Known limitations）

**可执行脚本（18 个 `.py`，全部含 `--self-test`，累计约 700 项断言）**
`validate_table` · `wan_convert` · `convert_units` · `weighted_gm` · `edi_calculation` ·
`gen_templates` · `audit_numbers` · `dedup_screen` · `sensitivity` · `verify_dois` ·
`citation_crosswalk` · `project_config` · `inferred_flags` · `snapshot` ·
`verify_consistency` · `verify_structure` · `ci_local` · `inject_failure`

**质量保障体系**
- `tests/verify_consistency.py` — 跨脚本一致性核验（C1–C6）：单一来源导入、
  反推确定性、规则表覆盖、端到端闭集、**唯一反推实现**、自检冒烟
- `tests/verify_structure.py` — 结构校验（S1–S6）：frontmatter、`parent_skill`、
  目录完整性、`name` 一致性、description 长度、顶层文件
- `tests/ci_local.py` — **本地 CI 预演**（无 GitHub 环境也能验证）
- `tests/inject_failure.py` — **注入断言失败以验证 CI 真的能拦住**
- `.github/workflows/validate.yml` — 5 步 CI（自检 / 模板 / 结构 / 一致性 / 源码卫生）
- `.gitignore` — 原始数据、稿件产物、`_state/`、凭证的排除规则

**教学案例与文档**
- `examples/arsenic-china-1980-2024/` — **脱敏可跑通案例**：检索式（含**排除词银行
  四类分组**）、72 列表头 + 12 条覆盖全部分叉的脱敏记录、数值审计与返修清单样例；
  自带脱敏规则自检
- `templates/project.yaml` — 可复制的完整配置模板（三键口径逐条说明"决定什么 + 改动后果"）
- `LICENSE`（MIT + CC BY 4.0 双许可）、`CITATION.cff`、`CONTRIBUTING.md`、本文件

### Design decisions · 关键设计决策

| 决策 | 理由 |
|---|---|
| **闸门只由人开启** | AI 无法做"该不该纳入某研究""该采信哪个数值"的领域判断 |
| **`project.yaml` 缺失即硬错误** | 用默认值继续会"看起来跑通、但口径是错的"——最危险的一类失败 |
| **字典单一来源** | 曾 5 处各写一份，扩充 16→19→20 时漏同步 2 处 |
| **未实现的口径显式拒绝** | `wan_variant="plus"` 未实现 → 抛错，**不做静默等价** |
| **降幅百分比不自动重算** | 分母选择不唯一，自动重算会引入"假确定感" |
| **脐血不计算 EDI（方案 A）** | 成人参数对胎儿是**模型对象错配**，不是精度不足 |
| **模板禁止手写** | 手写模板在开发期连续出错三次（列错位、数值与代码不一致） |

### Fixed · 开发期发现并修复的实质缺陷

| # | 缺陷 | 后果 |
|---|---|---|
| 1 | `wan_convert` 不读 `wan_variant` / `median_to_gm_strategy` | 配置**读了却无效**（静默失败） |
| 2 | `wan_variant="plus"` 与 `minus` 结果相同 | **假实现**（静默等价）→ 改为显式拒绝 |
| 3 | `validate_table` 的 `unit_policy` 传嵌套字典 | 单位口径校验**永远失败** |
| 4 | `validate_table` 的字典副本停留在 16 项 | 闭集校验形同虚设 |
| 5 | `convert_units` 只取 `initial_gm` | 仅报告中位数的记录被误判为"缺值" |
| 6 | `wan_convert` 不保留输入行的单位换算段 | 链式运行 **7/12 记录 `FLAG_MISMATCH`** |
| 7 | `derive_flags` 的 `reported_GM` 未早退 | 零估算记录被叠加统计量类标记 |
| 8 | `dedup_screen` 用 `year[:4]` 切片分期 | 违反分期铁律（且无年份语义） |
| 9 | `dedup_screen` 的 `Study.year` 取自 `t_publication` | 用**发表年**替代采样年 |
| 10 | `sensitivity` 阈值用 `>=` | 恰好 5% 被误判为上升 |
| 11 | `citation_crosswalk` 正则字符类漏空格 | **9 条引用只提取到 2 条** |
| 12 | `audit_numbers` 按段落而非句子推断分层 | 跨句线索污染（血值被误判为时段值） |

### Known limitations · 已知限制（见 `STEP4-TODO.md`）

- **S1 / S2 仅有目录骨架**，尚未实现（`verify_structure.py` 标注为"规划中"不校验）。
  实现计划见 `STEP4-TODO.md` **F5**
- `wan_variant="plus"` 未实现（显式拒绝 → 见 **F1**）
- 脐血 EDI 走方案 A（不计算）；独立参数表见 **F2**
- `.docx` / `.enl` / `.ris` 需先手工转换为 `.md` / `.csv`（见 **F3**）
- `weighting.method = inverse_variance` 未实现（见 **F4**）

### Notes · 关于本项目的来源

本工作包脱胎于一个已完成的真实科研项目（中国一般人群内暴露砷时空分布 meta 分析，
1980–2024，384 篇文献 / 688 条记录）。所有方法学规则、易错点、审稿意见类型
均来自该项目的实证，而非推测。

**案例已完全脱敏**：具体研究标题、作者、DOI、省份级真实数值均已移除；
命中数（5714 / 546 / 4649 / 519 / 135 / 384）作为**检索充分性诊断**的核心证据保留。

---

## 版本路线图

| 版本 | 计划内容 |
|---|---|
| v1.1 | 实现 S1 / S2（F5）；补齐对应的结构校验与案例示例 |
| v1.2 | Wan `plus` 变体（F1）；`inverse_variance` 加权（F4） |
| v1.3 | `.docx` / 文献库解析器（F3）；脐血 EDI 参数表（F2） |
