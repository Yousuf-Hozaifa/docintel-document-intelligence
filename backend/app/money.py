"""Decimal money helpers and currency metadata."""
import re
from decimal import Decimal, InvalidOperation

ISO_4217 = set("""AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD
HNL HRK HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD
MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON
RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX
USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL""".split())

MINOR_UNITS = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "UGX": 0, "XAF": 0, "XOF": 0, "XPF": 0,
               "BHD": 3, "KWD": 3, "OMR": 3, "JOD": 3, "TND": 3, "IQD": 3, "LYD": 3}

SYMBOLS = "$€£৳₹¥"
MONEY_RE = re.compile(rf"(?:[{SYMBOLS}]\s?)?-?\d[\d,]*(?:\.\d+)?(%)?")


def minor_places(currency: str | None) -> int:
    return MINOR_UNITS.get((currency or "").upper(), 2)


def parse_money(token: str) -> Decimal | None:
    t = re.sub(r"[^\d,.\-]", "", token or "")
    if not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        head, _, tail = t.rpartition(",")
        t = f"{head.replace(',', '')}.{tail}" if len(tail) <= 2 else t.replace(",", "")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def last_money(line: str) -> tuple[Decimal, str] | None:
    """Last monetary number on a line, ignoring percentages."""
    found = None
    for m in MONEY_RE.finditer(line):
        if m.group(1):  # percentage
            continue
        d = parse_money(m.group(0))
        if d is not None:
            found = (d, m.group(0).strip())
    return found
