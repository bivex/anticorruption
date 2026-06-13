"""
NAZK Public API SDK + Catala bridge
====================================
Wraps https://public-api.nazk.gov.ua/v2/ and converts declaration JSON
into Catala-generated Python dataclasses for rule evaluation.

Usage:
    from nazk_sdk import NazkClient, CatalaAnalyzer

    client = NazkClient()
    doc = client.get_document("210f5cfe-d5e9-4af8-8b6c-226b2eba6819")
    results = CatalaAnalyzer(doc).analyze()
    print(results)
"""

from __future__ import annotations

import datetime
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import urlencode

import requests

# ---------------------------------------------------------------------------
# Path setup — allow running from repo root or anticorruption/ directly
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))  # parent = catala/

# ---------------------------------------------------------------------------
# Catala runtime import (installed via opam / pip install catala-runtime)
# ---------------------------------------------------------------------------
try:
    from catala.catala_runtime import Money, Bool
    _CATALA_AVAILABLE = True
except ImportError:
    _CATALA_AVAILABLE = False

# ---------------------------------------------------------------------------
# Catala generated module
# ---------------------------------------------------------------------------
try:
    # When running as package (python -m anticorruption)
    import sys as _sys, os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    import anticorruption as _cat
except ImportError:
    try:
        # When running script directly inside anticorruption/
        import anticorruption as _cat  # type: ignore
    except ImportError:
        _cat = None  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_URL = "https://public-api.nazk.gov.ua/v2"

# Subsistence minimum (прожитковий мінімум) by year — UAH.
# Values per Cabinet of Ministers resolutions; update annually.
SUBSISTENCE_MINIMUM: dict[int, int] = {
    2024: 3028,
    2025: 3028,   # placeholder — update when CM sets 2025 value
    2026: 3028,   # placeholder
}

# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class NazkError(Exception):
    """Raised when the API returns an error code."""
    def __init__(self, code: int, url: str):
        super().__init__(f"NAZK API error {code} for {url}")
        self.code = code


class NazkClient:
    """Thin wrapper around the NAZK public API v2."""

    def __init__(self, base_url: str = BASE_URL, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Referer": "https://nazk.gov.ua/",
            "Origin": "https://nazk.gov.ua",
        })

    # ------------------------------------------------------------------
    # Core requests
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params, encoding='utf-8')}"
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            raise NazkError(data["error"], url)
        return data

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def get_document(self, document_id: str) -> dict:
        """Fetch a single declaration by UUID."""
        return self._get(f"/documents/{document_id}")

    def list_documents(
        self,
        query: str | None = None,
        full_search: bool = False,
        user_declarant_id: int | None = None,
        document_type: int | None = None,
        declaration_type: int | None = None,
        declaration_year: int | None = None,
        start_date: int | None = None,
        end_date: int | None = None,
        page: int = 1,
        work_place_edrpou: str | None = None,
        work_place: str | None = None,
        region_path: str | None = None,
        district_path: str | None = None,
        community_path: str | None = None,
        actual_region_path: str | None = None,
        actual_district_path: str | None = None,
        actual_community_path: str | None = None,
    ) -> dict:
        """
        Search / filter declarations.
        Returns raw API response with 'data' list and pagination info.
        """
        params: dict[str, Any] = {}
        if query is not None:
            params["query"] = query
        if full_search:
            params["full_search"] = 1
        if user_declarant_id is not None:
            params["user_declarant_id"] = user_declarant_id
        if document_type is not None:
            params["document_type"] = document_type
        if declaration_type is not None:
            params["declaration_type"] = declaration_type
        if declaration_year is not None:
            params["declaration_year"] = declaration_year
        if start_date is not None:
            params["start_date"] = start_date
        if end_date is not None:
            params["end_date"] = end_date
        if page != 1:
            params["page"] = page
        if work_place_edrpou is not None:
            params["workPlaceEdrpou"] = work_place_edrpou
        if work_place is not None:
            params["workPlace"] = work_place
        if region_path is not None:
            params["regionPath"] = region_path
        if district_path is not None:
            params["districtPath"] = district_path
        if community_path is not None:
            params["communityPath"] = community_path
        if actual_region_path is not None:
            params["actual_regionPath"] = actual_region_path
        if actual_district_path is not None:
            params["actual_districtPath"] = actual_district_path
        if actual_community_path is not None:
            params["actual_communityPath"] = actual_community_path
        return self._get("/documents/list", params)

    def iter_documents(self, **kwargs) -> Any:
        """
        Generator that pages through all results for a list query.
        Yields individual document dicts from 'data'.
        """
        for page in range(1, 101):
            resp = self.list_documents(page=page, **kwargs)
            docs = resp.get("data", [])
            if not docs:
                break
            yield from docs
            if len(docs) < 100:
                break

    # ------------------------------------------------------------------
    # Countries reference
    # ------------------------------------------------------------------

    def get_countries(self) -> list[dict]:
        """Fetch the countries reference list."""
        return self._get("/countries/list")


