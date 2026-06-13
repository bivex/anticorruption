#!/usr/bin/env python3
"""
Analyze Ukrainian public officials' NAZK declarations for corruption violations.

Usage:
    python3 analyze.py "Прізвище Ім'я По-батькові"
    python3 analyze.py "Іванов Іван Іванович" --all
    python3 analyze.py "Іваненко Петро" --year 2025 --edrpou 40116400
    python3 analyze.py --id <uuid>
    python3 analyze.py --id <uuid> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from typing import Any

from nazk_sdk import NazkClient, parse_declaration, CatalaAnalyzer, ParsedDeclaration, AnalysisResult
# ── colours ──────────────────────────────────────────────────────────────────
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

DECL_TYPES = {1: "річна", 2: "кандидат", 3: "виїзна", 4: "коригуюча", 0: "позачергова"}
SUBSISTENCE_2025 = Decimal("3028")


def _flag(s: str) -> str:  return f"{RED}{BOLD}{s}{RESET}"
def _warn(s: str) -> str:  return f"{YELLOW}{s}{RESET}"
def _ok(s: str) -> str:    return f"{GREEN}{s}{RESET}"
def _hdr(s: str) -> str:   return f"{CYAN}{BOLD}{s}{RESET}"
def _dim(s: str) -> str:   return f"{DIM}{s}{RESET}"


# ── single declaration report ─────────────────────────────────────────────────
def report(decl: ParsedDeclaration, result: AnalysisResult, verbose: bool = False, decl_type: int = 1) -> None:
    total_salary = sum(
        i.amount_uah for i in decl.incomes
        if i.income_type in ("salary", "monetary_allowance")
        and (i.recipient == decl.declarant_name or i.recipient.startswith(decl.declarant_name[:6]))
    )
    gift_total = sum(i.amount_uah for i in decl.incomes if i.income_type in ("gift_money", "gift_in_kind"))
    other_income = sum(i.amount_uah for i in decl.incomes if i.income_type not in ("salary", "monetary_allowance"))
    total_cash = decl.cash_uah + decl.cash_usd * 41 + decl.cash_eur * 45

    # Status line
    if result.gift_violations:
        status = _flag(f"!! ПОРУШЕННЯ ({len(result.gift_violations)})")
    elif result.warnings:
        status = _warn(f"⚠  ПІДОЗРІЛІ ({len(result.warnings)})")
    else:
        status = _ok("✓  чисто")

    dtype_str = DECL_TYPES.get(decl_type, str(decl_type))
    print(f"  {decl.declaration_year} [{dtype_str:11s}]  {decl.position[:35]:35s}  "
          f"зп={total_salary:>10,.0f}  "
          f"готівка={total_cash:>10,.0f}  "
          f"інше={other_income:>9,.0f}  {status}")

    # Violations
    for v in result.gift_violations:
        print(_flag(f"         !! {v['description']}"))

    # Warnings
    for w in result.warnings:
        print(_warn(f"          ! {w}"))

    if not verbose:
        return

    # Incomes detail
    non_salary = [i for i in decl.incomes if i.income_type not in ("salary", "monetary_allowance") and i.amount_uah > 0]
    if non_salary:
        for i in non_salary:
            print(_dim(f"         >> {i.income_type:22s} {i.amount_uah:>10,.0f}  "
                       f"{i.source_name[:45]}  -> {i.recipient[:35]}"))

    # Vehicles
    for v in decl.vehicles:
        cost = f"{v.cost_uah:,.0f}" if v.cost_uah else "?"
        print(_dim(f"         авто: {v.brand} {v.model} {v.year}  {v.ownership_type[:30]}  "
                   f"вартість={cost}  {v.owner[:25]}"))

    # Properties
    for p in decl.properties:
        print(_dim(f"         нерух: {p.object_type:20s} {p.city:12s} {p.ownership_type:20s} {p.owner[:25]}"))

    # Deposits
    for d in decl.deposits:
        if d.amount > 0:
            print(_dim(f"         депозит: {d.currency} {d.amount:,.0f}  {d.bank_name[:40]}  {d.owner[:20]}"))

    # Transactions
    for t in decl.transactions:
        cost = f"{t.cost_uah:,.0f}" if t.cost_uah else "невідомо"
        print(_dim(f"         угода: {t.date}  {t.transaction_type}  {t.subject}  вартість={cost}"))

    # Family
    fam = ", ".join(f"{m.relation} {m.firstname} {m.lastname}" for m in decl.family_members)
    if fam:
        print(_dim(f"         сім'я: {fam}"))


# ── summary table ─────────────────────────────────────────────────────────────
def summary_table(rows: list[tuple[ParsedDeclaration, AnalysisResult]]) -> None:
    total_viols = sum(1 for _, r in rows if r.gift_violations)
    total_warns = sum(1 for _, r in rows if r.warnings and not r.gift_violations)

    print()
    print(_hdr("═" * 100))
    if total_viols:
        print(_flag(f"  РАЗОМ ПОРУШЕНЬ ART.23: {total_viols} декларацій"))
    if total_warns:
        print(_warn(f"  РАЗОМ ПІДОЗРІЛИХ: {total_warns} декларацій"))
    if not total_viols and not total_warns:
        print(_ok("  Порушень не виявлено"))

    # Gifts summary
    all_gifts = [(d.declaration_year, i) for d, _ in rows
                 for i in d.incomes if i.income_type in ("gift_money", "gift_in_kind")]
    if all_gifts:
        print(_flag(f"\n  Подарунки:"))
        for yr, i in sorted(all_gifts, key=lambda x: x[0]):
            cap = SUBSISTENCE_2025 * 2
            over = i.amount_uah / cap if cap else 0
            print(_flag(f"    {yr}  {i.source_name[:45]:45s}  {i.amount_uah:>10,.0f} грн  "
                        f"({over:.1f}× ліміту)  -> {i.recipient[:30]}"))

    # Cash trend
    cash_rows = [(d.declaration_year, d.cash_uah + d.cash_usd * 41 + d.cash_eur * 45,
                  sum(i.amount_uah for i in d.incomes if i.income_type in ("salary","monetary_allowance")))
                 for d, _ in rows]
    if any(c > 0 for _, c, _ in cash_rows):
        print(_warn(f"\n  Динаміка готівки:"))
        for yr, cash, sal in sorted(cash_rows, key=lambda x: x[0]):
            months = float(cash) / (float(sal) / 12) if sal > 0 else 0.0
            bar = "█" * min(int(months), 40)
            flag = _flag(f"  {months:4.1f} міс") if months > 6 else _ok(f"  {months:4.1f} міс")
            print(f"    {yr}  {cash:>10,.0f} грн{flag}  {bar}")

    print(_hdr("═" * 100))
    print()


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Аналіз декларацій НАЗК на порушення антикорупційного законодавства"
    )
    parser.add_argument("query", nargs="?", help="ПІБ для пошуку")
    parser.add_argument("--id",     help="UUID конкретної декларації")
    parser.add_argument("--year",   type=int, help="Рік декларації")
    parser.add_argument("--edrpou", help="ЄДРПОУ роботодавця")
    parser.add_argument("--all",    action="store_true", help="Всі роки (без фільтру)")
    parser.add_argument("--limit",  type=int, default=20, help="Макс. результатів пошуку (default: 20)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Детальний вивід (майно, авто, угоди)")
    parser.add_argument("--json",   action="store_true", help="Вивести сирий JSON декларації")
    parser.add_argument("--list",   action="store_true", help="Тільки список декларацій (без аналізу)")
    args = parser.parse_args()

    if not args.query and not args.id:
        parser.print_help()
        sys.exit(1)

    client = NazkClient()

    # ── mode: single UUID ──
    if args.id:
        raw = client.get_document(args.id)
        if args.json:
            print(json.dumps(raw, ensure_ascii=False, indent=2))
            return
        decl = parse_declaration(raw)
        result = CatalaAnalyzer(decl).analyze()
        print(_hdr(f"\n{decl.declarant_name}  —  {decl.work_place}"))
        print(_hdr("─" * 100))
        report(decl, result, verbose=True, decl_type=raw.get("declaration_type", 1))
        summary_table([(decl, result)])
        return

    # ── mode: search ──
    print(_hdr(f"\nПошук: «{args.query}»"), end="")
    if args.year:   print(f"  рік={args.year}", end="")
    if args.edrpou: print(f"  ЄДРПОУ={args.edrpou}", end="")
    print()

    from typing import Any as _Any
    kwargs: dict[str, _Any] = {"query": args.query}
    if args.year and not args.all:
        kwargs["declaration_year"] = args.year
    if args.edrpou:
        kwargs["work_place_edrpou"] = args.edrpou

    r = client.list_documents(**kwargs)
    docs = r.get("data", [])[:args.limit]

    if not docs:
        print(_warn("  Нічого не знайдено"))
        return

    # deduplicate: by default keep latest annual (type=1) per year
    # with --all flag keep ALL declarations (all types, no dedup)
    if args.all:
        sorted_docs = sorted(docs, key=lambda x: (x.get("declaration_year", 0), x.get("date", "")))
    else:
        seen: dict[int, dict] = {}
        for d in docs:
            yr = d.get("declaration_year", 0)
            dtype = d.get("declaration_type", 99)
            prev = seen.get(yr)
            if prev is None:
                seen[yr] = d
            else:
                prev_type = prev.get("declaration_type", 99)
                if dtype == 1 and prev_type != 1:
                    seen[yr] = d
                elif dtype == prev_type and d.get("date", "") > prev.get("date", ""):
                    seen[yr] = d
        sorted_docs = sorted(seen.values(), key=lambda x: x.get("declaration_year", 0))

    # ── list only ──
    if args.list:
        print(f"  {'Рік':4s}  {'Тип':4s}  {'Подана':10s}  {'UUID':36s}  Місце роботи")
        print("  " + "─" * 90)
        for d in sorted_docs:
            s1 = d.get("data", {}).get("step_1", {})
            s1 = s1.get("data", s1) if isinstance(s1, dict) else s1
            wp = s1.get("workPlace", "")[:50]
            print(f"  {d.get('declaration_year',0):4d}  {d.get('declaration_type',0):4d}  "
                  f"{d.get('date','')[:10]:10s}  {d.get('id',''):36s}  {wp}")
        return

    # ── full analysis ──
    name = None
    rows: list[tuple[ParsedDeclaration, AnalysisResult]] = []

    for d in sorted_docs:
        doc_id = d.get("id")
        if not doc_id:
            continue
        try:
            full = client.get_document(doc_id)
            decl = parse_declaration(full)
            result = CatalaAnalyzer(decl).analyze()
            if name is None:
                name = decl.declarant_name
                print(_hdr(f"\n{name}  —  {decl.work_place}"))
                print(_hdr(f"ЄДРПОУ: {decl.work_place_edrpou}  Регіон: {decl.registration_region}"))
                print(_hdr("─" * 100))
            report(decl, result, verbose=args.verbose, decl_type=d.get("declaration_type", 1))
            rows.append((decl, result))
        except Exception as e:
            print(_warn(f"  [помилка {doc_id}: {e}]"))

    if rows:
        summary_table(rows)


if __name__ == "__main__":
    main()
