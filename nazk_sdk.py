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
# ---------------------------------------------------------------------------
# Constants & Error codes & Countries Map
# ---------------------------------------------------------------------------

NAZK_ERROR_CODES: dict[int, str] = {
    1310002: "Документ не знайдено (неіснуючий ідентифікатор)",
    404: "Сторінку не знайдено",
    1310101: "Пошуковий запит (query) має бути від 3 до 255 символів",
    1310111: "Некоректний ID суб'єкта декларування (user_declarant_id)",
    1310112: "Некоректний ID суб'єкта декларування (user_declarant_id)",
    1310121: "Некоректний тип документа (document_type, має бути від 1 до 3)",
    1310122: "Некоректний тип документа (document_type, має бути від 1 до 3)",
    1310131: "Некоректний тип декларації (declaration_type, має бути від 1 до 4)",
    1310132: "Некоректний тип декларації (declaration_type, має бути від 1 до 4)",
    1310141: "Некоректний рік декларації (declaration_year, має бути від 2015 до поточної дати)",
    1310142: "Некоректний рік декларації (declaration_year, має бути від 2015 до поточної дати)",
    1310151: "Некоректна дата початку подання (start_date, UNIX timestamp)",
    1310152: "Некоректна дата початку подання (start_date, UNIX timestamp)",
    1310161: "Некоректна дата кінця подання (end_date, UNIX timestamp)",
    1310162: "Некоректна дата кінця подання (end_date, UNIX timestamp)",
    1310171: "Некоректний номер сторінки (page, має бути від 1 до 100)",
    1310172: "Некоректний номер сторінки (page, має бути від 1 до 100)",
}

