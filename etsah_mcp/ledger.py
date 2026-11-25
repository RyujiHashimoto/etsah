"""Persist call counts and decimal spend for the guard.

Caps come from ``limit calls`` and ``limit spend`` on the op, the task, and
the agent. The file is the remaining balance's complement: it stores how much
has been used in the current Asia/Tokyo window. Writes replace the file
atomically while an exclusive lock is held, so two overlapping calls cannot
both pass a cap of one.

A spend op whose amount cannot be read is refused before the file is touched.
Exceeding a cap does not change the file.

Author: Ryuji Hashimoto (J-PROUT Inc.)
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from etsah import CallQuota, SpendQuota
from etsah_mcp.decide import _resolve_agent


TOKYO = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class Reservation:
    blocked: bool
    reason: str = ""
    remaining_calls: Optional[int] = None
    remaining_spend: Optional[str] = None
    currency: str = ""
    prior_calls: Optional[int] = None
    prior_spend: Optional[str] = None
    _rollback: Optional[dict[str, str]] = None
    _path: str = ""

    def rollback(self) -> None:
        """Put back a reservation that was not forwarded."""
        if not self._rollback or not self._path:
            return
        _update(self._path, lambda used: _subtract(used, self._rollback or {}))


def reserve(
    loaded: Any,
    tool: str,
    arguments: Any,
    *,
    agent: str = "",
    path: str = "",
    now: Optional[datetime] = None,
) -> Reservation:
    """Count one call. ``blocked`` is true when the call must not be forwarded."""
    program = loaded.program
    if program is None or not tool:
        return Reservation(False)
    ops = {op.name: op for op in program.ops}
    op = ops.get(tool)
    if op is None:
        return Reservation(False)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    spends = any(effect[0] == "spend" for effect in op.effects)
    amount: Optional[Decimal] = None
    currency = ""
    if spends:
        if op.spend_meter is None:
            return Reservation(True, "spend amount cannot be read")
        amount = _read_amount(arguments, op.spend_meter.root, op.spend_meter.field)
        if amount is None:
            return Reservation(True, "spend amount cannot be read")
        if amount <= 0:
            return Reservation(True, "spend amount is not positive")
        currency = op.spend_meter.currency
    chosen, _error = _resolve_agent(program, tool, agent)
    if _error:
        return Reservation(False)
    caps = _caps(program, op, chosen, amount, currency, moment)
    if not caps:
        return Reservation(False, currency=currency)
    if not path:
        return Reservation(True, "ledger file is not configured", currency=currency)
    delta = {cap.key: cap.delta for cap in caps}

    def apply(used: dict[str, str]) -> Optional[str]:
        for cap in caps:
            current = Decimal(used.get(cap.key, "0"))
            if current + Decimal(cap.delta) > cap.limit:
                return cap.reason
        for cap in caps:
            current = Decimal(used.get(cap.key, "0"))
            used[cap.key] = format(current + Decimal(cap.delta), "f")
        return None

    try:
        reason, used = _update(path, apply)
    except OSError as ex:
        message = "ledger is unreadable" if "unreadable" in str(ex) else "ledger is not writable"
        return Reservation(True, message, currency=currency)
    if reason:
        return Reservation(
            True,
            reason,
            remaining_calls=_remaining_calls(caps, used),
            remaining_spend=_remaining_spend(caps, used),
            currency=currency,
        )
    prior = dict(used)
    _subtract(prior, delta)
    return Reservation(
        False,
        remaining_calls=_remaining_calls(caps, used),
        remaining_spend=_remaining_spend(caps, used),
        currency=currency,
        prior_calls=_remaining_calls(caps, prior),
        prior_spend=_remaining_spend(caps, prior),
        _rollback=delta,
        _path=path,
    )


@dataclass(frozen=True)
class _Cap:
    key: str
    kind: str
    limit: Decimal
    delta: str
    reason: str


def _caps(program: Any, op: Any, agent: Any, amount: Optional[Decimal], currency: str, now: datetime) -> list[_Cap]:
    found: list[_Cap] = []
    owners: list[tuple[str, str, Any]] = [("op", op.name, op)]
    if agent is not None:
        owners.append(("agent", agent.name, agent))
    for task in program.tasks:
        if agent is not None and task.agent != agent.name:
            continue
        if op.name in _called_ops(task):
            owners.append(("task", task.name, task))
    for scope, name, owner in owners:
        if owner.call_quota is not None:
            found.append(_call_cap(scope, name, owner.call_quota, now))
        if amount is not None and owner.spend_quota is not None and owner.spend_quota.currency == currency:
            found.append(_spend_cap(scope, name, owner.spend_quota, amount, now))
    return found


def _call_cap(scope: str, name: str, quota: CallQuota, now: datetime) -> _Cap:
    window = _window(quota.period, now)
    return _Cap(
        key=f"{scope}:{name}:calls:{quota.period}:{window}",
        kind="calls",
        limit=Decimal(quota.count),
        delta="1",
        reason=f"call limit exceeded ({quota.count} per {quota.period})",
    )


def _spend_cap(scope: str, name: str, quota: SpendQuota, amount: Decimal, now: datetime) -> _Cap:
    window = _window(quota.period, now)
    return _Cap(
        key=f"{scope}:{name}:spend:{quota.currency}:{quota.period}:{window}",
        kind="spend",
        limit=quota.amount,
        delta=format(amount, "f"),
        reason=f"spend limit exceeded ({format(quota.amount, 'f')} {quota.currency} per {quota.period})",
    )


def _window(period: str, now: datetime) -> str:
    local = now.astimezone(TOKYO)
    if period == "hour":
        return local.strftime("%Y-%m-%dT%H")
    if period == "month":
        return local.strftime("%Y-%m")
    return local.strftime("%Y-%m-%d")


def _called_ops(task: Any) -> set[str]:
    found: set[str] = set()

    def walk(expr: Any) -> None:
        if expr is None:
            return
        if getattr(expr, "kind", "") == "call" and expr.name:
            found.add(expr.name)
        walk(getattr(expr, "left", None))
        walk(getattr(expr, "right", None))
        walk(getattr(expr, "base", None))
        for arg in getattr(expr, "args", []) or []:
            walk(arg)

    for stmt in task.body:
        walk(stmt.expr)
    return found


def _read_amount(arguments: Any, root: str, field: str) -> Optional[Decimal]:
    if not isinstance(arguments, dict):
        return None
    current = arguments.get(root)
    if field:
        if not isinstance(current, dict):
            return None
        current = current.get(field)
    if isinstance(current, bool) or current is None:
        return None
    if isinstance(current, float):
        text = format(current, "f")
    else:
        text = str(current).strip()
    if not text or text.startswith("-"):
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if amount != amount.to_integral() and "." not in text and "e" in text.lower():
        return None
    return amount


def _remaining_calls(caps: list[_Cap], used: dict[str, str]) -> Optional[int]:
    rooms = [
        int(cap.limit - Decimal(used.get(cap.key, "0")))
        for cap in caps
        if cap.kind == "calls"
    ]
    if not rooms:
        return None
    return min(rooms)


def _remaining_spend(caps: list[_Cap], used: dict[str, str]) -> Optional[str]:
    rooms = [
        cap.limit - Decimal(used.get(cap.key, "0"))
        for cap in caps
        if cap.kind == "spend"
    ]
    if not rooms:
        return None
    return format(min(rooms), "f")


def _subtract(used: dict[str, str], delta: dict[str, str]) -> Optional[str]:
    for key, value in delta.items():
        current = Decimal(used.get(key, "0")) - Decimal(value)
        if current < 0:
            current = Decimal(0)
        if current == 0:
            used.pop(key, None)
        else:
            used[key] = format(current, "f")
    return None


def _update(path: str, change: Any) -> tuple[Optional[str], dict[str, str]]:
    store = Path(path)
    parent = store.parent
    if parent != Path("") and not parent.is_dir():
        raise OSError("ledger directory is missing")
    lock_path = Path(str(store) + ".lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        used = _read(store)
        before = dict(used)
        reason = change(used)
        if reason:
            return reason, before
        if used != before:
            _write(store, used)
        return None, used
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _read(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as ex:
        raise OSError("ledger is unreadable") from ex
    if not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as ex:
        raise OSError("ledger is unreadable") from ex
    if not isinstance(payload, dict) or set(payload) - {"used"}:
        raise OSError("ledger is unreadable")
    used = payload.get("used", {})
    if not isinstance(used, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in used.items()):
        raise OSError("ledger is unreadable")
    return dict(used)


def _write(path: Path, used: dict[str, str]) -> None:
    blob = json.dumps({"used": used}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = blob
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
