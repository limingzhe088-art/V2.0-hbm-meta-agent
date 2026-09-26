# Examples — 示例案例说明

本目录包含两类示例，用途与数据性质完全不同：

## 1. `bpa-china-demo/` — 合成演示案例（SYNTHETIC DEMO）

**用途**：开发验证与 CI 测试。`tests/test_e2e_bpa_demo.py` 每次运行都会用本案例
把 **S1（检索/解析）→ S2（去重/分类/全文筛/清单/PRISMA）→ S2→S3 交接**
完整跑一遍，并逐行比对确定性快照。

- **所有文献题录、作者、DOI、PMID、数值均为虚构**（标题以 DEMO-Study- 占位、
  DOI 为 `10.1234/demo.xxx` 保留段式编造），可安全公开在仓库中。
- 规模：20 条合成题录（8 项人群 HBM 原始研究 + 3 综述 + 3 动物 + 2 体外 +
  2 人群非内暴露 + 2 条故意设计的重复记录）+ 8 份合成全文 + 6 行合成主表。
- 02-screening/ 下的 CSV 与 prisma_flow.md 是 e2e 链路的**确定性输出快照**：
  人工列（human_decision 等）已按 GATE-S2b/S2c 的裁决模拟预填。
  行为变化会使 CI 变红；有意变更后用
  `python tests/test_e2e_bpa_demo.py --update-expected` 重新生成。

**使用方式**：以 `bpa-china-demo/project.yaml` 为起点复制到你的工作目录，
按课题修改配置（analyte 词表、period_scheme、region_map、unit_policy、
screening 闭集等），再从 S1 开始走流程。

## 2. `arsenic-china-1980-2024/` — 真实课题的脱敏示例

展示真实课题（中国一般人群砷内暴露，384 篇 / 688 条）的**方法学形状**：
主表 72 列的填报样态、Wan/ICRP 换算路径、审计与返修文档样式。
其中数值为合成值、标识信息已脱敏，但**结构对应真实课题的产物**。

## 真实课题数据放哪里？

- 真实课题（如 arsenic-china-1980-2024 的完整版、或你的新课题）应作为
  **仓库之外的独立目录**使用；仓库内的 examples/ 只保留脱敏示例。
- 原始文献导出件、主表快照、稿件等**不应进入版本库**：
  仓库的 `.gitignore` 已按扩展名拦截（`*.xlsx` / `*.pdf` / `*.csv` 等），
  例外仅限 `templates/` 与 `examples/` 的模板与脱敏文件。
- 若确需在本仓库保存某个真实课题目录，请确认每个文件都已脱敏，
  并逐一加入 `.gitignore` 例外（参照 `!examples/**` 规则的写法），
  避免 CI 的 C7/C8 洁净度检查出现意外。

## 已知简化（demo 案例）

- 检索数字（342/287/391）为演示用虚构值；真实流程的权威执行记录是
  各库检索式原文 + 日期 + 命中数（`source_summary.csv` / 检索记录）。
- demo 的 `analysis` 区块（random_effects 等）是 v2.1 规划项声明，
  当前 S5 实现为样本量加权合并；脚本不读取该区块。
- S4（单位换算）/ S5（加权合并）/ S6（审计返修）在本 demo 中未自动化串联，
  由各脚本 `--self-test`（500+ 断言）与砷案例的端到端手册流程覆盖；
  demo 主表已通过 S3 `validate_table.py`（ERROR=0）。
