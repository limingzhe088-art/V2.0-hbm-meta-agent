# PRISMA 2020 流程图（Identification → Screening → Included）

- 项目：analyte=BPA country=China periods=3 regions=7 config_version=2.0.0

```mermaid
flowchart TD
    A["检索识别的记录<br/>n = 20"]
    B["去重前记录<br/>n = 20"]
    C["去重后记录<br/>n = 18"]
    D["重复记录被移除<br/>n = 2"]
    E["题目+摘要人工筛选<br/>n = 18"]
    F["题摘阶段排除<br/>n = 8"]
    G["题摘阶段未决/不确定<br/>n = 2"]
    H["进入全文获取<br/>n = 8"]
    I["全文评估<br/>n = 8"]
    J["全文未获取<br/>n = 0"]
    K["全文人工复筛<br/>n = 8"]
    L["全文排除（见排除原因分项）<br/>n = 2"]
    M["最终纳入<br/>n = 6"]
    A --> B
    B --> C
    B --> D
    C --> E
    E --> F
    E --> G
    E --> H
    H --> I
    I --> J
    I --> K
    K --> L
    K --> M
```

## 数字核对表

| 阶段 | 恒等式 | 数值 |
|---|---|---|
| identification | records_identified_total = records_after_dedup + duplicates_removed | 20 = 18 + 2 |
| screening | ta_screened = ta_excluded + ta_uncertain_unresolved + ta_proceeded_fulltext | 18 = 8 + 2 + 8 |
| retrieval | fulltext_assessed = fulltext_retrieved + fulltext_unavailable | 8 = 8 + 0 |
| included | fulltext_retrieved = fulltext_excluded + fulltext_included | 8 = 2 + 6 |

## 各指标数值

| metric | value |
|---|---|
| records_identified_total | 20 |
| records_after_dedup | 18 |
| duplicates_removed | 2 |
| ta_screened | 18 |
| ta_excluded | 8 |
| ta_uncertain_unresolved | 2 |
| ta_proceeded_fulltext | 8 |
| fulltext_assessed | 8 |
| fulltext_unavailable | 0 |
| fulltext_retrieved | 8 |
| fulltext_excluded | 2 |
| fulltext_included | 6 |

> 被排除的记录不物理删除（见 excluded_studies.csv）；GATE-S2c 由人工确认后，纳入清单方可进入 S3。