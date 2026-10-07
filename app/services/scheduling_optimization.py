"""Deterministic appointment slot ranking under business constraints."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Iterable


class SchedulingOptimizationService:
    def rank_slots(self, slots: Iterable[object], *, now: datetime,
                   timezone_name: str | None, allowed_weekdays: tuple[int, ...] = (),
                   preferred_time: tuple[time, time] | None = None,
                   max_advance_days: int | None = None,
                   preferred_date: date | None = None,
                   preferred_weekday: int | None = None) -> list[object]:
        if (now.tzinfo is None or now.utcoffset() is None
                or max_advance_days is not None and max_advance_days < 1):
            raise ValueError("valid clock and positive scheduling horizon are required")
        if (any(not 0 <= weekday <= 6 for weekday in allowed_weekdays)
                or preferred_time is not None and preferred_time[0] >= preferred_time[1]):
            raise ValueError("scheduling preferences are invalid")
        if preferred_weekday is not None and not 0 <= preferred_weekday <= 6:
            raise ValueError("preferred weekday must be between 0 and 6")
        try:
            zone = ZoneInfo(timezone_name or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            zone = ZoneInfo("UTC")
        now = now.astimezone(UTC)
        latest = now + timedelta(days=max_advance_days) if max_advance_days is not None else None
        candidates = []
        for slot in slots:
            starts_at = getattr(slot, "starts_at", None)
            if not isinstance(starts_at, datetime):
                continue
            if starts_at.tzinfo is None or starts_at.utcoffset() is None:
                starts_at = starts_at.replace(tzinfo=UTC)
            starts_at = starts_at.astimezone(UTC)
            local_start = starts_at.astimezone(zone)
            if starts_at <= now or latest is not None and starts_at > latest:
                continue
            if allowed_weekdays and local_start.weekday() not in allowed_weekdays:
                continue
            in_preferred_time = (preferred_time is None
                                 or preferred_time[0] <= local_start.timetz().replace(tzinfo=None) < preferred_time[1])
            candidates.append((
                0 if preferred_date is None or local_start.date() == preferred_date else 1,
                0 if preferred_weekday is None or local_start.weekday() == preferred_weekday else 1,
                0 if in_preferred_time else 1, starts_at,
                               str(getattr(slot, "staff_id", "")), slot))
        candidates.sort(key=lambda item: item[:5])
        return [item[5] for item in candidates]


__all__ = ["SchedulingOptimizationService"]
