#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
normalize_records.py — 题录规范化（S2-A 去重前置）

输入
  S1 `merge_sources.py` 产出的统一格式 CSV（表头必须与 shared/record_schema.py
  的 RECORD_FIELDS 完全一致，否则硬错误 —— 不做静默重排/补列）。

功能
  a. 清洗标题/摘要：折叠空白（规范化后重算 raw_row_hash —— 清洗会改变哈希，
     因此本脚本是哈希的**重新计算点**）
  b. 清洗作者：拆分（";" / " and " / "&"）、去空项、去大小写重复项、"; " 重连
  c. DOI 规范化：去 URL/`doi:` 前缀、小写；形态异常**原样保留**并记 warning
  d. PMID 规范化：纯数字保留；形态异常原样保留并记 warning
  e. 年份：必须是 4 位数字，否则清空并记 warning（缺失留空，不插补）
  f. source_database / record_id 透传（不改动；record_id 是审计链）

输出
  normalized_records.csv（列 = RECORD_FIELDS，与输入同构）

用法
  python normalize_records.py --input all_raw_records.csv --output normalized_records.csv
  python normalize_records.py --self-test

退出码
  0 成功   1 --validate 发现字段完整性问题   2 前置条件不满足
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
for _p in (str(_ROOT / "shared"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from record_schema import (  # noqa: E402  ★ S1 题录字段的单一来源
        RECORD_FIELDS, SchemaError, compute_row_hash, read_records_csv,
        write_records_csv,
    )
    from screening_schema import (  # noqa: E402  ★ S2 单一来源
        normalize_doi, normalize_pmid, normalize_text,
    )
except ImportError as _exc:  # pragma: no cover
    print(f"错误：无法导入 shared/record_schema.py 或 screening_schema.py（{_exc}）",
          file=sys.stderr)
    raise SystemExit(2)


_YEAR_LEN = 4
_YEAR_SEARCH = re.compile(r"(?:19|20)\d{2}")


def clean_authors(authors: str) -> tuple[str, list[str]]:
    """拆分、去空、去大小写重复（保留首个写法）、'; ' 重连。返回 (清洗值, warnings)。"""
    warnings: list[str] = []
    raw = normalize_text(authors)
    if not raw:
        return "", warnings
    parts: list[str] = []
    seen: set[str] = set()
    for chunk in raw.replace("&", ";").replace(" and ", ";").split(";"):
        name = normalize_text(chunk)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            warnings.append(f"作者重复已合并：{name}")
            continue
        seen.add(key)
        parts.append(name)
    return "; ".join(parts), warnings


def normalize_record(row: dict) -> tuple[dict, list[str]]:
    """规范化一条题录 → (新记录, warnings)。title/abstract 清洗后重算 raw_row_hash。"""
    warnings: list[str] = []
    rec = dict(row)

    for field in ("title", "abstract", "journal", "keywords"):
        cleaned = normalize_text(rec.get(field, ""))
        if cleaned != rec.get(field, ""):
            warnings.append(f"{field} 清洗了空白字符")
        rec[field] = cleaned

    authors, w = clean_authors(rec.get("authors", ""))
    warnings.extend(f"authors: {x}" for x in w)
    rec["authors"] = authors

    doi_in = normalize_text(rec.get("doi", ""))
    doi_out = normalize_doi(doi_in)
    if doi_out != doi_in:
        warnings.append(f"DOI 规范化：{doi_in!r} → {doi_out!r}")
    rec["doi"] = doi_out

    pmid_in = normalize_text(rec.get("pmid", ""))
    pmid_out = normalize_pmid(pmid_in)
    if pmid_out != pmid_in:
        warnings.append(f"PMID 规范化：{pmid_in!r} → {pmid_out!r}")
    rec["pmid"] = pmid_out

    year = normalize_text(rec.get("year", ""))
    if year and (not year.isdigit() or len(year) != _YEAR_LEN):
        # 杂质年份（如 "Published online 2021"）：提取其中的 4 位年份（原文字面存在，
        # 不属插补）；提取不到 → 清空并警告（缺失留空，不插补）
        m = _YEAR_SEARCH.search(year)
        if m:
            warnings.append(f"year=`{year}` 含杂质 → 提取 {m.group(0)}")
            year = m.group(0)
        else:
            warnings.append(f"year=`{year}` 无法提取 4 位年份 → 清空（不插补）")
            year = ""
    rec["year"] = year

    rec["raw_row_hash"] = compute_row_hash(rec.get("title", ""), rec.get("abstract", ""))
    return rec, warnings


def normalize_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """批量规范化 → (记录列表, warnings 列表，含 record_id 前缀)。"""
    out: list[dict] = []
    warnings: list[str] = []
    for row in rows:
        rec, w = normalize_record(row)
        rid = rec.get("record_id", "") or "(无 record_id)"
        warnings.extend(f"{rid}: {x}" for x in w)
        out.append(rec)
    return out, warnings


# ===========================================================================
# self-test
# ===========================================================================

FIXTURE_ROWS = [
    {
        "record_id": "pubmed-00001", "source_database": "PubMed",
        "title": "  Urinary   arsenic in general population ",
        "abstract": " Background:\tnational biomonitoring  data. ",
        "authors": "Zhang, San; Zhang, san; Li, Si and Wang, Wu",
        "year": "2020", "doi": "https://doi.org/10.1234/ARS.2020.001",
        "pmid": "32456789", "journal": " Environmental Research ",
        "keywords": "arsenic; biomonitoring",
    },
    {
        "record_id": "wos-00001", "source_database": "WebOfScience",
        "title": "Blood mercury in parturients",
        "abstract": "", "authors": "", "year": " Published online 2021 ",
        "doi": "doi: 10.5678/hg.2021.002.", "pmid": "MEDLINE:31055111",
        "journal": "Chemosphere", "keywords": "",
    },
    {
        "record_id": "cnki-00001", "source_database": "CNKI",
        "title": "某省居民尿砷水平调查", "abstract": "对居民砷内暴露的描述性研究。",
        "authors": "张三; 李四", "year": "2019",
        "doi": "not-a-doi", "pmid": "", "journal": "中华流行病学杂志", "keywords": "砷",
    },
]


def run_self_test() -> int:
    print("=" * 70)
    print("normalize_records.py --self-test")
    print("=" * 70)
    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"      {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))

    rows, warnings = normalize_rows([dict(r) for r in FIXTURE_ROWS])
    r1, r2, r3 = rows

    print("\n[1] 空白清洗与哈希重算")
    check("标题空白折叠", r1["title"] == "Urinary arsenic in general population",
          r1["title"])
    check("摘要空白折叠（含制表符）", r1["abstract"] == "Background: national biomonitoring data.",
          r1["abstract"])
    check("清洗后哈希被重算（≠ 原始哈希算法对脏文本的结果）",
          r1["raw_row_hash"] == compute_row_hash(r1["title"], r1["abstract"]))
    check("哈希为 64 位十六进制", len(r1["raw_row_hash"]) == 64)
    check("期刊空白清洗", r1["journal"] == "Environmental Research")

    print("\n[2] 作者清洗")
    check("大小写重复作者合并", r1["authors"] == "Zhang, San; Li, Si; Wang, Wu",
          r1["authors"])
    check("and/& 分隔符统一为 '; '", " and " not in r1["authors"] and "&" not in r1["authors"])
    check("空作者 → 留空", r2["authors"] == "")
    check("中文作者保留", r3["authors"] == "张三; 李四", r3["authors"])

    print("\n[3] DOI / PMID / 年份")
    check("URL 前缀剥离 + 小写", r1["doi"] == "10.1234/ars.2020.001", r1["doi"])
    check("doi: 前缀与尾标点剥离", r2["doi"] == "10.5678/hg.2021.002", r2["doi"])
    check("形态异常 DOI 原样保留（不猜）", r3["doi"] == "not-a-doi", r3["doi"])
    check("MEDLINE:NNN 形态 PMID 原样保留（不猜）",
          r2["pmid"] == "MEDLINE:31055111", r2["pmid"])
    check("年份杂质 → 提取 4 位", r2["year"] == "2021", r2["year"])
    check("形态异常 year 记入 warnings", any("year" in w.lower() for w in warnings),
          str(warnings[:2]))
    check("record_id 与来源透传（审计链不变）",
          r1["record_id"] == "pubmed-00001" and r1["source_database"] == "PubMed")

    print("\n[4] schema 一致性与写盘往返")
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="hbm_norm_"))
    src = tmp / "raw.csv"
    write_records_csv(src, [dict(r) for r in FIXTURE_ROWS])
    loaded = read_records_csv(src)
    check("读入统一格式 CSV 行数正确", len(loaded) == 3)
    out_rows, _ = normalize_rows(loaded)
    dst = tmp / "normalized.csv"
    write_records_csv(dst, out_rows)
    back = read_records_csv(dst)
    check("规范化输出仍是 RECORD_FIELDS 同构（可回读）",
          len(back) == 3 and back[0]["record_id"] == "pubmed-00001")
    check("往返后哈希稳定（幂等）",
          back[0]["raw_row_hash"] == out_rows[0]["raw_row_hash"])
    again, _ = normalize_rows([dict(b) for b in back])
    check("二次规范化幂等（哈希不变）",
          again[0]["raw_row_hash"] == out_rows[0]["raw_row_hash"])
    issues = validate_output(out_rows)
    check("规范化输出通过字段校验", issues == [], "; ".join(issues[:2]))

    print("\n" + "-" * 70)
    failed = [c for c in checks if not c[1]]
    if failed:
        print(f"self-test 失败：{len(failed)}/{len(checks)} 项未通过")
        for name, _, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print(f"self-test 通过：{len(checks)}/{len(checks)} 项全部通过")
    return 0