COUNTRIES_MAP: dict[str, str] = {
    "1": "Україна",
    "2": "Австралія",
    "3": "Австрія",
    "4": "Азербайджан",
    "5": "Аландські острови",
    "6": "Албанія",
    "7": "Алжир",
    "8": "Американське Самоа",
    "9": "Американські Віргінські острови",
    "10": "Ангілья",
    "11": "Ангола",
    "12": "Андорра",
    "13": "Антарктида",
    "14": "Антигуа і Барбуда",
    "15": "Аргентина",
    "16": "Аруба",
    "17": "Афганістан",
    "18": "Багамські Острови",
    "19": "Бангладеш",
    "20": "Барбадос",
    "21": "Бахрейн",
    "22": "Беліз",
    "23": "Бельгія",
    "24": "Бенін",
    "25": "Бермудські острови",
    "26": "Білорусь",
    "27": "Болгарія",
    "28": "Болівія",
    "29": "Боснія і Герцеговина",
    "30": "Ботсвана",
    "31": "Бразилія",
    "32": "Британська територія в Індійському океані",
    "33": "Британські Віргінські острови",
    "34": "Бруней",
    "35": "Буркіна-Фасо",
    "36": "Бурунді",
    "37": "Бутан",
    "38": "Вануату",
    "39": "Ватикан",
    "40": "Велика Британія",
    "41": "Венесуела",
    "42": "В'єтнам",
    "43": "Вірменія",
    "44": "Волліс і Футуна",
    "45": "Габон",
    "46": "Гаїті",
    "47": "Гаяна",
    "48": "Гамбія",
    "49": "Гана",
    "50": "Гваделупа",
    "51": "Гватемала",
    "52": "Гвінея",
    "53": "Гвінея-Бісау",
    "54": "Гернсі",
    "55": "Гондурас",
    "56": "Гонконг",
    "57": "Гренада",
    "58": "Греція",
    "59": "Грузія",
    "60": "Гуам",
    "61": "Гібралтар",
    "62": "Ґренландія",
    "63": "Данія",
    "64": "ДР Конго",
    "65": "Джерсі",
    "66": "Джибуті",
    "67": "Домініка",
    "68": "Домініканська Республіка",
    "69": "Зовнішні малі острови США",
    "70": "Еквадор",
    "71": "Екваторіальна Гвінея",
    "72": "Еритрея",
    "73": "Естонія",
    "74": "Ефіопія",
    "75": "Єгипет",
    "76": "Ємен",
    "77": "Замбія",
    "78": "Західна Сахара",
    "79": "Зімбабве",
    "80": "Ізраїль",
    "81": "Індія",
    "82": "Індонезія",
    "83": "Ірак",
    "84": "Іран",
    "85": "Ірландія",
    "86": "Ісландія",
    "87": "Іспанія",
    "88": "Італія",
    "89": "Йорданія",
    "90": "Кабо-Верде",
    "91": "Казахстан",
    "92": "Кайманові острови",
    "93": "Камбоджа",
    "94": "Камерун",
    "95": "Канада",
    "96": "Катар",
    "97": "Кенія",
    "98": "Киргизстан",
    "99": "КНР",
    "100": "Північна Корея",
    "101": "Кіпр",
    "102": "Кірибаті",
    "103": "Кокосові острови (Кілінг)",
    "104": "Колумбія",
    "105": "Коморські Острови",
    "106": "Конго",
    "107": "Коста-Рика",
    "108": "Кот-д'Івуар",
    "109": "Куба",
    "110": "Кувейт",
    "111": "Лаос",
    "112": "Латвія",
    "113": "Лесото",
    "114": "Литва",
    "115": "Ліберія",
    "116": "Ліван",
    "117": "Лівія",
    "118": "Ліхтенштейн",
    "119": "Люксембург",
    "120": "Маврикій",
    "121": "Мавританія",
    "122": "Мадагаскар",
    "123": "Майотта",
    "124": "Макао",
    "125": "Македонія",
    "126": "Малаві",
    "127": "Малайзія",
    "128": "Малі",
    "129": "Мальдіви",
    "130": "Мальта",
    "131": "Марокко",
    "132": "Мартиніка",
    "133": "Маршаллові Острови",
    "134": "Мексика",
    "135": "Мозамбік",
    "136": "Молдова",
    "137": "Монако",
    "138": "Монголія",
    "139": "Монтсеррат",
    "140": "М'янма",
    "141": "Намібія",
    "142": "Науру",
    "143": "Непал",
    "144": "Нігер",
    "145": "Нігерія",
    "146": "Нідерланди",
    "147": "Нідерландські Антильські острови",
    "148": "Нікарагуа",
    "149": "Німеччина",
    "150": "Ніуе",
    "151": "Нова Зеландія",
    "152": "Нова Каледонія",
    "153": "Норвегія",
    "154": "ОАЕ",
    "155": "Оман",
    "156": "Острів Буве",
    "157": "Острів Мен",
    "158": "Острів Норфолк",
    "159": "Острів Різдва",
    "160": "Острови Святої Єлени, Вознесіння і Тристан-да-Кунья",
    "161": "Острови Герд і Макдональд",
    "162": "Острови Кука",
    "163": "Пакистан",
    "164": "Палау",
    "165": "Палестина",
    "166": "Панама",
    "167": "Папуа - Нова Гвінея",
    "168": "Парагвай",
    "169": "Перу",
    "170": "Південна Джорджія та Південні Сандвічеві острови",
    "171": "Південна Корея",
    "172": "Південний Судан",
    "173": "ПАР",
    "174": "Північні Маріанські острови",
    "175": "Піткерн",
    "176": "Польща",
    "177": "Португалія",
    "178": "Пуерто-Рико",
    "179": "Реюньйон",
    "180": "Росія",
    "181": "Руанда",
    "182": "Румунія",
    "183": "Сальвадор",
    "184": "Самоа",
    "185": "Сан-Марино",
    "186": "Сан-Томе і Принсіпі",
    "187": "Саудівська Аравія",
    "188": "Свазіленд",
    "189": "Свальбард і Ян-Маєн",
    "190": "Сейшельські Острови",
    "191": "Сен-Бартельмі",
    "192": "Сенегал",
    "193": "Сен-Мартін",
    "194": "Сен-П'єр і Мікелон",
    "195": "Сент-Вінсент і Гренадини",
    "196": "Сент-Кіттс і Невіс",
    "197": "Сент-Люсія",
    "198": "Сербія",
    "199": "Сирія",
    "200": "Сінгапур",
    "201": "Словаччина",
    "202": "Словенія",
    "203": "Соломонові Острови",
    "204": "Сомалі",
    "205": "США",
    "206": "Судан",
    "207": "Суринам",
    "208": "Східний Тимор",
    "209": "Сьєрра-Леоне",
    "210": "Таджикистан",
    "211": "Таїланд",
    "212": "Тайвань",
    "213": "Танзанія",
    "214": "Острови Теркс і Кайкос",
    "215": "Того",
    "216": "Токелау",
    "217": "Тонга",
    "218": "Тринідад і Тобаго",
    "219": "Тувалу",
    "220": "Туніс",
    "221": "Туреччина",
    "222": "Туркменістан",
    "223": "Уганда",
    "224": "Угорщина",
    "225": "Узбекистан",
    "227": "Уругвай",
    "228": "Фарерські острови",
    "229": "Федеративні Штати Мікронезії",
    "230": "Фіджі",
    "231": "Філіппіни",
    "232": "Фінляндія",
    "233": "Фолклендські (Мальвінські) острови",
    "234": "Франція",
    "235": "Французька Гвіана",
    "236": "Французька Полінезія",
    "237": "Французькі Південні території",
    "238": "Хорватія",
    "239": "Центральноафриканська Республіка",
    "240": "Чад",
    "241": "Чехія",
    "242": "Чилі",
    "243": "Чорногорія",
    "244": "Швейцарія",
    "245": "Швеція",
    "246": "Шрі-Ланка",
    "247": "Ямайка",
    "248": "Японія",
}


