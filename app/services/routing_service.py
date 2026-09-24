"""Business-hours and trusted timezone routing rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Iterable


@dataclass(frozen=True)
class RoutingDecision:
    is_open: bool
    after_hours_action: str
    business_timezone: str | None
    contact_timezone: str | None
    checked_at_local: datetime | None
    safely_configured: bool


class RoutingService:
    """Resolve business hours from provisioned business and routing records."""

    @staticmethod
    def _zone(name: str | None) -> ZoneInfo | None:
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return None

    def evaluate(
        self,
        *,
        business: object,
        contact: object,
        tenant: object,
        rules: Iterable[object],
        now: datetime,
    ) -> RoutingDecision:
        business_zone = self._zone(getattr(business, "timezone", None))
        if business_zone is None:
            business_zone = self._zone(getattr(tenant, "default_timezone", None))
        if business_zone is None:
            return RoutingDecision(False, "callback", None, None, None, False)

        contact_zone = self._zone(getattr(contact, "timezone", None)) or business_zone
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        local_now = now.astimezone(business_zone)
        by_weekday = {rule.weekday: rule for rule in rules if getattr(rule, "active", True)}
        todays_rule = by_weekday.get(local_now.weekday())
        previous_rule = by_weekday.get((local_now.weekday() - 1) % 7)
        local_time = local_now.timetz().replace(tzinfo=None)
        is_open = False
        active_rule = todays_rule

        if todays_rule is not None:
            open_time, close_time = todays_rule.opens_at, todays_rule.closes_at
            if open_time < close_time:
                is_open = open_time <= local_time < close_time
            else:  # An overnight shift closes on the following weekday.
                is_open = local_time >= open_time
        if (
            not is_open
            and previous_rule is not None
            and previous_rule.opens_at > previous_rule.closes_at
            and local_time < previous_rule.closes_at
        ):
            is_open = True
            active_rule = previous_rule

        action = getattr(active_rule, "after_hours_action", "callback")
        if action not in {"voicemail", "callback"}:
            action = "callback"
        return RoutingDecision(
            is_open=is_open,
            after_hours_action=action,
            business_timezone=business_zone.key,
            contact_timezone=contact_zone.key,
            checked_at_local=local_now,
            safely_configured=True,
        )

    @staticmethod
    def format_local_time(value: datetime, timezone_name: str | None) -> str:
        zone = RoutingService._zone(timezone_name)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        local_value = value.astimezone(zone) if zone else value.astimezone(timezone.utc)
        return local_value.strftime("%A, %B %d at %I:%M %p %Z").replace(" 0", " ")


__all__ = ["RoutingDecision", "RoutingService"]
