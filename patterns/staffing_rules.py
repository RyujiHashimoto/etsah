"""Static and generated-runtime rules for dispatch limits and 36 agreements.

The compiler evaluates the dates and ceilings written in the spec. It does
not read live timesheets or stored contracts. `runtime_checks` describes the
checks the generated program must run later.

Date math uses `datetime` from the standard library. This module does not
import `prout.py`. The numbers below are the general statutory ceilings used
as defaults and as maxima. They are not a legal opinion. A 社労士 has to
confirm them, including industry exceptions this module does not model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional


# Labor Standards Act art. 36 paras. 3-6, general rule (not 変形労働時間制's
# 42h/320h, and not the driver / doctor / R&D / disaster-construction exceptions).
MONTHLY_OVERTIME_MAX = Decimal("45")
YEARLY_OVERTIME_MAX = Decimal("360")
SPECIAL_YEARLY_MAX = Decimal("720")
MONTH_WITH_HOLIDAY_UNDER = Decimal("100")
AVERAGE_MAX = Decimal("80")
MONTHS_OVER_LIMIT_MAX = 6
DISPATCH_YEARS = 3
COOLING_MONTHS = 3

# Flags that downgrade a 抵触日 overrun from an error to a warning.
# `day_limited` and `leave_substitute` do not prove the statutory conditions.
EXEMPT_ESTABLISHED = ("indefinite", "age60")
EXEMPT_OPTIONAL = ("day_limited", "leave_substitute")
EXEMPT = EXEMPT_ESTABLISHED + EXEMPT_OPTIONAL

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_DECIMAL = re.compile(r"\d+(\.\d+)?\Z")
_CLOCK = re.compile(r"(\d+)h(\d{1,2})m\Z")
_COUNT = re.compile(r"0|[1-9]\d*\Z")
_SPAN = re.compile(r"(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})\Z")


@dataclass(frozen=True)
class StaffingNote:
    severity: str
    code: str
    message: str
    line: int = 0


@dataclass(frozen=True)
class Span:
    start: date
    end: date
    current: bool = False


def parse_date(value: str) -> Optional[date]:
    """ISO calendar date. `2026-4-1` and impossible days are rejected."""
    value = value.strip()
    if not _DATE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_duration(value: str) -> Optional[Decimal]:
    """Hours as a decimal (`7.5`) or a clock (`7h30m`). Same grammar as proutc."""
    value = value.strip()
    clock = _CLOCK.fullmatch(value)
    if clock:
        minutes = int(clock.group(2))
        if minutes >= 60:
            return None
        return Decimal(clock.group(1)) + (Decimal(minutes) / Decimal(60))
    if not _DECIMAL.fullmatch(value):
        return None
    return Decimal(value)


def parse_count(value: str) -> Optional[int]:
    value = value.strip()
    if not _COUNT.fullmatch(value):
        return None
    return int(value)


def shift_date(start: date, *, years: int = 0, months: int = 0) -> date:
    """Anniversary `years` and `months` later.

    A year or month period ends on the day before the anniversary (Civil Code
    art. 143). The value returned here is the day after that period. When the
    anniversary does not exist, the period ends on the last day of the target
    month and this function returns the next day. For every other date the
    anniversary itself is that next day, so 2026-04-01 plus 3 years is
    2029-04-01, and 2024-02-29 plus 3 years is 2027-03-01.
    """
    month_index = start.month - 1 + months
    year = start.year + years + month_index // 12
    month = month_index % 12 + 1
    try:
        return date(year, month, start.day)
    except ValueError:
        if month == 12:
            return date(year + 1, 1, 1)
        return date(year, month + 1, 1)


def conflict_day(start: date, years: int = DISPATCH_YEARS) -> date:
    """抵触日: the day after `years` from `start`."""
    return shift_date(start, years=years)


def cooling_resets(prior_end: date, next_start: date) -> bool:
    """True when the blank between two dispatches is more than three months.

    The blank starts the day after `prior_end`. A blank of exactly three
    months does not reset. The next start resets only when it is later than
    the three-month anniversary of the blank start (three months plus one day).
    """
    if next_start <= prior_end:
        return False
    blank_start = prior_end + timedelta(days=1)
    boundary = shift_date(blank_start, months=COOLING_MONTHS)
    return next_start > boundary


def chain_anchor(spans: list[Span]) -> tuple[date, str]:
    """Start of the continuous chain that contains the current span.

    The reason is `no earlier span`, `cooling reset`, or `continued from <date>`.
    """
    if not spans or not any(span.current for span in spans):
        raise ValueError("chain_anchor requires the current span")
    ordered = sorted(spans, key=lambda span: (span.start, span.end, not span.current))
    anchor = ordered[0].start
    end = ordered[0].end
    found: Optional[tuple[date, str]] = None
    for index, span in enumerate(ordered):
        broke = False
        if index == 0:
            anchor = span.start
            end = span.end
        elif cooling_resets(end, span.start):
            anchor = span.start
            end = span.end
            broke = True
        elif span.end > end:
            end = span.end
        if span.current:
            if anchor < span.start:
                reason = f"continued from {anchor.isoformat()}"
            elif broke:
                reason = "cooling reset"
            else:
                reason = "no earlier span"
            found = (anchor, reason)
    assert found is not None
    return found


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _slot(pattern, name: str) -> Optional[str]:
    raw = pattern.slots.get(name)
    if raw is None:
        return None
    return _unquote(raw)


def _entity_name(pattern) -> str:
    return _slot(pattern, "entity") or pattern.target or ""


def _entity(app, name: str):
    return next((item for item in app.entities if item.name == name), None)


def _patterns(app, name: str) -> list:
    return [item for item in app.patterns if item.name == name]


def parse_history(value: str) -> tuple[Optional[list[tuple[date, date]]], Optional[str]]:
    """Parse `YYYY-MM-DD/YYYY-MM-DD` spans separated by commas."""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return None, value.strip() or "(empty)"
    spans: list[tuple[date, date]] = []
    for part in parts:
        matched = _SPAN.fullmatch(part)
        if not matched:
            return None, part
        start = parse_date(matched.group(1))
        end = parse_date(matched.group(2))
        if start is None or end is None or end < start:
            return None, part
        spans.append((start, end))
    return spans, None


def _history_spans(pattern, slot: str) -> tuple[list[Span], Optional[str]]:
    raw = _slot(pattern, slot)
    if raw is None:
        return [], None
    parsed, bad = parse_history(raw)
    if parsed is None:
        return [], bad
    return [Span(start, end, False) for start, end in parsed], None


def _group_spans(contracts: list, current, key: Optional[str], history_slot: str, notes: list[StaffingNote]) -> Optional[list[Span]]:
    spans: list[Span] = []
    ok = True
    for contract in contracts:
        other = _slot(contract, "office") if history_slot == "office_history" else _person_key(contract)
        if key is None:
            if contract is not current:
                continue
        elif other != key:
            continue
        history, bad = _history_spans(contract, history_slot)
        if bad is not None:
            if contract is current:
                notes.append(
                    StaffingNote(
                        "error",
                        "E-SLOT",
                        f"{contract.name}.{history_slot} must be start/end dates "
                        f"(YYYY-MM-DD/YYYY-MM-DD), got {bad!r}",
                        contract.line,
                    )
                )
                ok = False
        else:
            spans.extend(history)
        start = parse_date(_slot(contract, "start_date") or "")
        end = parse_date(_slot(contract, "end_date") or "")
        if start is None or end is None:
            if contract is current:
                return None
            continue
        if end < start:
            spans.append(Span(start, start, contract is current))
        else:
            spans.append(Span(start, end, contract is current))
    if not ok:
        return None
    return spans


def _person_key(pattern) -> Optional[str]:
    person = _slot(pattern, "person")
    unit = _slot(pattern, "org_unit")
    if person and unit:
        return person + "\n" + unit
    return None


def _exempt(pattern) -> str:
    return _slot(pattern, "exempt") or "none"


def _exempt_suffix(exempt: str) -> str:
    if exempt in EXEMPT_OPTIONAL:
        return (
            f" Exempt slot is {exempt}; the day-count or leave conditions were not checked."
            " This is not a legal opinion."
        )
    if exempt in EXEMPT_ESTABLISHED:
        return f" Exempt slot is {exempt}. This is not a legal opinion."
    return ""


@dataclass
class _Agreement:
    pattern: object
    timesheet: str
    monthly: Optional[Decimal]
    yearly: Optional[Decimal]
    special: bool
    special_yearly: Optional[Decimal]
    month_with_holiday: Optional[Decimal]
    average: Optional[Decimal]
    months_over: Optional[int]
    warn_at: Optional[Decimal]
    on_warning: Optional[str]
    on_limit: Optional[str]
    usable: bool


def _hours_slot(pattern, name: str) -> tuple[Optional[Decimal], bool]:
    """Return (value, ok). Omitted is (None, True). Unparseable is (None, False)."""
    raw = _slot(pattern, name)
    if raw is None:
        return None, True
    parsed = parse_duration(raw)
    if parsed is None:
        return None, False
    return parsed, True


def _check_range(
    notes: list[StaffingNote],
    pattern,
    name: str,
    value: Decimal,
    ceiling: Decimal,
    unit: str,
) -> None:
    if value <= 0:
        notes.append(
            StaffingNote(
                "error",
                "E-POLICY",
                f"overtime_agreement.{name}: {value} must be greater than zero",
                pattern.line,
            )
        )
    elif value > ceiling:
        notes.append(
            StaffingNote(
                "error",
                "E-POLICY",
                (
                    f"overtime_agreement.{name}: {value} exceeds the general statutory maximum "
                    f"of {ceiling} {unit}. Confirm the ceiling with a 社労士; "
                    "industry exceptions are not modeled."
                ),
                pattern.line,
            )
        )


def _analyze_agreement(pattern) -> tuple[list[StaffingNote], _Agreement]:
    notes: list[StaffingNote] = []
    parsed_ok = True
    special_raw = _slot(pattern, "special_clause") or "none"
    special = special_raw == "enabled"
    monthly, ok = _hours_slot(pattern, "monthly_overtime_hours")
    parsed_ok = parsed_ok and ok
    if monthly is None and ok:
        monthly = MONTHLY_OVERTIME_MAX
    yearly, ok = _hours_slot(pattern, "yearly_overtime_hours")
    parsed_ok = parsed_ok and ok
    if yearly is None and ok:
        yearly = YEARLY_OVERTIME_MAX
    if monthly is not None:
        _check_range(notes, pattern, "monthly_overtime_hours", monthly, MONTHLY_OVERTIME_MAX, "hours/month")
    if yearly is not None:
        _check_range(notes, pattern, "yearly_overtime_hours", yearly, YEARLY_OVERTIME_MAX, "hours/year")
    if monthly is not None and yearly is not None and yearly < monthly:
        notes.append(
            StaffingNote(
                "error",
                "E-POLICY",
                f"overtime_agreement: yearly cap {yearly} is below the monthly cap {monthly}",
                pattern.line,
            )
        )

    special_names = (
        "special_yearly_overtime_hours",
        "month_with_holiday_hours",
        "average_hours",
        "months_over_limit",
    )
    special_yearly: Optional[Decimal] = None
    month_with_holiday: Optional[Decimal] = None
    average: Optional[Decimal] = None
    months_over: Optional[int] = None
    if not special and any(name in pattern.slots for name in special_names):
        notes.append(
            StaffingNote(
                "error",
                "E-POLICY",
                "overtime_agreement: special-clause slots require special_clause: enabled",
                pattern.line,
            )
        )
    elif special:
        special_yearly, ok = _hours_slot(pattern, "special_yearly_overtime_hours")
        parsed_ok = parsed_ok and ok
        if special_yearly is None and ok:
            special_yearly = SPECIAL_YEARLY_MAX
        if special_yearly is not None:
            _check_range(
                notes,
                pattern,
                "special_yearly_overtime_hours",
                special_yearly,
                SPECIAL_YEARLY_MAX,
                "hours/year",
            )
            if yearly is not None and special_yearly < yearly:
                notes.append(
                    StaffingNote(
                        "error",
                        "E-POLICY",
                        (
                            f"overtime_agreement: special yearly cap {special_yearly} "
                            f"is below the base yearly cap {yearly}"
                        ),
                        pattern.line,
                    )
                )
        month_with_holiday, ok = _hours_slot(pattern, "month_with_holiday_hours")
        parsed_ok = parsed_ok and ok
        if month_with_holiday is not None and (
            month_with_holiday <= 0 or month_with_holiday >= MONTH_WITH_HOLIDAY_UNDER
        ):
            notes.append(
                StaffingNote(
                    "error",
                    "E-POLICY",
                    (
                        f"overtime_agreement.month_with_holiday_hours: {month_with_holiday} "
                        f"must be greater than zero and under {MONTH_WITH_HOLIDAY_UNDER} "
                        "(時間外+休日)"
                    ),
                    pattern.line,
                )
            )
        average, ok = _hours_slot(pattern, "average_hours")
        parsed_ok = parsed_ok and ok
        if average is None and ok:
            average = AVERAGE_MAX
        if average is not None:
            _check_range(notes, pattern, "average_hours", average, AVERAGE_MAX, "hours")
        if "months_over_limit" in pattern.slots:
            raw_months = _slot(pattern, "months_over_limit") or ""
            months_over = parse_count(raw_months)
            if months_over is None:
                notes.append(
                    StaffingNote(
                        "error",
                        "E-SLOT",
                        f"overtime_agreement.months_over_limit must be an integer, got {raw_months!r}",
                        pattern.line,
                    )
                )
        else:
            months_over = MONTHS_OVER_LIMIT_MAX
        if months_over is not None and months_over > MONTHS_OVER_LIMIT_MAX:
            notes.append(
                StaffingNote(
                    "error",
                    "E-POLICY",
                    (
                        f"overtime_agreement.months_over_limit: {months_over} exceeds the general "
                        f"statutory maximum of {MONTHS_OVER_LIMIT_MAX} months/year"
                    ),
                    pattern.line,
                )
            )

    warn_at, ok = _hours_slot(pattern, "warn_at_hours")
    parsed_ok = parsed_ok and ok
    if warn_at is not None and monthly is not None and not (Decimal("0") < warn_at < monthly):
        notes.append(
            StaffingNote(
                "error",
                "E-POLICY",
                (
                    f"overtime_agreement.warn_at_hours: {warn_at} must be greater than zero and "
                    f"below the hard monthly cap {monthly} (warning threshold, not the hard stop)"
                ),
                pattern.line,
            )
        )
    on_warning = _slot(pattern, "on_warning")
    on_limit = _slot(pattern, "on_limit")
    actions_ok = on_warning == "notify" and on_limit == "reject"
    usable = (
        parsed_ok
        and actions_ok
        and warn_at is not None
        and monthly is not None
        and yearly is not None
        and not any(note.severity == "error" for note in notes)
    )
    agreement = _Agreement(
        pattern,
        _slot(pattern, "timesheet") or "",
        monthly,
        yearly,
        special,
        special_yearly,
        month_with_holiday,
        average,
        months_over,
        warn_at,
        on_warning,
        on_limit,
        usable,
    )
    return notes, agreement


def _timesheet_has_overtime(pattern, entity) -> bool:
    if entity is not None and "overtime_hours" in entity.fields:
        return True
    raw = _slot(pattern, "overtime_cap_hours")
    if raw is None:
        return False
    parsed = parse_duration(raw)
    return parsed is not None and parsed > 0


def _period_monthly(pattern) -> bool:
    period = _slot(pattern, "period") or "monthly"
    return period == "monthly"


def _analyze_timesheets(app, agreements: list[_Agreement]) -> list[StaffingNote]:
    notes: list[StaffingNote] = []
    by_sheet: dict[str, list[_Agreement]] = {}
    for agreement in agreements:
        by_sheet.setdefault(agreement.timesheet, []).append(agreement)
    for pattern in _patterns(app, "timesheet"):
        name = _entity_name(pattern)
        entity = _entity(app, name)
        if entity is not None:
            for field in ("overtime_hours", "holiday_hours"):
                actual = entity.fields.get(field)
                if actual is not None and actual != "Duration":
                    notes.append(
                        StaffingNote(
                            "error",
                            "E-TYPE",
                            f"{entity.name}.{field} must be Duration for timesheet, got {actual}",
                            pattern.line,
                        )
                    )
        if not _timesheet_has_overtime(pattern, entity):
            continue
        covering = by_sheet.get(name, [])
        if not covering:
            reason = "overtime_hours" if entity is not None and "overtime_hours" in entity.fields else "overtime_cap_hours"
            notes.append(
                StaffingNote(
                    "error",
                    "E-POLICY",
                    (
                        f"timesheet: a 36協定 (overtime_agreement) is missing for {name}, "
                        f"which declares overtime ({reason})"
                    ),
                    pattern.line,
                )
            )
        if not _period_monthly(pattern):
            continue
        raw_cap = _slot(pattern, "overtime_cap_hours")
        cap = parse_duration(raw_cap) if raw_cap is not None else None
        if cap is None:
            continue
        specials = [item for item in covering if item.special and item.usable]
        if specials:
            ceilings = []
            for item in specials:
                if item.month_with_holiday is not None:
                    ceilings.append(item.month_with_holiday)
            if ceilings:
                limit = min(ceilings)
                if cap > limit:
                    notes.append(
                        StaffingNote(
                            "error",
                            "E-POLICY",
                            (
                                f"timesheet.overtime_cap_hours: {cap} exceeds the special-clause "
                                f"時間外+休日 cap {limit}"
                            ),
                            pattern.line,
                        )
                    )
            elif cap >= MONTH_WITH_HOLIDAY_UNDER:
                notes.append(
                    StaffingNote(
                        "error",
                        "E-POLICY",
                        (
                            f"timesheet.overtime_cap_hours: {cap} must be under "
                            f"{MONTH_WITH_HOLIDAY_UNDER} (時間外+休日)"
                        ),
                        pattern.line,
                    )
                )
        else:
            if cap > MONTHLY_OVERTIME_MAX:
                notes.append(
                    StaffingNote(
                        "error",
                        "E-POLICY",
                        (
                            f"timesheet.overtime_cap_hours: {cap} exceeds the general statutory "
                            f"maximum of {MONTHLY_OVERTIME_MAX} hours/month without an enabled special clause"
                        ),
                        pattern.line,
                    )
                )
            monthly_caps = [item.monthly for item in covering if item.usable and item.monthly is not None]
            if monthly_caps and cap > min(monthly_caps):
                notes.append(
                    StaffingNote(
                        "error",
                        "E-POLICY",
                        (
                            f"timesheet.overtime_cap_hours: {cap} exceeds the agreement monthly cap "
                            f"{min(monthly_caps)}"
                        ),
                        pattern.line,
                    )
                )
        if any(item.special for item in covering):
            if entity is None or "holiday_hours" not in entity.fields:
                notes.append(
                    StaffingNote(
                        "error",
                        "E-ENTITY",
                        (
                            f"{name} needs a `holiday_hours: Duration` field so the special-clause "
                            "時間外+休日 checks do not treat holiday work as zero"
                        ),
                        pattern.line,
                    )
                )
    return notes


def _limit_violation(pattern, end: date, calculated: date, manual: date, kind: str, anchor: date, reason: str, exempt: str) -> Optional[StaffingNote]:
    if calculated == manual or not calculated < manual or not end > calculated:
        return None
    base = (
        f"dispatch_contract: end_date {end.isoformat()} is after the calculated {kind} 抵触日 "
        f"{calculated.isoformat()} (anchor {anchor.isoformat()}, {reason}). "
        f"notified 抵触日 is {manual.isoformat()}."
    )
    if exempt in EXEMPT:
        return StaffingNote("warning", "W-POLICY", base + _exempt_suffix(exempt), pattern.line)
    return StaffingNote("error", "E-POLICY", base, pattern.line)


def _analyze_dispatch(app) -> list[StaffingNote]:
    notes: list[StaffingNote] = []
    contracts = _patterns(app, "dispatch_contract")
    for pattern in contracts:
        person = _slot(pattern, "person")
        unit = _slot(pattern, "org_unit")
        if (person is None) != (unit is None):
            notes.append(
                StaffingNote(
                    "error",
                    "E-SLOT",
                    "dispatch_contract: person and org_unit must be set together for 個人単位",
                    pattern.line,
                )
            )
        start = parse_date(_slot(pattern, "start_date") or "")
        end = parse_date(_slot(pattern, "end_date") or "")
        manual = parse_date(_slot(pattern, "conflict_date") or "")
        if start is None or end is None or manual is None:
            continue
        office_key = _slot(pattern, "office")
        office_spans = _group_spans(contracts, pattern, office_key, "office_history", notes)
        person_key = _person_key(pattern)
        person_spans = _group_spans(contracts, pattern, person_key, "person_history", notes)
        if office_spans is None or person_spans is None:
            continue
        office_anchor, office_reason = chain_anchor(office_spans)
        person_anchor, person_reason = chain_anchor(person_spans)
        office_conflict = conflict_day(office_anchor)
        person_conflict = conflict_day(person_anchor)
        exempt = _exempt(pattern)
        if office_conflict != manual:
            notes.append(
                StaffingNote(
                    "warning",
                    "W-POLICY",
                    (
                        f"dispatch_contract: notified 抵触日 {manual.isoformat()} disagrees with the "
                        f"calculated office 抵触日 {office_conflict.isoformat()} "
                        f"(anchor {office_anchor.isoformat()}, {office_reason}). "
                        "The notified date is kept. This is not a legal opinion."
                    ),
                    pattern.line,
                )
            )
        violation = _limit_violation(
            pattern, end, office_conflict, manual, "office", office_anchor, office_reason, exempt
        )
        if violation is not None:
            notes.append(violation)
        if person_conflict != office_conflict:
            violation = _limit_violation(
                pattern,
                end,
                person_conflict,
                manual,
                "個人単位",
                person_anchor,
                person_reason,
                exempt,
            )
            if violation is not None:
                notes.append(violation)
        notified_person = _slot(pattern, "person_conflict_date")
        if notified_person is not None:
            parsed = parse_date(notified_person)
            if parsed is None:
                continue
            if parsed != person_conflict:
                notes.append(
                    StaffingNote(
                        "warning",
                        "W-POLICY",
                        (
                            f"dispatch_contract: notified 個人単位 抵触日 {parsed.isoformat()} disagrees with "
                            f"the calculated 個人単位 抵触日 {person_conflict.isoformat()} "
                            f"(anchor {person_anchor.isoformat()}, {person_reason}). "
                            "The notified date is kept. This is not a legal opinion."
                        ),
                        pattern.line,
                    )
                )
    return notes


def analyze(app) -> list[StaffingNote]:
    """Spec diagnostics. Empty when the app does not use the staffing rules."""
    notes: list[StaffingNote] = []
    agreements: list[_Agreement] = []
    for pattern in _patterns(app, "overtime_agreement"):
        found, agreement = _analyze_agreement(pattern)
        notes.extend(found)
        agreements.append(agreement)
    notes.extend(_analyze_timesheets(app, agreements))
    notes.extend(_analyze_dispatch(app))
    return notes


def _fmt(value: Decimal) -> str:
    return format(value, "f")


def _agreement_checks(app, agreement: _Agreement) -> list[dict]:
    pattern = agreement.pattern
    checks: list[dict] = []
    if not agreement.usable or agreement.monthly is None or agreement.yearly is None or agreement.warn_at is None:
        checks.append(
            {
                "phase": "static",
                "pattern": "overtime_agreement",
                "id": "ceilings",
                "rule": "configured 36協定 limits failed the static ceiling check; runtime checks were not generated",
                "on_breach": "error",
                "line": pattern.line,
            }
        )
        return checks
    checks.append(
        {
            "phase": "static",
            "pattern": "overtime_agreement",
            "id": "ceilings",
            "rule": (
                f"spec constants for {agreement.timesheet}: monthly {_fmt(agreement.monthly)} "
                f"<= {_fmt(MONTHLY_OVERTIME_MAX)}, yearly {_fmt(agreement.yearly)} "
                f"<= {_fmt(YEARLY_OVERTIME_MAX)}, warn_at {_fmt(agreement.warn_at)} "
                f"is below the hard monthly cap, on_warning={agreement.on_warning}, "
                f"on_limit={agreement.on_limit}. Live hours were not read."
            ),
            "on_breach": "evaluated",
            "line": pattern.line,
        }
    )
    warning = agreement.on_warning or "notify"
    limit = agreement.on_limit or "reject"
    checks.append(
        {
            "phase": "runtime",
            "pattern": "overtime_agreement",
            "id": "monthly_warning",
            "rule": (
                f"for each month of {agreement.timesheet}, if overtime_hours >= {_fmt(agreement.warn_at)} "
                f"and overtime_hours <= {_fmt(agreement.monthly)}: {warning} "
                "(notify the configured channel; do not stop submission)"
            ),
            "on_breach": warning,
            "line": pattern.line,
        }
    )
    checks.append(
        {
            "phase": "runtime",
            "pattern": "overtime_agreement",
            "id": "monthly_hard_stop",
            "rule": (
                f"for each month of {agreement.timesheet}, if overtime_hours > {_fmt(agreement.monthly)}: "
                f"{limit} (hard stop; do not accept the timesheet)"
            ),
            "on_breach": limit,
            "line": pattern.line,
        }
    )
    checks.append(
        {
            "phase": "runtime",
            "pattern": "overtime_agreement",
            "id": "yearly_hard_stop",
            "rule": (
                f"if the year's overtime_hours sum > {_fmt(agreement.yearly)}: {limit}"
            ),
            "on_breach": limit,
            "line": pattern.line,
        }
    )
    if agreement.special:
        holiday = (
            f"> {_fmt(agreement.month_with_holiday)}"
            if agreement.month_with_holiday is not None
            else f">= {_fmt(MONTH_WITH_HOLIDAY_UNDER)} (under {_fmt(MONTH_WITH_HOLIDAY_UNDER)}; 100 exactly fails)"
        )
        average = agreement.average if agreement.average is not None else AVERAGE_MAX
        months_over = agreement.months_over if agreement.months_over is not None else MONTHS_OVER_LIMIT_MAX
        yearly = agreement.special_yearly if agreement.special_yearly is not None else SPECIAL_YEARLY_MAX
        checks.append(
            {
                "phase": "static",
                "pattern": "overtime_agreement",
                "id": "special_clause_ceilings",
                "rule": (
                    f"special clause enabled: yearly {_fmt(yearly)} <= {_fmt(SPECIAL_YEARLY_MAX)}, "
                    f"時間外+休日 monthly cap is {holiday}, "
                    f"2-6 month average <= {_fmt(average)}, "
                    f"months over the { _fmt(agreement.monthly) } hour cap <= {months_over}"
                ),
                "on_breach": "evaluated",
                "line": pattern.line,
            }
        )
        checks.append(
            {
                "phase": "runtime",
                "pattern": "overtime_agreement",
                "id": "special_yearly",
                "rule": f"if the year's overtime_hours sum > {_fmt(yearly)}: {limit}",
                "on_breach": limit,
                "line": pattern.line,
            }
        )
        checks.append(
            {
                "phase": "runtime",
                "pattern": "overtime_agreement",
                "id": "month_with_holiday",
                "rule": (
                    f"if overtime_hours + holiday_hours {holiday}: {limit}. "
                    "A missing holiday_hours value rejects the row instead of counting holiday work as zero."
                ),
                "on_breach": limit,
                "line": pattern.line,
            }
        )
        checks.append(
            {
                "phase": "runtime",
                "pattern": "overtime_agreement",
                "id": "multi_month_average",
                "rule": (
                    f"if any 2, 3, 4, 5, or 6 month average of overtime_hours + holiday_hours "
                    f"> {_fmt(average)}: {limit}"
                ),
                "on_breach": limit,
                "line": pattern.line,
            }
        )
        checks.append(
            {
                "phase": "runtime",
                "pattern": "overtime_agreement",
                "id": "months_over_limit",
                "rule": (
                    f"if the number of months with overtime_hours > {_fmt(agreement.monthly)} "
                    f"is > {months_over}: {limit}. A month equal to the cap is not counted."
                ),
                "on_breach": limit,
                "line": pattern.line,
            }
        )
    return checks


def _dispatch_checks(app) -> list[dict]:
    checks: list[dict] = []
    contracts = _patterns(app, "dispatch_contract")
    if not contracts:
        return checks
    for pattern in contracts:
        start = parse_date(_slot(pattern, "start_date") or "")
        manual = parse_date(_slot(pattern, "conflict_date") or "")
        if start is None or manual is None:
            continue
        office_key = _slot(pattern, "office") or "(this contract only)"
        office_spans = _group_spans(contracts, pattern, _slot(pattern, "office"), "office_history", [])
        person_spans = _group_spans(contracts, pattern, _person_key(pattern), "person_history", [])
        if not office_spans or not person_spans:
            continue
        office_anchor, office_reason = chain_anchor(office_spans)
        person_anchor, person_reason = chain_anchor(person_spans)
        office_conflict = conflict_day(office_anchor)
        person_conflict = conflict_day(person_anchor)
        checks.append(
            {
                "phase": "static",
                "pattern": "dispatch_contract",
                "id": "conflict_date",
                "rule": (
                    f"office {office_key}: anchor {office_anchor.isoformat()} ({office_reason}), "
                    f"抵触日 {office_conflict.isoformat()}; "
                    f"個人単位 anchor {person_anchor.isoformat()} ({person_reason}), "
                    f"抵触日 {person_conflict.isoformat()}; "
                    f"notified {manual.isoformat()}. "
                    "Compared with the spec spans only."
                ),
                "on_breach": "evaluated",
                "line": pattern.line,
            }
        )
    checks.append(
        {
            "phase": "runtime",
            "pattern": "dispatch_contract",
            "id": "conflict_date_recompute",
            "rule": (
                "On insert or update, recompute from stored contracts. "
                "事業所単位 groups the same office id; 個人単位 groups the same person and org_unit. "
                "A blank of more than 3 months (the day after a 3-month anniversary of the day after the prior end) "
                "resets the anchor; a blank of exactly 3 months does not. "
                "抵触日 is anchor + 3 years, the day after that period. "
                "A Feb 29 anchor whose anniversary does not exist uses March 1. "
                "If end_date is after that 抵触日, reject unless exempt is indefinite, age60, "
                "day_limited, or leave_substitute (those warn; they are not a legal opinion, and "
                "day_limited / leave_substitute do not check the day count or the leave). "
                "If the stored notified 抵触日 disagrees, warn and keep the notified date. "
                "This recomputation is not run by the compiler."
            ),
            "on_breach": "reject",
            "line": contracts[0].line,
        }
    )
    return checks


def runtime_checks(app) -> list[dict]:
    """Static results and the runtime checks the generated code must perform."""
    checks: list[dict] = []
    for pattern in _patterns(app, "overtime_agreement"):
        _notes, agreement = _analyze_agreement(pattern)
        checks.extend(_agreement_checks(app, agreement))
    checks.extend(_dispatch_checks(app))
    return checks


def format_checks(checks: list[dict]) -> str:
    if not checks:
        return ""
    lines = [
        "staffing checks: static lines were evaluated from the spec; runtime lines are generated and were not run on live rows."
    ]
    for item in checks:
        lines.append(
            f"  [{item['phase']}] {item['pattern']}.{item['id']}: {item['rule']} -> {item['on_breach']}"
        )
    return "\n".join(lines)


def runtime_artifacts(app) -> list[tuple[str, str, str, int, str]]:
    """Extra artifacts: (pattern, path, kind, lines, desc)."""
    checks = runtime_checks(app)
    arts: list[tuple[str, str, str, int, str]] = []
    overtime = [item for item in checks if item["pattern"] == "overtime_agreement"]
    dispatch = [item for item in checks if item["pattern"] == "dispatch_contract"]
    if overtime:
        desc = " | ".join(f"{item['phase']}: {item['rule']} -> {item['on_breach']}" for item in overtime)
        arts.append(("overtime_agreement", "lib/labor/overtime_checks.ts", "TS", 80, desc))
    if dispatch:
        desc = " | ".join(f"{item['phase']}: {item['rule']} -> {item['on_breach']}" for item in dispatch)
        arts.append(("dispatch_contract", "lib/labor/conflict_date.ts", "TS", 70, desc))
    return arts