def validate_output(rows: list[dict]) -> list[str]:
    """输出校验：record_id 唯一 + title 非空 + 哈希自洽（--validate 共用）。"""
    issues: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        rid = normalize_text(row.get("record_id", ""))
        if not rid:
            issues.append(f"第 {i} 行：record_id 为空")
        elif rid in seen:
            issues.append(f"第 {i} 行：record_id 重复（{rid}）")
        seen.add(rid)
        if not normalize_text(row.get("title", "")):
            issues.append(f"第 {i} 行：title 为空")
        expect = compute_row_hash(row.get("title", ""), row.get("abstract", ""))
        if normalize_text(row.get("raw_row_hash", "")) != expect:
            issues.append(f"第 {i} 行：raw_row_hash 与规范化后的 title+abstract 不一致")
    return issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="normalize_records.py",
        description="题录规范化（S2-A 去重前置）→ normalized_records.csv")
    ap.add_argument("--input", type=Path, help="S1 统一格式 CSV（all_raw_records.csv）")
    ap.add_argument("--output", type=Path, help="输出 normalized_records.csv")
    ap.add_argument("--validate", action="store_true",
                    help="写盘后立即校验字段完整性")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    missing = [a for a, v in (("--input", args.input), ("--output", args.output)) if not v]
    if missing:
        print(f"错误：缺少必需参数 {' '.join(missing)}（--self-test 可单独运行）",
              file=sys.stderr)
        return 2
    if not args.input.exists():
        print(f"错误：输入文件不存在：{args.input}", file=sys.stderr)
        return 2

    try:
        rows = read_records_csv(args.input)
    except SchemaError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    out_rows, warnings = normalize_rows(rows)
    write_records_csv(args.output, out_rows)
    print(f"已规范化 {len(out_rows)} 条题录 → {args.output}")
    if warnings:
        print(f"⚠️ warnings 共 {len(warnings)} 条（不中断，建议复核）：")
        for w in warnings[:20]:
            print(f"  - {w}")
        if len(warnings) > 20:
            print(f"  …（其余 {len(warnings) - 20} 条略）")

    if args.validate:
        issues = validate_output(out_rows)
        if issues:
            print(f"❌ --validate 发现 {len(issues)} 个问题：", file=sys.stderr)
            for p in issues[:20]:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print("✅ --validate 通过")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
