"""Expected and forbidden facts, and how an answer is checked against them (framework 6).

A question carries `expected_facts` (what a correct answer must state) and
`forbidden_facts` (plausible wrong statements). Both are `Fact`s. This module is the single
definition of what "an answer states a fact" means, used by the question generator to
self-check every question it writes and by the benchmark scorer. It has no dependency on
services/ (the benchmark must not grade the system with the system's own code).

Matching is deliberately simple, case-insensitive, whitespace-normalised and heuristic;
framework section 9 lists the false positives and negatives this can produce, which is why
flagged responses are audited by a human.

Fact kinds
  token            any accepted string appears as a whole token ("7.2" is not inside "17.2",
                   "2026-04" is not inside "2026-04-10")
  stem             any accepted string appears anywhere (for direction words: "decreas")
  any_date         the answer states some full calendar date
  day_in_month     the answer states a full date inside the month `param` ("2026-04")
  value_near_name  the answer puts a number (param "number") or a dose (param "dose") right
                   after one of the accepted names, e.g. "LDL was 130"; calendar dates are
                   ignored, and a match is dropped if another lab or drug name sits between
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from evaluation.catalogs import LABS, MEDS

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
MONTH_ABBR = tuple(m[:3] for m in MONTHS)
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                "seventeen", "eighteen", "nineteen", "twenty")

KINDS = ("token", "stem", "any_date", "day_in_month", "value_near_name")


@dataclass(frozen=True)
class Fact:
    label: str
    kind: str
    accepted: tuple = ()
    param: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown fact kind {self.kind!r}")
        if self.kind in ("token", "stem", "value_near_name") and not self.accepted:
            raise ValueError(f"{self.kind} fact needs at least one accepted string")
        if self.kind == "day_in_month" and not re.fullmatch(r"\d{4}-\d{2}", self.param):
            raise ValueError("day_in_month needs param 'YYYY-MM'")
        if self.kind == "value_near_name" and self.param not in ("number", "dose"):
            raise ValueError("value_near_name needs param 'number' or 'dose'")

    def to_dict(self) -> dict:
        return {"label": self.label, "kind": self.kind, "accepted": list(self.accepted),
                "param": self.param}

    @staticmethod
    def from_dict(raw: dict) -> "Fact":
        return Fact(raw["label"], raw["kind"], tuple(raw.get("accepted", ())),
                    raw.get("param", ""))

    def example(self) -> str:
        """A short text that states this fact: used to check the matcher against the fact,
        and to build an 'oracle' answer for a question."""
        if self.kind in ("token", "stem"):
            return self.accepted[0]
        if self.kind == "any_date":
            return "2025-01-15"
        if self.kind == "day_in_month":
            return f"{self.param}-15"
        value = "7.1" if self.param == "number" else "500mg"
        return f"{self.accepted[0]} {value}"


# --- spellings -------------------------------------------------------------------------


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _unique(items: list) -> tuple:
    return tuple(dict.fromkeys(items))


def date_spellings(when: date) -> tuple:
    """Every way of writing a full date the matcher accepts (never the ambiguous 10/01/2026)."""
    out = [when.isoformat()]
    months = {MONTHS[when.month - 1], MONTH_ABBR[when.month - 1]}
    if when.month == 9:
        months.add("Sept")
    for month in sorted(months):
        for day in (str(when.day), _ordinal(when.day)):
            out += [f"{day} {month} {when.year}", f"{month} {day}, {when.year}",
                    f"{month} {day} {when.year}", f"{day} of {month} {when.year}"]
    return _unique(out)


def month_spellings(when: date) -> tuple:
    month, abbr = MONTHS[when.month - 1], MONTH_ABBR[when.month - 1]
    return _unique([f"{when.year}-{when.month:02d}", f"{month} {when.year}",
                    f"{abbr} {when.year}", f"{month}, {when.year}"])


def number_spellings(value: float, decimals: int) -> tuple:
    shown = f"{value:.{decimals}f}"
    out = [shown]
    if float(value).is_integer():
        out += [f"{int(value)}", f"{int(value)}.0"]
    elif shown.endswith("0"):
        out.append(shown.rstrip("0"))
    return _unique(out)


def dose_spellings(dose: str) -> tuple:
    """'500mg' -> '500mg', '500 mg'; '10IU' -> '10IU', '10 IU'."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)", dose)
    if not match:
        return (dose,)
    return _unique([dose, f"{match.group(1)} {match.group(2)}"])