# ---------------------------------------------------------------------------
# Declaration parser — extracts typed fields from raw API JSON
# ---------------------------------------------------------------------------

@dataclass
class IncomeItem:
    source_name: str
    source_edrpou: str | None
    source_type: str       # "legal_entity_ua", "citizen_ua", "declarant", etc.
    income_type: str       # "salary", "gift_money", "business", "other_monetary", etc.
    amount_uah: Decimal
    recipient: str         # full name of who received it


@dataclass
class ParsedDeclaration:
    document_id: str
    declarant_name: str
    declaration_type: str   # "annual", "exit", "candidate", etc.
    declaration_year: int
    submitted_date: datetime.date | None
    work_place: str
    position: str
    incomes: list[IncomeItem] = field(default_factory=list)
    cash_usd: Decimal = Decimal("0")
    cash_uah: Decimal = Decimal("0")
    family_members_declared: bool = False
    raw: dict = field(default_factory=dict, repr=False)


_INCOME_TYPE_MAP = {
    # Ukrainian label fragment -> normalized key
    "Заробітна плата": "salary",
    "грошове забезпечення": "monetary_allowance",
    "підприємницькою діяльністю": "business",
    "відчуження рухомого": "movable_property_sale",
    "Подарунок у грошовій": "gift_money",
    "Подарунок у негрошовій": "gift_in_kind",
    "Додаткове благо": "additional_benefit",
    "Дивіденди": "dividends",
    "Відсотки": "interest",
    "Роялті": "royalty",
    "Оренда": "rental",
}


def _normalize_income_type(raw_type: str) -> str:
    for fragment, normalized in _INCOME_TYPE_MAP.items():
        if fragment.lower() in raw_type.lower():
            return normalized
    return "other"


def _parse_money(value: Any) -> Decimal:
    """Parse money from various formats: int, float, str '1 755 572'."""
    if value is None:
        return Decimal("0")
    s = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(s)
    except Exception:
        return Decimal("0")