class NazkError(Exception):
    """Raised when the API returns an error code."""
    def __init__(self, code: int, url: str):
        desc = NAZK_ERROR_CODES.get(code, "Невідома помилка API")
        super().__init__(f"NAZK API error {code} ({desc}) for {url}")
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
        self._countries_cache: dict[str, str] | None = None

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

    def _fetch_document_html(self, document_id: str) -> dict:
        """Fallback: fetch declaration HTML from public registry and convert to API structure."""
        from bs4 import BeautifulSoup
        import re
        url = f"https://public.nazk.gov.ua/documents/{document_id}"
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        def get_field_val(soup_node, label: str) -> str:
            if not soup_node:
                return ""
            for span in soup_node.find_all("span", class_="info-title"):
                if label.lower() in span.get_text().lower():
                    v = span.find_next_sibling("span", class_="info-value")
                    if v:
                        return v.get_text(strip=True)
            for div in soup_node.find_all("div", class_=re.compile(r"color-1")):
                if label.lower() in div.get_text().lower():
                    v = div.find_next_sibling("div", class_=re.compile(r"border-bottom"))
                    if v:
                        return v.get_text(strip=True)
            return ""

        # Step 0
        s0_card = soup.find(id="step-data-0")
        s0_text = s0_card.get_text(" ", strip=True) if s0_card else ""
        decl_type = 1
        if "При звільненні" in s0_text or "при звільненні" in s0_text:
            decl_type = 3
        elif "кандидат" in s0_text.lower():
            decl_type = 2
        elif "виправлена" in s0_text.lower():
            decl_type = 4

        year_m = re.search(r"\b(20\d{2})\b", s0_text)
        year = int(year_m.group(1)) if year_m else 0

        # Step 1
        s1 = soup.find(id="step-data-1")
        lastname = get_field_val(s1, "Прізвище")
        firstname = get_field_val(s1, "Імʼя") or get_field_val(s1, "Ім’я") or get_field_val(s1, "Ім'я")
        middlename = get_field_val(s1, "По батькові")
        fullname = f"{lastname} {firstname} {middlename}".strip()
        workplace = get_field_val(s1, "Найменування місця роботи")
        post = get_field_val(s1, "Займана посада")
        edrpou = get_field_val(s1, "Код в Єдиному державному реєстрі")
        region = get_field_val(s1, "Автономна Республіка Крим/область/місто зі спеціальним статусом")
        post_category = get_field_val(s1, "Категорія посади")
        post_type = get_field_val(s1, "Тип посади")

        # Step 11 (incomes)
        incomes = {}
        s11 = soup.find(id="step-data-11")
        if s11:
            trs = s11.find_all("tr")
            for idx, tr in enumerate(trs[1:]):
                tds = tr.find_all(["th", "td"])
                if len(tds) < 4:
                    continue
                src_txt = tds[1].get_text("\n", strip=True)
                inc_type = tds[2].get_text(strip=True)
                amt = tds[3].get_text(strip=True).replace(" ", "").replace(",", ".")
                rec_txt = tds[4].get_text(" ", strip=True) if len(tds) > 4 else fullname

                edrpou_m = re.search(r"\b(\d{8})\b", src_txt)
                src_edrpou = edrpou_m.group(1) if edrpou_m else ""
                name_m = re.search(r"Найменування:\s*([^\n]+)", src_txt)
                src_name = name_m.group(1).strip() if name_m else (src_txt.splitlines()[-1] if src_txt.splitlines() else "")

                incomes[str(idx + 1)] = {
                    "iteration": str(idx + 1),
                    "source_name": src_name,
                    "source_edrpou": src_edrpou,
                    "source_citizen": "Юридична особа, зареєстрована в Україні" if "Юридична особа" in src_txt else "Громадянин України",
                    "objectType": inc_type,
                    "sizeIncome": amt,
                    "rights": {
                        "1": {
                            "citizen": fullname
                        }
                    }
                }

        # Step 12 (cash)
        cash_assets = {}
        s12 = soup.find(id="step-data-12")
        if s12:
            trs = s12.find_all("tr")
            for idx, tr in enumerate(trs[1:]):
                tds = tr.find_all(["th", "td"])
                if len(tds) < 4:
                    continue
                cash_assets[str(idx + 1)] = {
                    "objectType": tds[1].get_text(strip=True),
                    "sizeAssets": tds[2].get_text(strip=True).replace(" ", ""),
                    "assetsCurrency": "UAH",
                }

        return {
            "id": document_id,
            "declaration_type": decl_type,
            "declaration_year": year,
            "data": {
                "step_0": {
                    "data": {
                        "declaration_type": decl_type,
                        "declaration_period": year,
                        "declaration_year": year,
                    }
                },
                "step_1": {
                    "data": {
                        "lastname": lastname,
                        "firstname": firstname,
                        "middlename": middlename,
                        "workPlace": workplace,
                        "workPost": post,
                        "workPlaceEdrpou": edrpou,
                        "actual_region": region,
                        "postCategory": post_category,
                        "postType": post_type,
                    }
                },
                "step_2": {"data": {}},
                "step_11": {"data": incomes},
                "step_12": {"data": cash_assets},
            }
        }

    def _search_web_html(self, query: str, page: int = 1) -> dict:
        """Fallback: search public registry HTML and return list structure."""
        from bs4 import BeautifulSoup
        import re
        url = f"https://public.nazk.gov.ua/documents/list?q={urlencode({'q': query})[2:]}&page={page}"
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        items = []
        links = soup.find_all("a", href=re.compile(r"^/documents/[0-9a-f-]{36}$"))
        seen = set()
        for a in links:
            href = a["href"]
            if href in seen:
                continue
            seen.add(href)
            doc_id = href.split("/")[-1]
            container = a
            while container and container.name != "body":
                if container.find("span", class_="info-1"):
                    break
                container = container.parent
            text = " ".join(container.stripped_strings) if container else ""
            date_m = re.search(r"([0-9]{2}\.[0-9]{2}\.[0-9]{4}\s+[0-9]{2}:[0-9]{2})", text)
            date_str = date_m.group(1) if date_m else ""
            
            decl_type = 1
            if "кандидат" in text.lower():
                decl_type = 2
            elif "звільнен" in text.lower():
                decl_type = 3
            elif "виправлена" in text.lower():
                decl_type = 4
                
            year_m = re.search(r"(?:20\d{2})", text)
            year = int(year_m.group(0)) if year_m else 0
            
            wp_m = re.search(r"Посада:\s*([^H]+?)(?=HTML|$)", text)
            wp = wp_m.group(1).strip() if wp_m else ""
            
            items.append({
                "id": doc_id,
                "date": date_str,
                "declaration_type": decl_type,
                "declaration_year": year,
                "data": {"step_1": {"data": {"workPlace": wp}}}
            })
        return {"data": items}

    def get_document(self, document_id: str) -> dict:
        """Fetch a single declaration by UUID, falling back to registry HTML on 403."""
        try:
            return self._get(f"/documents/{document_id}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                return self._fetch_document_html(document_id)
            raise

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
        city_path: str | None = None,
        actual_region_path: str | None = None,
        actual_district_path: str | None = None,
        actual_community_path: str | None = None,
        actual_city_path: str | None = None,
    ) -> dict:
        """
        Search / filter declarations with client-side validation.
        Returns raw API response with 'data' list and pagination info.
        """
        params: dict[str, Any] = {}
        
        if query is not None:
            if not isinstance(query, str) or not (3 <= len(query) <= 255):
                raise ValueError("query (пошуковий запит) має бути текстовим полем від 3 до 255 символів")
            params["query"] = query
            
        if full_search:
            params["full_search"] = 1
            
        if user_declarant_id is not None:
            if not isinstance(user_declarant_id, int) or not (1 <= user_declarant_id <= 10000000):
                raise ValueError("user_declarant_id (ID суб’єкта) має бути числом від 1 до 10000000")
            params["user_declarant_id"] = user_declarant_id
            
        if document_type is not None:
            if not isinstance(document_type, int) or not (1 <= document_type <= 3):
                raise ValueError("document_type (тип документа) має бути числом від 1 до 3")
            params["document_type"] = document_type
            
        if declaration_type is not None:
            if not isinstance(declaration_type, int) or not (1 <= declaration_type <= 4):
                raise ValueError("declaration_type (тип декларації) має бути числом від 1 до 4")
            params["declaration_type"] = declaration_type
            
        if declaration_year is not None:
            current_year = datetime.date.today().year
            if not isinstance(declaration_year, int) or not (2015 <= declaration_year <= current_year):
                raise ValueError(f"declaration_year (рік декларації) має бути числом від 2015 до {current_year}")
            params["declaration_year"] = declaration_year
            
        if start_date is not None:
            current_time = int(time.time())
            if not isinstance(start_date, int) or not (1470009600 <= start_date <= current_time):
                raise ValueError("start_date (початок дати подання) має бути UNIX-timestamp від 1470009600 (01.08.2016) до поточної дати")
            params["start_date"] = start_date
            
        if end_date is not None:
            current_time = int(time.time())
            if not isinstance(end_date, int) or not (1470009600 <= end_date <= current_time):
                raise ValueError("end_date (кінець дати подання) має бути UNIX-timestamp від 1470009600 (01.08.2016) до поточної дати")
            params["end_date"] = end_date
            
        if page is not None:
            if not isinstance(page, int) or not (1 <= page <= 100):
                raise ValueError("page (№ сторінки) має бути числом від 1 до 100")
            if page != 1:
                params["page"] = page
            
        if work_place_edrpou is not None:
            if isinstance(work_place_edrpou, int):
                wp_edrpou_str = f"{work_place_edrpou:08d}"
            else:
                wp_edrpou_str = str(work_place_edrpou).strip()
            if not wp_edrpou_str.isdigit() or len(wp_edrpou_str) != 8:
                raise ValueError("work_place_edrpou (ЄДРПОУ) має бути числовим кодом довжиною 8 символів")
            params["workPlaceEdrpou"] = wp_edrpou_str
            
        if work_place is not None:
            params["workPlace"] = str(work_place)
            
        if region_path is not None:
            params["regionPath"] = region_path
        if district_path is not None:
            params["districtPath"] = district_path
        if community_path is not None:
            params["communityPath"] = community_path
        if city_path is not None:
            params["cityPath"] = city_path
            
        if actual_region_path is not None:
            params["actual_regionPath"] = actual_region_path
        if actual_district_path is not None:
            params["actual_districtPath"] = actual_district_path
        if actual_community_path is not None:
            params["actual_communityPath"] = actual_community_path
        if actual_city_path is not None:
            params["actual_cityPath"] = actual_city_path
            
        try:
            return self._get("/documents/list", params)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403 and query:
                return self._search_web_html(query, page=page or 1)
            raise

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

    def get_countries(self) -> dict[str, str]:
        """Fetch the countries reference list."""
        if self._countries_cache is None:
            self._countries_cache = self._get("/countries/list")
        return self._countries_cache


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
class SecurityItem:
    issuer: str
    security_type: str   # "Акції", "Облігації", etc.
    quantity: Decimal | None
    nominal_uah: Decimal | None
    owner: str


