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
    recipient: str         # full name or "declarant" / "family:<relation>"


@dataclass
class PropertyItem:
    object_type: str       # "Житловий будинок", "Квартира", etc.
    area: Decimal | None
    country: str
    city: str
    owner: str             # "declarant" or "family:<relation>"
    ownership_type: str    # "Власність", "Оренда", etc.


@dataclass
class VehicleItem:
    brand: str
    model: str
    year: int | None
    object_type: str       # "Автомобіль легковий", etc.
    owner: str
    ownership_type: str
    cost_uah: Decimal | None


@dataclass
class BankAccount:
    bank_name: str
    bank_edrpou: str | None
    owner: str


@dataclass
class FamilyMember:
    member_id: str         # matches person_who_care / rightBelongs
    firstname: str
    lastname: str
    relation: str          # "дружина", "чоловік", "дитина", etc.


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
    cash_eur: Decimal = Decimal("0")
    cash_other: list[tuple[str, Decimal]] = field(default_factory=list)  # [(currency, amount)]
    family_members: list[FamilyMember] = field(default_factory=list)
    family_members_declared: bool = False
    properties: list[PropertyItem] = field(default_factory=list)
    vehicles: list[VehicleItem] = field(default_factory=list)
    bank_accounts: list[BankAccount] = field(default_factory=list)
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
    Handles both shallow (list API) and full (get_document) responses.
    """
    data = raw.get("data", raw)

    # Top-level fields present in list API response
    declaration_year_top = raw.get("declaration_year")
    declaration_type_top = raw.get("declaration_type")
    date_top = raw.get("date") or raw.get("lastmodified_date")

    def step_raw(n: int):
        """Return the raw step value (list or dict)."""
        key = f"step_{n}"
        s = data.get(key)
        if s is None:
            return None
        # Full API: value is {"data": <actual>}
        if isinstance(s, dict) and "data" in s:
            return s["data"]
        return s

    def step_dict(n: int) -> dict:
        v = step_raw(n)
        return v if isinstance(v, dict) else {}

    def step_list(n: int) -> list:
        v = step_raw(n)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            # dict with numeric/iteration keys — collect values
            return [vv for vv in v.values() if isinstance(vv, dict)]
        return []

    # ------------------------------------------------------------------
    # step_0 — declaration meta (most reliable source)
    # ------------------------------------------------------------------
    s0 = step_dict(0)
    declaration_year = int(
        declaration_year_top
        or s0.get("declaration_period")
        or s0.get("declaration_year")
        or 0
    )
    decl_type_raw = str(
        declaration_type_top
        or s0.get("declaration_type", "")
    )
    # step_0 may have Ukrainian label; top-level is numeric code
    decl_type_map = {
        "1": "annual", "2": "candidate", "3": "exit", "4": "family",
        "Щорічна": "annual", "Кандидат": "candidate",
        "Припинення": "exit", "Сімейна": "family",
    }
    declaration_type = decl_type_map.get(decl_type_raw, decl_type_raw or "annual")

    # Submission date
    submitted_date = None
    date_raw = date_top or data.get("date") or data.get("lastmodified_date")
    if date_raw:
        try:
            submitted_date = datetime.date.fromisoformat(str(date_raw)[:10])
        except Exception:
            pass

    # ------------------------------------------------------------------
    # step_1 — declarant personal info
    # ------------------------------------------------------------------
    s1 = step_dict(1)
    lastname  = s1.get("lastname", "")
    firstname = s1.get("firstname", "")
    middlename = s1.get("middlename", "")
    if firstname or middlename:
        declarant_name = f"{lastname} {firstname} {middlename}".strip()
    else:
        # list API: lastname already contains full name
        declarant_name = lastname.strip() or "Unknown"

    wp_raw = s1.get("workPlace", "")
    work_place = wp_raw.get("value", "") if isinstance(wp_raw, dict) else str(wp_raw or "")
    position = s1.get("workPost", "")

    # ------------------------------------------------------------------
    # step_2 — family members (list of person objects)
    # ------------------------------------------------------------------
    family_members: list[FamilyMember] = []
    for entry in step_list(2):
        if not isinstance(entry, dict):
            continue
        member_id = str(entry.get("id", ""))
        family_members.append(FamilyMember(
            member_id=member_id,
            firstname=entry.get("firstname", ""),
            lastname=entry.get("lastname", ""),
            relation=entry.get("subjectRelation", ""),
        ))
    family_members_declared = bool(family_members)

    # Build id → relation map for resolving person_who_care / rightBelongs
    _id_to_relation: dict[str, str] = {
        m.member_id: f"family:{m.relation} {m.firstname} {m.lastname}".strip()
        for m in family_members
    }

    def resolve_person(person_code: str) -> str:
        """Map person id to human-readable label."""
        if str(person_code) == "1":
            return declarant_name
        return _id_to_relation.get(str(person_code), f"family_member_{person_code}")

    def resolve_rights(rights: list) -> tuple[str, str]:
        """Return (owner_label, ownership_type) from a rights list."""
        if not rights:
            return declarant_name, ""
        r = rights[0]
        belongs = str(r.get("rightBelongs", r.get("rights_id", "1")))
        owner = resolve_person(belongs)
        ownership = r.get("ownershipType", "")
        return owner, ownership

    # ------------------------------------------------------------------
    # step_11 — incomes (list)
    # ------------------------------------------------------------------
    incomes: list[IncomeItem] = []
    for entry in step_list(11):
        if not isinstance(entry, dict):
            continue

        sources = entry.get("sources", [])
        src = sources[0] if sources else {}

        source_name = (
            src.get("source_ua_company_name")
            or " ".join(filter(None, [
                src.get("source_ua_lastname", ""),
                src.get("source_ua_firstname", ""),
                src.get("source_ua_middlename", ""),
            ])).strip()
            or src.get("source_citizen", "unknown")
        )
        source_edrpou = src.get("source_ua_company_code")
        source_type = src.get("source_citizen", "unknown")

        income_type = _normalize_income_type(str(entry.get("objectType", "")))
        amount = _parse_money(entry.get("sizeIncome") or entry.get("amount"))

        persons = entry.get("person_who_care", [{"person": "1"}])
        person_code = str(persons[0].get("person", "1")) if persons else "1"
        recipient = resolve_person(person_code)

        incomes.append(IncomeItem(
            source_name=str(source_name),
            source_edrpou=source_edrpou,
            source_type=str(source_type),
            income_type=income_type,
            amount_uah=amount,
            recipient=recipient,
        ))

    # ------------------------------------------------------------------
    # step_12 — cash & financial assets (list)
    # ------------------------------------------------------------------
    cash_uah = Decimal("0")
    cash_usd = Decimal("0")
    cash_eur = Decimal("0")
    cash_other: list[tuple[str, Decimal]] = []

    for entry in step_list(12):
        if not isinstance(entry, dict):
            continue
        obj_type = str(entry.get("objectType", "")).lower()
        if "готівков" not in obj_type and "cash" not in obj_type:
            continue
        amount = _parse_money(entry.get("sizeAssets") or entry.get("amount"))
        currency_raw = str(entry.get("assetsCurrency", "UAH")).upper()
        # Normalize: "UAH (УКРАЇНСЬКА ГРИВНЯ)" -> "UAH"
        currency = currency_raw.split("(")[0].strip()
        if currency == "UAH":
            cash_uah += amount
        elif currency == "USD":
            cash_usd += amount
        elif currency == "EUR":
            cash_eur += amount
        else:
            cash_other.append((currency, amount))

    # ------------------------------------------------------------------
    # step_3 — real estate (list)
    # ------------------------------------------------------------------
    properties: list[PropertyItem] = []
    for entry in step_list(3):
        if not isinstance(entry, dict):
            continue
        rights = entry.get("rights", [])
        owner, ownership_type = resolve_rights(rights)
        area_raw = entry.get("totalArea") or entry.get("livingArea")
        properties.append(PropertyItem(
            object_type=entry.get("objectType", ""),
            area=_parse_money(area_raw) if area_raw else None,
            country=str(entry.get("country", "")),
            city=str(entry.get("city", "")),
            owner=owner,
            ownership_type=ownership_type,
        ))

    # ------------------------------------------------------------------
    # step_6 — vehicles (list)
    # ------------------------------------------------------------------
    vehicles: list[VehicleItem] = []
    for entry in step_list(6):
        if not isinstance(entry, dict):
            continue
        rights = entry.get("rights", [])
        owner, ownership_type = resolve_rights(rights)
        cost_raw = entry.get("costDate")
        cost = _parse_money(cost_raw) if cost_raw and str(cost_raw) not in ("[Не застосовується]", "") else None
        yr_raw = entry.get("graduationYear")
        vehicles.append(VehicleItem(
            brand=str(entry.get("brand", "")),
            model=str(entry.get("model", "")),
            year=int(yr_raw) if yr_raw and str(yr_raw).isdigit() else None,
            object_type=str(entry.get("objectType", "")),
            owner=owner,
            ownership_type=ownership_type,
            cost_uah=cost,
        ))

    # ------------------------------------------------------------------
    # step_17 — bank accounts (list)
    # ------------------------------------------------------------------
    bank_accounts: list[BankAccount] = []
    seen_banks: set[str] = set()
    for entry in step_list(17):
        if not isinstance(entry, dict):
            continue
        bank_name = str(entry.get("establishment_ua_company_name", "") or entry.get("establishment_eng_company_name", ""))
        bank_edrpou = entry.get("establishment_ua_company_code")
        persons = entry.get("person_who_care", [{"person": "1"}])
        person_code = str(persons[0].get("person", "1")) if persons else "1"
        owner = resolve_person(person_code)
        key = f"{bank_name}|{owner}"
        if key not in seen_banks:
            seen_banks.add(key)
            bank_accounts.append(BankAccount(
                bank_name=bank_name,
                bank_edrpou=str(bank_edrpou) if bank_edrpou else None,
                owner=owner,
            ))

    return ParsedDeclaration(
        document_id=str(raw.get("id") or data.get("id") or ""),
        declarant_name=declarant_name,
        declaration_type=declaration_type,
        declaration_year=declaration_year,
        submitted_date=submitted_date,
        work_place=work_place,
        position=position,
        incomes=incomes,
        cash_usd=cash_usd,
        cash_uah=cash_uah,
        cash_eur=cash_eur,
        cash_other=cash_other,
        family_members=family_members,
        family_members_declared=family_members_declared,
        properties=properties,
        vehicles=vehicles,
        bank_accounts=bank_accounts,
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
        else:
            # Check if any income recipient is a family member not listed in step_2
            declared_ids = {m.member_id for m in self.decl.family_members}
            for inc in self.decl.incomes:
                if inc.recipient.startswith("family_member_"):
                    fid = inc.recipient.split("_")[-1]
                    if fid not in declared_ids:
                        result.warnings.append(
                            f"Income '{inc.source_name}' {inc.amount_uah:,.0f} UAH "
                            f"attributed to undeclared family member id={fid}"
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
    p_search.add_argument("--limit", type=int, default=10, help="Max results (default 10)")

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
            doc_id = doc.get("id", "")
            # list API returns shallow docs — fetch full for analysis
            if args.analyze and doc_id:
                try:
                    doc = client.get_document(doc_id)
                except Exception:
                    pass
            decl = parse_declaration(doc)
            if args.analyze:
                result = CatalaAnalyzer(decl).analyze()
                print(result.summary())
                print()
            else:
                print(f"{doc_id}  {decl.declarant_name}  {decl.declaration_year}  {decl.work_place}")
            if hasattr(args, "limit") and args.limit and found >= args.limit:
                break
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
