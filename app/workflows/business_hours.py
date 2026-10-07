"""Tenant and business scoped hours checks with timezone aware results."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.auth.principal_resolver import AuthorizationError
from app.services.routing_service import RoutingDecision, RoutingService
from app.security import AuthenticatedPrincipal


class BusinessHoursService:
    def __init__(self, routing: RoutingService | None = None):
        self._routing = routing or RoutingService()

    @staticmethod
    def _now(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value.astimezone(UTC)

    def evaluate(self, principal: AuthenticatedPrincipal, *, uow,
                 business_id: str, now: datetime) -> RoutingDecision:
        now = self._now(now)
        if not isinstance(principal, AuthenticatedPrincipal) or not (
            {"workflow_executor", "conversation_reader", "conversation_writer"} & set(principal.roles)
        ):
            raise AuthorizationError("Business hours access denied")
        if principal.issued_at > now or (principal.expires_at is not None and principal.expires_at <= now):
            raise AuthorizationError("Business hours access denied")
        tenant = uow.tenants.get(tenant_id=principal.tenant_id)
        business = uow.businesses.get(tenant_id=principal.tenant_id, business_id=business_id)
        if tenant is None or business is None:
            raise AuthorizationError("Business hours access denied")
        rules = uow.routing_rules.list_for_business(
            tenant_id=principal.tenant_id, business_id=business.id,
        )
        region = self._region_config(uow, principal.tenant_id, business, tenant)
        return self._routing.evaluate(business=business, contact=None, tenant=tenant,
                                      rules=rules, now=now,
                                      region_timezone=getattr(region, "timezone", None))

    def within_business_hours(self, principal: AuthenticatedPrincipal, *, uow,
                              business_id: str, at: datetime) -> bool:
        result = self.evaluate(principal, uow=uow, business_id=business_id, now=at)
        return result.safely_configured and result.is_open

    def next_business_time(self, principal: AuthenticatedPrincipal, *, uow,
                           business_id: str, after: datetime) -> datetime | None:
        after = self._now(after)
        decision = self.evaluate(principal, uow=uow, business_id=business_id, now=after)
        if not decision.safely_configured or not decision.business_timezone:
            return None
        if decision.is_open:
            return after
        try:
            zone = ZoneInfo(decision.business_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return None
        rules = uow.routing_rules.list_for_business(
            tenant_id=principal.tenant_id, business_id=business_id,
        )
        by_day = {rule.weekday: rule for rule in rules if rule.active}
        tenant = uow.tenants.get(tenant_id=principal.tenant_id)
        business = uow.businesses.get(tenant_id=principal.tenant_id, business_id=business_id)
        region = self._region_config(uow, principal.tenant_id, business, tenant)
        local_after = after.astimezone(zone)
        for offset in range(15):
            day: date = local_after.date() + timedelta(days=offset)
            rule = by_day.get(day.weekday())
            if rule is None:
                continue
            local_candidate = datetime.combine(day, rule.opens_at).replace(tzinfo=zone)
            candidate = local_candidate.astimezone(UTC)
            # Skip nonexistent local times during DST jumps.
            if candidate.astimezone(zone).replace(tzinfo=None) != local_candidate.replace(tzinfo=None):
                continue
            if candidate < after:
                continue
            result = self._routing.evaluate(business=business, contact=None,
                tenant=tenant, rules=rules, now=candidate,
                region_timezone=getattr(region, "timezone", None))
            if result.safely_configured and result.is_open:
                return candidate
        return None

    @staticmethod
    def _region_config(uow, tenant_id, business, tenant):
        regions = getattr(uow, "regions", None)
        if regions is None:
            return None
        region_id = getattr(business, "region_id", None) or getattr(tenant, "default_region_id", None)
        return regions.get(tenant_id=tenant_id, region_id=region_id) if region_id else None


def within_business_hours(principal: AuthenticatedPrincipal, *, uow,
                          business_id: str, at: datetime) -> bool:
    return BusinessHoursService().within_business_hours(principal, uow=uow,
                                                        business_id=business_id, at=at)


def next_business_time(principal: AuthenticatedPrincipal, *, uow,
                       business_id: str, after: datetime) -> datetime | None:
    return BusinessHoursService().next_business_time(principal, uow=uow,
                                                      business_id=business_id, after=after)