@dataclass
class DepositItem:
    bank_name: str
    bank_edrpou: str | None
    currency: str
    amount: Decimal
    owner: str


@dataclass
class TransactionItem:
    transaction_type: str   # "Найм (оренда)", "Купівля-продаж", etc.
    subject: str            # "Транспортний засіб", "Нерухоме майно", etc.
    date: str
    cost_uah: Decimal | None


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
    securities: list[SecurityItem] = field(default_factory=list)
    deposits: list[DepositItem] = field(default_factory=list)
    transactions: list[TransactionItem] = field(default_factory=list)
    work_place_edrpou: str = ""
    registration_region: str = ""
    responsible_position: int | None = None
    post_type: int | None = None
    post_category: int | None = None
    corruption_affected: int | None = None
    schema_version: int | None = None
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


def parse_declaration(raw: dict, countries_map: dict[str, str] | None = None) -> ParsedDeclaration:
    """
    Convert a raw NAZK API document dict into a ParsedDeclaration.
    Handles both shallow (list API) and full (get_document) responses.
    """
    data = raw.get("data", raw)

    # Top-level fields present in list API response or full document
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

    # Duplicate top-level fields / step 2.1 properties
    def _parse_int(val: Any) -> int | None:
        if val is None or str(val) in ("", "[Не застосовується]", "null"):
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    responsible_position = _parse_int(raw.get("responsible_position") or s1.get("responsiblePosition"))
    post_type = _parse_int(raw.get("post_type") or s1.get("postType"))
    post_category = _parse_int(raw.get("post_category") or s1.get("postCategory"))
    corruption_affected = _parse_int(raw.get("corruption_affected") or s1.get("corruptionAffected"))
    schema_version = _parse_int(raw.get("schema_version"))

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
            or entry.get("source_ua_company_name")
            or " ".join(filter(None, [
                src.get("source_ua_lastname") or entry.get("source_ua_lastname", ""),
                src.get("source_ua_firstname") or entry.get("source_ua_firstname", ""),
                src.get("source_ua_middlename") or entry.get("source_ua_middlename", ""),
            ])).strip()
            or src.get("source_citizen")
            or entry.get("source_citizen")
            or "unknown"
        )
        source_edrpou = src.get("source_ua_company_code") or entry.get("source_ua_company_code")
        source_type = src.get("source_citizen") or entry.get("source_citizen") or "unknown"

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
    cmap = countries_map or COUNTRIES_MAP
    for entry in step_list(3):
        if not isinstance(entry, dict):
            continue
        rights = entry.get("rights", [])
        owner, ownership_type = resolve_rights(rights)
        area_raw = entry.get("totalArea") or entry.get("livingArea")
        
        country_id = str(entry.get("country", ""))
        country_name = cmap.get(country_id, country_id)
        
        properties.append(PropertyItem(
            object_type=entry.get("objectType", ""),
            area=_parse_money(area_raw) if area_raw else None,
            country=country_name,
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

    # ------------------------------------------------------------------
    # step_1 extras — workplace EDRPOU, registration region
    # ------------------------------------------------------------------
    work_place_edrpou = str(s1.get("workPlaceEdrpou", "") or "")
    registration_region = str(s1.get("region", "") or "")

    # ------------------------------------------------------------------
    # step_4 — securities (list)
    # ------------------------------------------------------------------
    securities: list[SecurityItem] = []
    for entry in step_list(4):
        if not isinstance(entry, dict):
            continue
        rights = entry.get("rights", [])
        owner, _ = resolve_rights(rights)
        qty_raw = entry.get("quantity") or entry.get("quantityAcquired")
        nom_raw = entry.get("cost") or entry.get("nominal")
        securities.append(SecurityItem(
            issuer=str(entry.get("issuer_ua_company_name") or entry.get("issuer_eng_company_name", "")),
            security_type=str(entry.get("objectType", "")),
            quantity=_parse_money(qty_raw) if qty_raw else None,
            nominal_uah=_parse_money(nom_raw) if nom_raw else None,
            owner=owner,
        ))

    # ------------------------------------------------------------------
    # step_12 — cash AND deposits/other financial assets
    # ------------------------------------------------------------------
    deposits: list[DepositItem] = []
    for entry in step_list(12):
        if not isinstance(entry, dict):
            continue
        obj_type = str(entry.get("objectType", "")).lower()
        amount = _parse_money(entry.get("sizeAssets") or entry.get("amount"))
        currency_raw = str(entry.get("assetsCurrency", "UAH")).upper()
        currency = currency_raw.split("(")[0].strip()

        if "готівков" in obj_type or "cash" in obj_type:
            # already handled above
            pass
        else:
            # deposits, savings accounts, other monetary assets
            rights = entry.get("rights", [])
            owner, _ = resolve_rights(rights)
            org = entry.get("organization", "") or ""
            org_code = entry.get("organization_code") or entry.get("organization_edrpou")
            deposits.append(DepositItem(
                bank_name=str(org),
                bank_edrpou=str(org_code) if org_code else None,
                currency=currency,
                amount=amount,
                owner=owner,
            ))

    # ------------------------------------------------------------------
    # step_14 — significant transactions (list)
    # ------------------------------------------------------------------
    transactions: list[TransactionItem] = []
    for entry in step_list(14):
        if not isinstance(entry, dict):
            continue
        # cost may be nested in expenses dict
        cost = None
        expenses = entry.get("expenses", {})
        if isinstance(expenses, dict):
            for exp in expenses.values():
                if isinstance(exp, dict):
                    c = exp.get("costAmount")
                    if c and str(c) not in ("[Не застосовується]", ""):
                        cost = _parse_money(c)
                        break
        transactions.append(TransactionItem(
            transaction_type=str(entry.get("specExpenses", "") or entry.get("transactionType", "")),
            subject=str(entry.get("specExpensesSubject", "") or entry.get("objectType", "")),
            date=str(entry.get("transactionDate", "")),
            cost_uah=cost,
        ))

    return ParsedDeclaration(
        document_id=str(raw.get("id") or data.get("id") or ""),
        declarant_name=declarant_name,
        declaration_type=declaration_type,
        declaration_year=declaration_year,
        submitted_date=submitted_date,
        work_place=work_place,
        work_place_edrpou=work_place_edrpou,
        registration_region=registration_region,
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
        securities=securities,
        deposits=deposits,
        transactions=transactions,
        responsible_position=responsible_position,
        post_type=post_type,
        post_category=post_category,
        corruption_affected=corruption_affected,
        schema_version=schema_version,
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