def parse_declaration(raw: dict) -> ParsedDeclaration:
    """
    Convert a raw NAZK API document dict into a ParsedDeclaration.
    Handles both old (step_N arrays) and new (step_N.data) formats.
    """
    data = raw.get("data", raw)

    def step(n: int) -> dict:
        key = f"step_{n}"
        s = data.get(key, {})
        # New API: data is nested under .data
        if isinstance(s, dict) and "data" in s:
            return s["data"]
        return s if isinstance(s, dict) else {}

    # Step 1 — declaration type
    s1 = step(1)
    decl_type_raw = (
        data.get("declaration_type")
        or s1.get("declarationType", "")
    )
    decl_type_map = {"1": "annual", "2": "candidate", "3": "exit", "4": "family"}
    declaration_type = decl_type_map.get(str(decl_type_raw), str(decl_type_raw))

    declaration_year = int(data.get("declaration_year") or s1.get("declarationYear") or 0)

    # Submission date
    submitted_date = None
    date_raw = data.get("date") or data.get("lastmodified_date")
    if date_raw:
        try:
            submitted_date = datetime.date.fromisoformat(str(date_raw)[:10])
        except Exception:
            pass

    # Step 2.1 — declarant info
    s2 = step(2)
    declarant_name = (
        data.get("declarant_name")
        or (s2.get("lastname", "") + " " + s2.get("firstname", "") + " " + s2.get("middlename", "")).strip()
        or "Unknown"
    )
    work_place = (
        data.get("workPlace")
        or s2.get("workPlace", {}).get("value", "")
        or ""
    )
    position = s2.get("workPost", "")

    # Step 2.2 — family members
    s2_2 = data.get("step_2_2", {})
    if isinstance(s2_2, dict) and "data" in s2_2:
        s2_2 = s2_2["data"]
    family_members_declared = bool(s2_2)

    # Step 11 — incomes
    s11 = step(11)
    incomes: list[IncomeItem] = []

    income_entries = s11 if isinstance(s11, list) else s11.get("incomes", [])
    if isinstance(s11, dict):
        # Sometimes it's a dict with numeric keys
        income_entries = [v for k, v in s11.items() if isinstance(v, dict)]

    for entry in income_entries:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source", {}) or {}
        if isinstance(source, str):
            source_name = source
            source_edrpou = None
            source_type = "unknown"
        else:
            source_name = (
                source.get("ukrName")
                or source.get("citizen_pib")
                or source.get("ua_company_name")
                or source.get("objectType", "")
            )
            source_edrpou = source.get("ua_company_code")
            source_type = source.get("objectType", "unknown")

        income_type_raw = entry.get("incomeType", {})
        if isinstance(income_type_raw, dict):
            income_type_raw = income_type_raw.get("value", "")
        income_type = _normalize_income_type(str(income_type_raw))

        amount = _parse_money(entry.get("sizeIncome") or entry.get("amount"))

        recipient_info = entry.get("person", {}) or {}
        if isinstance(recipient_info, str):
            recipient = recipient_info
        else:
            recipient = (
                recipient_info.get("pib")
                or recipient_info.get("citizen_pib")
                or declarant_name
            )

        incomes.append(IncomeItem(
            source_name=str(source_name),
            source_edrpou=source_edrpou,
            source_type=str(source_type),
            income_type=income_type,
            amount_uah=amount,
            recipient=str(recipient),
        ))

    # Step 12 — cash assets
    s12 = step(12)
    cash_usd = Decimal("0")
    cash_uah = Decimal("0")
    cash_entries = s12 if isinstance(s12, list) else s12.get("assets", [])
    if isinstance(s12, dict):
        cash_entries = [v for k, v in s12.items() if isinstance(v, dict)]
    for entry in cash_entries:
        if not isinstance(entry, dict):
            continue
        amount_raw = entry.get("amount") or entry.get("sizeAssets") or 0
        currency = str(entry.get("currency", {}).get("value", "UAH") if isinstance(entry.get("currency"), dict) else entry.get("currency", "UAH")).upper()
        amount = _parse_money(amount_raw)
        if "USD" in currency:
            cash_usd += amount
        else:
            cash_uah += amount

    return ParsedDeclaration(
        document_id=str(data.get("id") or raw.get("id") or ""),
        declarant_name=declarant_name,
        declaration_type=declaration_type,
        declaration_year=declaration_year,
        submitted_date=submitted_date,
        work_place=work_place,
        position=position,
        incomes=incomes,
        cash_usd=cash_usd,
        cash_uah=cash_uah,
        family_members_declared=family_members_declared,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Catala bridge — maps ParsedDeclaration fields to Catala scope inputs
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    """Results of running all applicable Catala rules against a declaration."""
    declarant_name: str
    declaration_year: int

    # Gift checks
    gift_violations: list[dict] = field(default_factory=list)

    # Declaration threshold checks
    gift_income_threshold_uah: Decimal | None = None
    gift_incomes_over_threshold: list[dict] = field(default_factory=list)

    # Warnings (non-violations but noteworthy)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Declarant : {self.declarant_name}",
            f"Year      : {self.declaration_year}",
        ]
        if self.gift_violations:
            lines.append(f"VIOLATIONS ({len(self.gift_violations)}):")
            for v in self.gift_violations:
                lines.append(f"  - {v['description']}")
        else:
            lines.append("Violations: none detected")
        if self.warnings:
            lines.append(f"Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  ! {w}")
        return "\n".join(lines)


