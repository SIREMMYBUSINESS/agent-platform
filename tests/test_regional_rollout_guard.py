import pytest

from scripts.check_regional_rollout import validate_rollout


@pytest.mark.parametrize("enabled", ["true", "1", "yes", "on"])
def test_enabling_routing_requires_migration_and_ops_changes(enabled):
    changes = {".env.example": ("TENANT_REGION_ROUTING_ENABLED=false", f"TENANT_REGION_ROUTING_ENABLED={enabled}")}
    assert validate_rollout(changes)
    changes["migrations/versions/0031_control.py"] = ("", "# migration")
    assert validate_rollout(changes)
    changes["ops/runbooks/enable-routing.md"] = ("", "Rollout and rollback procedure")
    assert validate_rollout(changes) == []


def test_python_settings_default_cannot_enable_routing_without_rollout_files():
    before = "class Settings:\n    tenant_region_routing_enabled: bool = False\n"
    after = "class Settings:\n    tenant_region_routing_enabled: bool = Field(\n        default=True,\n    )\n"
    assert validate_rollout({"app/config.py": (before, after)})


def test_tests_and_documented_examples_do_not_trigger_enablement_guard():
    assert validate_rollout({
        "tests/example.py": ("", 'TENANT_REGION_ROUTING_ENABLED=true'),
        "ops/runbooks/example.md": ("", 'TENANT_REGION_ROUTING_ENABLED=true'),
        ".env.example": ("", '# TENANT_REGION_ROUTING_ENABLED=true\nTENANT_REGION_ROUTING_ENABLED=false'),
    }) == []


def test_deleting_rollout_files_does_not_satisfy_guard():
    assert validate_rollout({
        ".github/workflows/deploy.yml": ("", 'TENANT_REGION_ROUTING_ENABLED: "true"'),
        "migrations/versions/0031_control.py": ("# migration", ""),
        "ops/runbooks/enable-routing.md": ("procedure", ""),
    })
