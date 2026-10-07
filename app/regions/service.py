"""Tenant-scoped region configuration, residency gates, and audit decisions."""
from __future__ import annotations

from hashlib import sha256
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.regions.catalog import get_region, list_regions, validate_country
from app.security import AuthenticatedPrincipal


class RegionAccessDenied(PermissionError):
    pass


class RegionService:
    def __init__(self, *, uow_factory):
        self._uow_factory = uow_factory

    def list_regions(self, principal: AuthenticatedPrincipal) -> list[dict]:
        with self._uow_factory() as uow:
            configs = {row.region_id: row for row in uow.regions.list(tenant_id=principal.tenant_id,
                                                                       active_only=False)}
            return [{"region_id": row.region_id, "region_group": row.region_group,
                     "country_code": row.country_code, "timezone": row.timezone,
                     "language": row.language,
                     "data_residency_region": row.data_residency_region,
                     "configured": row.region_id in configs,
                     "active": bool(configs.get(row.region_id) and configs[row.region_id].active),
                     "approval_status": configs[row.region_id].approval_status if row.region_id in configs else "not_enabled"}
                    for row in list_regions()]

    def tenant_regions(self, principal: AuthenticatedPrincipal) -> dict:
        with self._uow_factory() as uow:
            tenant = uow.tenants.get(tenant_id=principal.tenant_id)
            if tenant is None:
                raise LookupError("tenant not found")
            configs = uow.regions.list(tenant_id=principal.tenant_id, active_only=False)
            return {"tenant_id": principal.tenant_id,
                    "default_region_id": tenant.default_region_id,
                    "supported_region_ids": list(tenant.supported_region_ids or []),
                    "regions": [self._config_view(row) for row in configs]}

    def enable_region(self, principal: AuthenticatedPrincipal, request) -> dict:
        self._require_role(principal, "region_admin")
        definition = get_region(request.region_id)
        country_code = validate_country(request.region_id, request.country_code)
        timezone = request.timezone or definition.timezone
        language = request.language or definition.language
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone must be a valid IANA timezone") from None
        allowed_channels = sorted(set(request.allowed_channels))
        if not set(allowed_channels).issubset(set(definition.allowed_channels)):
            raise ValueError("channel is not supported for the selected deployment region")
        storage_regions = sorted(set(request.allowed_storage_regions or [request.region_id]))
        for region_id in storage_regions:
            get_region(region_id)
        if request.region_id not in storage_regions:
            raise ValueError("residency region must be included in allowed storage regions")
        cross_region = any(item != request.region_id for item in storage_regions)
        if cross_region:
            self._require_role(principal, "region_residency_approver")
        preferences = self._validate_provider_preferences(
            request.provider_preferences, storage_regions=storage_regions,
        )
        retention_policy = self._validate_retention_policy(request.retention_policy)
        contact_preferences = self._validate_contact_preferences(request.contact_preferences)
        with self._uow_factory() as uow:
            tenant = uow.tenants.get(tenant_id=principal.tenant_id)
            if tenant is None:
                raise LookupError("tenant not found")
            values = {
                "country_code": country_code, "timezone": timezone, "language": language,
                "data_residency_region": definition.data_residency_region,
                "allowed_storage_regions": storage_regions,
                "retention_policy": retention_policy,
                "provider_preferences": preferences,
                "compliance_profile": definition.compliance_profile,
                "allowed_channels": allowed_channels,
                "local_phone_route": request.local_phone_route,
                "contact_preferences": contact_preferences,
                "active": True, "approval_status": "approved",
                "approved_by": principal.principal_id, "approved_at": _now_utc(),
            }
            row = uow.regions.put(tenant_id=principal.tenant_id,
                                  region_id=request.region_id, values=values)
            supported = set(tenant.supported_region_ids or [])
            supported.add(request.region_id)
            default_id = request.region_id if request.make_default or not tenant.default_region_id else tenant.default_region_id
            if request.make_default and tenant.default_region_id != request.region_id:
                self._require_role(principal, "region_default_approver")
            if default_id not in supported:
                default_id = request.region_id
            uow.tenants.set_region_configuration(
                tenant_id=principal.tenant_id, default_region_id=default_id,
                supported_region_ids=sorted(supported),
            )
            uow.regions.add_audit(
                tenant_id=principal.tenant_id, region_id=request.region_id,
                action="region_enabled", decision="allow", resource_type="tenant",
                resource_id=principal.tenant_id, actor_id=principal.principal_id,
                details={"country_code": country_code,
                         "data_residency_region": definition.data_residency_region,
                         "allowed_storage_regions": storage_regions,
                         "cross_region_storage_approved": cross_region,
                         "allowed_channels": allowed_channels,
                         "default_region_id": default_id},
            )
            return self._config_view(row)

    def policy(self, principal: AuthenticatedPrincipal, region_id: str) -> dict:
        with self._uow_factory() as uow:
            region = uow.regions.get(tenant_id=principal.tenant_id, region_id=region_id)
            if region is None:
                raise LookupError("tenant region not found")
            policy = uow.country_policies.get(country_code=region.country_code)
            overrides = uow.regions.policy_overrides(tenant_id=principal.tenant_id,
                                                      region_id=region_id)
            return {"region_id": region_id, "country_code": region.country_code,
                    "region_group": region.compliance_profile.get("region_group") or
                                   get_region(region_id).region_group,
                    "country_policy_active": bool(policy and policy.active),
                    "country_policy": policy.rules if policy and policy.active else None,
                    "safe_mode": bool(region.compliance_profile.get("safe_mode") or
                                      not policy or not policy.active),
                    "overrides": [{"id": row.id, "rules": row.override_rules,
                                   "reason": row.reason, "approved_by": row.approved_by,
                                   "created_at": row.created_at} for row in overrides]}

    def audit(self, principal: AuthenticatedPrincipal, *, region_id: str,
              limit: int = 100) -> list[dict]:
        with self._uow_factory() as uow:
            if uow.regions.get(tenant_id=principal.tenant_id, region_id=region_id,
                               approved_only=False) is None:
                raise LookupError("tenant region not found")
            rows = uow.regions.audit(tenant_id=principal.tenant_id,
                                     region_id=region_id, limit=limit)
            return [{"id": row.id, "region_id": row.region_id,
                     "action": row.action, "decision": row.decision,
                     "resource_type": row.resource_type,
                     "resource_id": row.resource_id, "actor_id": row.actor_id,
                     "details": row.details, "created_at": row.created_at}
                    for row in rows]

    def add_policy_override(self, principal: AuthenticatedPrincipal,
                            region_id: str, request) -> dict:
        self._require_role(principal, "region_policy_approver")
        if not request.blocked_channels:
            raise ValueError("at least one restrictive channel block is required")
        rules = {}
        for direction, channels in request.blocked_channels.items():
            unique = sorted(set(channels))
            if not unique:
                raise ValueError("each override direction must block at least one channel")
            rules[direction] = {channel: {"allowed": False} for channel in unique}
        with self._uow_factory() as uow:
            if uow.regions.get(tenant_id=principal.tenant_id, region_id=region_id) is None:
                raise LookupError("tenant region not found")
            row = uow.regions.add_policy_override(
                tenant_id=principal.tenant_id, region_id=region_id,
                override_rules=rules, reason=request.reason.strip(),
                approved_by=principal.principal_id,
            )
            uow.regions.add_audit(
                tenant_id=principal.tenant_id, region_id=region_id,
                action="region_policy_override_approved", decision="allow",
                resource_type="policy_override", resource_id=str(row.id),
                actor_id=principal.principal_id,
                details={"blocked_channels": rules, "reason": request.reason.strip()},
            )
            return {"id": row.id, "region_id": region_id,
                    "override_rules": row.override_rules, "approved_by": row.approved_by,
                    "created_at": row.created_at}

    def authorize_storage(self, *, uow, tenant_id: str, region_id: str,
                          target_region_id: str, resource_type: str,
                          resource_id: str, actor_id: str = "system") -> bool:
        row = uow.regions.get(tenant_id=tenant_id, region_id=region_id)
        allowed = bool(row and target_region_id in row.allowed_storage_regions
                       and target_region_id in {item.region_id for item in list_regions()})
        uow.regions.add_audit(
            tenant_id=tenant_id, region_id=region_id,
            action="data_residency_transfer", decision="allow" if allowed else "block",
            resource_type=resource_type, resource_id=resource_id, actor_id=actor_id,
            details={"target_region_id": target_region_id,
                     "residency_region": row.data_residency_region if row else "unknown"},
        )
        return allowed

    def authorize_provider_transfer(self, *, uow, tenant_id: str, business,
                                    provider_kind: str, resource_type: str,
                                    resource_id: str, actor_id: str = "worker") -> str | None:
        region = self.resolve_business_region(
            uow=uow, tenant_id=tenant_id, business=business,
        )
        if region is None:
            # Legacy/test tenants without regional provisioning retain the
            # existing provider path. Migrated and new regional tenants fail closed.
            tenant = uow.tenants.get(tenant_id=tenant_id)
            if tenant is not None and (tenant.supported_region_ids or
                                       getattr(business, "region_id", None)):
                raise RegionAccessDenied("tenant region is not configured or approved")
            return None
        target = region.provider_preferences.get(f"{provider_kind}_region")
        allowed = bool(target and self.authorize_storage(
            uow=uow, tenant_id=tenant_id, region_id=region.region_id,
            target_region_id=target, resource_type=resource_type,
            resource_id=resource_id, actor_id=actor_id,
        ))
        if not allowed:
            uow.commit()
            raise RegionAccessDenied("provider data region is not explicitly approved")
        return target

    def resolve_business_region(self, *, uow, tenant_id: str, business) -> object | None:
        tenant = uow.tenants.get(tenant_id=tenant_id)
        region_id = getattr(business, "region_id", None) or getattr(tenant, "default_region_id", None)
        if not region_id or region_id not in (getattr(tenant, "supported_region_ids", []) or []):
            return None
        region = uow.regions.get(tenant_id=tenant_id, region_id=region_id)
        if region is None:
            return None
        return region

    def assert_runtime_data_access(self, *, uow, tenant_id: str,
                                   runtime_region: str, actor_id: str) -> None:
        tenant = uow.tenants.get(tenant_id=tenant_id)
        if tenant is None:
            raise LookupError("tenant not found")
        supported = set(tenant.supported_region_ids or [])
        if not supported:
            return
        configs = uow.regions.list(tenant_id=tenant_id)
        eligible = next((row for row in configs
                         if row.region_id in supported
                         and runtime_region in row.allowed_storage_regions), None)
        audit_region = eligible.region_id if eligible else tenant.default_region_id
        uow.regions.add_audit(
            tenant_id=tenant_id, region_id=audit_region,
            action="regional_data_access", decision="allow" if eligible else "block",
            resource_type="tenant_database", resource_id=None, actor_id=actor_id,
            details={"runtime_region": runtime_region,
                     "permitted_region_count": len(configs)},
        )
        if eligible is None:
            uow.commit()
            raise RegionAccessDenied("database region is not permitted for tenant data")

    def set_feature_flag(self, principal: AuthenticatedPrincipal, *, region_id: str,
                         request) -> dict:
        self._require_role(principal, "region_release_manager")
        if any(word in request.flag_key.casefold() for word in
               ("policy", "consent", "residency", "compliance")):
            raise ValueError("policy-sensitive feature flags require policy review")
        if len(str(request.config).encode()) > 4096:
            raise ValueError("feature flag configuration is too large")
        with self._uow_factory() as uow:
            if uow.regions.get(tenant_id=principal.tenant_id, region_id=region_id) is None:
                raise LookupError("tenant region not found")
            flag = uow.regions.put_flag(
                tenant_id=principal.tenant_id, region_id=region_id,
                flag_key=request.flag_key, enabled=request.enabled,
                rollout_percentage=request.rollout_percentage, config=request.config,
            )
            uow.regions.add_audit(
                tenant_id=principal.tenant_id, region_id=region_id,
                action="region_feature_flag_updated", decision="allow",
                resource_type="feature_flag", resource_id=request.flag_key,
                actor_id=principal.principal_id,
                details={"enabled": request.enabled,
                         "rollout_percentage": request.rollout_percentage,
                         "config": request.config},
            )
            return {"tenant_id": flag.tenant_id, "region_id": flag.region_id,
                    "flag_key": flag.flag_key, "enabled": flag.enabled,
                    "rollout_percentage": flag.rollout_percentage, "config": flag.config}

    def feature_flag_applies(self, *, tenant_id: str, region_id: str,
                             flag_key: str, subject_id: str) -> tuple[bool, dict]:
        with self._uow_factory() as uow:
            row = uow.regions.flag(tenant_id=tenant_id, region_id=region_id,
                                   flag_key=flag_key)
            if row is None or not row.enabled:
                return False, {}
            bucket = int.from_bytes(sha256(
                f"{tenant_id}:{region_id}:{flag_key}:{subject_id}".encode()
            ).digest()[:4], "big") % 100
            return bucket < row.rollout_percentage, dict(row.config)

    @staticmethod
    def _validate_provider_preferences(values: dict[str, str], *,
                                       storage_regions: list[str]) -> dict[str, str]:
        allowed = {"sms_provider": {"twilio"}, "email_provider": {"sendgrid", "smtp"},
                   "voice_provider": {"twilio"}, "calendar_provider": {"google"},
                   "crm_provider": {"hubspot", "salesforce"}}
        region_keys = {f"{kind}_region" for kind in
                       ("sms", "email", "voice", "calendar", "crm")}
        if set(values) - set(allowed) - region_keys:
            raise ValueError("unsupported provider preference")
        if any(value not in allowed[key] for key, value in values.items()
               if key in allowed):
            raise ValueError("provider preference is unsupported")
        for key in region_keys.intersection(values):
            get_region(values[key])
            if values[key] not in storage_regions:
                raise ValueError("provider data region must be an explicitly allowed storage region")
        return dict(values)

    @staticmethod
    def _validate_contact_preferences(values: dict[str, str]) -> dict[str, str]:
        allowed = {"preferred_channel", "timezone", "language", "date_format", "time_format"}
        if set(values) - allowed:
            raise ValueError("unsupported country contact preference")
        if "timezone" in values:
            try:
                ZoneInfo(values["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("contact preference timezone must be a valid IANA timezone") from None
        return dict(values)

    @staticmethod
    def _validate_retention_policy(values: dict[str, int]) -> dict[str, int]:
        allowed = {"contact_days", "conversation_days", "message_days", "event_days",
                   "audit_days", "analytics_days"}
        if set(values) - allowed:
            raise ValueError("unsupported retention policy category")
        if any(not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3650
               for days in values.values()):
            raise ValueError("retention periods must be between 1 and 3650 days")
        return dict(values)

    @staticmethod
    def _config_view(row) -> dict:
        return {"tenant_id": row.tenant_id, "region_id": row.region_id,
                "country_code": row.country_code, "timezone": row.timezone,
                "language": row.language,
                "data_residency_region": row.data_residency_region,
                "retention_policy": row.retention_policy,
                "allowed_storage_regions": list(row.allowed_storage_regions),
                "provider_preferences": row.provider_preferences,
                "compliance_profile": row.compliance_profile,
                "allowed_channels": list(row.allowed_channels),
                "local_phone_route_configured": bool(row.local_phone_route),
                "contact_preferences": row.contact_preferences,
                "active": row.active, "approval_status": row.approval_status,
                "approved_by": row.approved_by, "approved_at": row.approved_at}

    @staticmethod
    def _require_role(principal: AuthenticatedPrincipal, role: str) -> None:
        if not isinstance(principal, AuthenticatedPrincipal) or role not in principal.roles:
            raise PermissionError("region operation denied")


def _now_utc():
    from datetime import UTC, datetime
    return datetime.now(UTC)