class CatalaAnalyzer:
    """
    Runs Catala-encoded rules against a ParsedDeclaration.

    Works in two modes:
    - With catala_runtime installed: calls the compiled anticorruption.py directly.
    - Without it: falls back to pure-Python reimplementation of the same rules.
    """

    def __init__(self, declaration: ParsedDeclaration):
        self.decl = declaration
        year = declaration.declaration_year or datetime.date.today().year
        self.subsistence_min = Decimal(
            str(SUBSISTENCE_MINIMUM.get(year, SUBSISTENCE_MINIMUM[max(SUBSISTENCE_MINIMUM)]))
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def analyze(self) -> AnalysisResult:
        result = AnalysisResult(
            declarant_name=self.decl.declarant_name,
            declaration_year=self.decl.declaration_year,
        )
        self._check_gifts(result)
        self._check_gift_income_threshold(result)
        self._check_family_members(result)
        self._check_cash(result)
        return result

    # ------------------------------------------------------------------
    # Art.23 — gift cap check
    # ------------------------------------------------------------------

    def _check_gifts(self, result: AnalysisResult) -> None:
        """
        Art.23 part 2: single gift <= 2 * subsistence_min_on_gift_day
        Annual from same source <= 4 * subsistence_min_jan1
        """
        single_cap = self.subsistence_min * 2
        annual_cap = self.subsistence_min * 4

        result.gift_violations  # ensure list exists

        for item in self.decl.incomes:
            if item.income_type not in ("gift_money", "gift_in_kind"):
                continue

            if _CATALA_AVAILABLE and _cat is not None:
                violation = self._catala_gift_check(item, single_cap, annual_cap)
            else:
                violation = self._py_gift_check(item, single_cap, annual_cap)

            if violation:
                result.gift_violations.append(violation)

    def _py_gift_check(
        self,
        item: IncomeItem,
        single_cap: Decimal,
        annual_cap: Decimal,
    ) -> dict | None:
        """Pure-Python fallback for Art.23 check."""
        if item.amount_uah > single_cap:
            return {
                "description": (
                    f"Gift from '{item.source_name}' of "
                    f"{item.amount_uah:,.0f} UAH exceeds single cap "
                    f"of {single_cap:,.0f} UAH (Art.23 p.2)"
                ),
                "amount": item.amount_uah,
                "cap": single_cap,
                "source": item.source_name,
                "rule": "Art.23 single_cap",
            }
        return None

    def _catala_gift_check(
        self,
        item: IncomeItem,
        single_cap: Decimal,
        annual_cap: Decimal,
    ) -> dict | None:
        """Call compiled Catala GiftRestriction scope."""
        try:
            amount_str = f"{item.amount_uah:.2f}"
            sub_str = f"{self.subsistence_min:.2f}"
            gift_event = _cat.GiftEvent(
                gift_value=Money(amount_str),
                annual_from_same_source=Money(amount_str),
                from_close_person=Bool(False),
                is_publicly_accessible_discount=Bool(False),
                is_official_travel_reimbursement=Bool(False),
            )
            sub_min = _cat.SubsistenceMinimum(
                on_jan1_of_year=Money(sub_str),
                on_gift_day=Money(sub_str),
            )
            out = _cat.gift_restriction(_cat.GiftRestrictionIn(
                gift_in=gift_event,
                subsistence_min_in=sub_min,
            ))
            if out.is_prohibited:
                return {
                    "description": (
                        f"Gift from '{item.source_name}' of "
                        f"{item.amount_uah:,.0f} UAH exceeds cap "
                        f"(Catala Art.23)"
                    ),
                    "amount": item.amount_uah,
                    "cap": single_cap,
                    "source": item.source_name,
                    "rule": "Art.23 catala",
                    "exceeds_single": out.exceeds_single_cap,
                    "exceeds_annual": out.exceeds_annual_cap,
                }
        except Exception as exc:
            # Catala call failed — fall back to pure Python
            return self._py_gift_check(item, single_cap, annual_cap)
        return None

    # ------------------------------------------------------------------
    # Art.46 p.7 — gift income disclosure threshold
    # ------------------------------------------------------------------

    def _check_gift_income_threshold(self, result: AnalysisResult) -> None:
        """
        Art.46 p.7: gift income must be declared if > 5 * subsistence_min_jan1.
        (They already declared it — we flag if any single gift exceeds threshold.)
        """
        threshold = self.subsistence_min * 5
        result.gift_income_threshold_uah = threshold
        for item in self.decl.incomes:
            if item.income_type in ("gift_money", "gift_in_kind"):
                if item.amount_uah > threshold:
                    result.gift_incomes_over_threshold.append({
                        "source": item.source_name,
                        "amount": item.amount_uah,
                        "threshold": threshold,
                        "description": (
                            f"Gift/income from '{item.source_name}' "
                            f"{item.amount_uah:,.0f} UAH > Art.46 p.7 threshold "
                            f"{threshold:,.0f} UAH — correctly declared"
                        ),
                    })

    # ------------------------------------------------------------------
    # Section 2.2 — family members
    # ------------------------------------------------------------------

    def _check_family_members(self, result: AnalysisResult) -> None:
        if not self.decl.family_members_declared:
            result.warnings.append(
                "Section 2.2 (family members) is empty — "
                "verify declarant is genuinely single with no cohabiting persons"
            )

    # ------------------------------------------------------------------
    # Cash assets plausibility
    # ------------------------------------------------------------------

    def _check_cash(self, result: AnalysisResult) -> None:
        """
        Flags if cash on hand > 6 months of salary (rough plausibility check).
        Not a legal rule — just a red-flag heuristic for analysts.
        """
        total_salary = sum(
            i.amount_uah for i in self.decl.incomes if i.income_type in ("salary", "monetary_allowance")
        )
        if total_salary <= 0:
            return
        monthly_salary = total_salary / 12
        # Convert USD to UAH at approximate rate
        usd_rate = Decimal("41")
        total_cash_uah = self.decl.cash_uah + self.decl.cash_usd * usd_rate
        if total_cash_uah > monthly_salary * 6:
            result.warnings.append(
                f"Cash on hand {total_cash_uah:,.0f} UAH "
                f"exceeds 6 months salary ({monthly_salary * 6:,.0f} UAH) — "
                "review income sources"
            )


# ---------------------------------------------------------------------------
# CLI convenience
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse, json

    parser = argparse.ArgumentParser(
        description="NAZK declaration fetcher + Catala rule analyzer"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # get
    p_get = sub.add_parser("get", help="Fetch and analyze a declaration by UUID")
    p_get.add_argument("document_id", help="Declaration UUID")

    # search
    p_search = sub.add_parser("search", help="Search declarations")
    p_search.add_argument("query", help="Name or keyword")
    p_search.add_argument("--year", type=int, help="Declaration year")
    p_search.add_argument("--edrpou", help="Workplace EDRPOU code")
    p_search.add_argument("--analyze", action="store_true", help="Run Catala rules on each result")

    # analyze-json
    p_aj = sub.add_parser("analyze-json", help="Analyze a declaration JSON file")
    p_aj.add_argument("file", help="Path to JSON file")

    args = parser.parse_args()
    client = NazkClient()

    if args.cmd == "get":
        raw = client.get_document(args.document_id)
        decl = parse_declaration(raw)
        result = CatalaAnalyzer(decl).analyze()
        print(result.summary())

    elif args.cmd == "search":
        kwargs: dict[str, Any] = {"query": args.query}
        if args.year:
            kwargs["declaration_year"] = args.year
        if args.edrpou:
            kwargs["work_place_edrpou"] = args.edrpou
        found = 0
        for doc in client.iter_documents(**kwargs):
            found += 1
            decl = parse_declaration(doc)
            if args.analyze:
                result = CatalaAnalyzer(decl).analyze()
                print(result.summary())
                print()
            else:
                print(f"{doc.get('id', '?')}  {decl.declarant_name}  {decl.declaration_year}  {decl.work_place}")
        if not found:
            print("No results.")

    elif args.cmd == "analyze-json":
        with open(args.file, encoding="utf-8") as f:
            raw = json.load(f)
        decl = parse_declaration(raw)
        result = CatalaAnalyzer(decl).analyze()
        print(result.summary())


if __name__ == "__main__":
    _cli()
