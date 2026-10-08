"""Money formatting and parsing helpers (USD, integer cents)."""

from __future__ import annotations

import re

# "$1,234.56", "USD 1234.5", "1,234.56 dollars", "75 dollar". A bare number without a currency marker is ignored.
_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
MONEY_RE = re.compile(rf"(?:US\$|\$|USD\s?)\s?{_NUM}|{_NUM}\s?(?:USD|dollars?\b)", re.IGNORECASE)


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def parse_money_cents(text: str) -> list[int]:
    """All currency-marked amounts in `text`, in order, as integer cents."""
    out: list[int] = []
    for m in MONEY_RE.finditer(text):
        whole, frac = (m.group(1), m.group(2)) if m.group(1) is not None else (m.group(3), m.group(4))
        cents = int(whole.replace(",", "")) * 100 + (int(frac.ljust(2, "0")) if frac else 0)
        out.append(cents)
    return out


def to_cents(value: object) -> int | None:
    """Judge-reported value -> cents. Ints are already cents; strings may be '1250' or '$12.50'."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if re.fullmatch(r"-?\d+", stripped):
            return int(stripped)
        found = parse_money_cents(value)
        return found[0] if found else None
    return None
