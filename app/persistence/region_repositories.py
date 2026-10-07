"""Tenant-scoped regional configuration, policy, audit, and rollout stores."""
from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.persistence.models import (
    RegionAuditLog, RegionFeatureFlag, RegionPolicyOverride, TenantRegionConfig,
)
from app.persistence.repositories import _database_call, _require_tenant


class SQLRegionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, tenant_id: str, region_id: str, approved_only: bool = True):
        _require_tenant(tenant_id)
        statement = select(TenantRegionConfig).where(
            TenantRegionConfig.tenant_id == tenant_id,
            TenantRegionConfig.region_id == region_id,
        )
        if approved_only:
            statement = statement.where(TenantRegionConfig.active.is_(True),
                                        TenantRegionConfig.approval_status == "approved")
        return _database_call(lambda: self._session.exec(statement).first())

    def list(self, *, tenant_id: str, active_only: bool = True):
        _require_tenant(tenant_id)
        statement = select(TenantRegionConfig).where(TenantRegionConfig.tenant_id == tenant_id)
        if active_only:
            statement = statement.where(TenantRegionConfig.active.is_(True),
                                        TenantRegionConfig.approval_status == "approved")
        return _database_call(lambda: list(self._session.exec(
            statement.order_by(TenantRegionConfig.region_id)
        ).all()))

    def put(self, *, tenant_id: str, region_id: str, values: dict):
        _require_tenant(tenant_id)
        row = self._session.get(TenantRegionConfig, (tenant_id, region_id))
        if row is None:
            row = TenantRegionConfig(tenant_id=tenant_id, region_id=region_id, **values)
            self._session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        self._session.flush()
        return row

    def add_policy_override(self, *, tenant_id: str, region_id: str,
                            override_rules: dict, reason: str, approved_by: str):
        _require_tenant(tenant_id)
        row = RegionPolicyOverride(tenant_id=tenant_id, region_id=region_id,
                                   override_rules=override_rules, reason=reason,
                                   approved_by=approved_by)
        self._session.add(row)
        self._session.flush()
        return row

    def policy_overrides(self, *, tenant_id: str, region_id: str):
        _require_tenant(tenant_id)
        statement = select(RegionPolicyOverride).where(
            RegionPolicyOverride.tenant_id == tenant_id,
            RegionPolicyOverride.region_id == region_id,
            RegionPolicyOverride.active.is_(True),
        ).order_by(RegionPolicyOverride.created_at, RegionPolicyOverride.id)
        return _database_call(lambda: list(self._session.exec(statement).all()))

    def add_audit(self, *, tenant_id: str, region_id: str, action: str,
                  decision: str, resource_type: str, resource_id: str | None,
                  actor_id: str, details: dict):
        _require_tenant(tenant_id)
        row = RegionAuditLog(tenant_id=tenant_id, region_id=region_id,
                             action=action, decision=decision,
                             resource_type=resource_type, resource_id=resource_id,
                             actor_id=actor_id, details=details)
        self._session.add(row)
        self._session.flush()
        return row

    def audit(self, *, tenant_id: str, region_id: str | None = None, limit: int = 100):
        _require_tenant(tenant_id)
        statement = select(RegionAuditLog).where(RegionAuditLog.tenant_id == tenant_id)
        if region_id is not None:
            statement = statement.where(RegionAuditLog.region_id == region_id)
        return _database_call(lambda: list(self._session.exec(
            statement.order_by(RegionAuditLog.created_at.desc()).limit(limit)
        ).all()))

    def flag(self, *, tenant_id: str, region_id: str, flag_key: str):
        _require_tenant(tenant_id)
        return _database_call(lambda: self._session.get(
            RegionFeatureFlag, (tenant_id, region_id, flag_key),
        ))

    def put_flag(self, *, tenant_id: str, region_id: str, flag_key: str,
                 enabled: bool, rollout_percentage: int, config: dict):
        _require_tenant(tenant_id)
        row = self.flag(tenant_id=tenant_id, region_id=region_id, flag_key=flag_key)
        if row is None:
            row = RegionFeatureFlag(tenant_id=tenant_id, region_id=region_id,
                                    flag_key=flag_key, enabled=enabled,
                                    rollout_percentage=rollout_percentage, config=config)
            self._session.add(row)
        else:
            row.enabled, row.rollout_percentage, row.config = enabled, rollout_percentage, config
        self._session.flush()
        return row