def _word(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)


def duration_spellings(days: int) -> tuple:
    out = [f"{days} days", f"{days} day", f"{days}-day", f"{_word(days)} days",
           f"{_word(days)}-day"]
    if days % 7 == 0:
        weeks = days // 7
        out += [f"{weeks} week", f"{weeks} weeks", f"{weeks}-week", f"{_word(weeks)} week",
                f"{_word(weeks)} weeks", f"{_word(weeks)}-week"]
        if weeks == 1:
            out += ["a week", "a week-long", "a 1-week"]
    return _unique(out)


# --- parsing dates out of an answer ----------------------------------------------------------

_MONTH_LOOKUP = {name.lower(): i for i, name in enumerate(MONTHS, 1)}
_MONTH_LOOKUP.update({name.lower(): i for i, name in enumerate(MONTH_ABBR, 1)})
_MONTH_LOOKUP["sept"] = 9
_MON = "(?:" + "|".join(sorted(_MONTH_LOOKUP, key=len, reverse=True)) + ")"
_ISO = re.compile(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])")
_DMY = re.compile(
    rf"(?<![\w])(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+({_MON})\.?,?\s+(\d{{4}})(?!\d)", re.I)
_MDY = re.compile(
    rf"(?<![\w])({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})(?!\d)", re.I)
_MONTH_YEAR = re.compile(rf"(?<![\w])({_MON})\.?,?\s+(\d{{4}})(?!\d)", re.I)
_ISO_MONTH = re.compile(r"(?<![\d-])\d{4}-\d{2}(?![\d-])")


def _build(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_dates(text: str) -> list:
    """Every full calendar date (day precision) the text states."""
    found = []
    for m in _ISO.finditer(text):
        found.append(_build(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _DMY.finditer(text):
        found.append(_build(int(m.group(3)), _MONTH_LOOKUP[m.group(2).lower()], int(m.group(1))))
    for m in _MDY.finditer(text):
        found.append(_build(int(m.group(3)), _MONTH_LOOKUP[m.group(1).lower()], int(m.group(2))))
    return [d for d in found if d is not None]


def strip_dates(text: str) -> str:
    """Blank out dates (full and month-level) so their digits are not read as values."""
    for pattern in (_ISO, _DMY, _MDY, _MONTH_YEAR, _ISO_MONTH):
        text = pattern.sub(" ", text)
    return text


_strip_dates = strip_dates


# --- matching ------------------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _token_regex(accepted: str) -> "re.Pattern":
    return re.compile(r"(?<![\w-])(?<!\d\.)" + re.escape(accepted) + r"(?![\w-])(?!\.\d)",
                      re.IGNORECASE)


_ALL_NAMES = sorted(
    {n.lower() for lab in LABS.values() for n in (lab.name, *lab.aliases)}
    | {n.lower() for med in MEDS.values() for n in (med.name, *med.aliases)},
    key=len, reverse=True)
_NUMBER = r"(\d+(?:\.\d+)?)"
_DOSE = r"(\d+(?:\.\d+)?)\s?(?:mg|mcg|g|ml|iu)\b"


def _value_near_name(fact: Fact, text: str) -> bool:
    text = _strip_dates(text)
    value = _NUMBER if fact.param == "number" else _DOSE
    own = {a.lower() for a in fact.accepted}
    others = [n for n in _ALL_NAMES if n not in own]
    for name in fact.accepted:
        pattern = re.compile(
            rf"(?<![\w-]){re.escape(name)}(?![\w-])([^.\n;]{{0,40}}?)(?<![\w.-]){value}(?![\w-])",
            re.IGNORECASE)
        for match in pattern.finditer(text):
            filler = match.group(1).lower()
            if not any(re.search(rf"(?<![\w-]){re.escape(o)}(?![\w-])", filler) for o in others):
                return True
    return False


def fact_matches(fact: Fact, answer: str) -> bool:
    text = _normalise(answer)
    if fact.kind == "token":
        return any(_token_regex(a).search(text) for a in fact.accepted)
    if fact.kind == "stem":
        lowered = text.lower()
        return any(a.lower() in lowered for a in fact.accepted)
    if fact.kind == "any_date":
        return bool(find_dates(text))
    if fact.kind == "day_in_month":
        year, month = (int(x) for x in fact.param.split("-"))
        return any(d.year == year and d.month == month for d in find_dates(text))
    return _value_near_name(fact, text)
