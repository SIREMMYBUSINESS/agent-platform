"""Require migration and operational changes when a PR enables regional routing."""

import argparse
import ast
from collections import Counter
import re
import subprocess


ENABLED_SETTING = re.compile(
    r"\bTENANT_REGION_ROUTING_ENABLED\b[\"']?\s*[:=]\s*[\"']?(?:true|1|yes|on)\b",
    re.IGNORECASE,
)


def enabled_lines(content: str) -> Counter:
    return Counter(
        line.strip() for line in content.splitlines()
        if not line.lstrip().startswith("#") and ENABLED_SETTING.search(line)
    )


def python_default_enabled(content: str) -> bool:
    for node in ast.walk(ast.parse(content)):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "tenant_region_routing_enabled":
            continue
        value = node.value
        if isinstance(value, ast.Call):
            value = next((item.value for item in value.keywords if item.arg == "default"), None)
        return isinstance(value, ast.Constant) and value.value is True
    return False


def validate_rollout(changes: dict[str, tuple[str, str]]) -> list[str]:
    enabling_paths = []
    for path, (before, after) in changes.items():
        if path.startswith(("tests/", "ops/", "migrations/", "scripts/")) or path.endswith(".md"):
            continue
        enabling = bool(enabled_lines(after) - enabled_lines(before))
        if path == "app/config.py":
            enabling |= python_default_enabled(after) and not python_default_enabled(before)
        if enabling:
            enabling_paths.append(path)
    if not enabling_paths:
        return []
    migration = any(path.startswith("migrations/versions/") and path.endswith(".py") and after
                    for path, (_, after) in changes.items())
    operations = any(path.startswith("ops/") and after for path, (_, after) in changes.items())
    if migration and operations:
        return []
    missing = [name for name, present in (("migration", migration), ("ops update", operations)) if not present]
    return [f"Routing enabled in {', '.join(enabling_paths)} without accompanying {' and '.join(missing)}. "
            "Keep TENANT_REGION_ROUTING_ENABLED=false until the rollout gates are met."]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def revision_file(revision: str, path: str) -> str:
    result = subprocess.run(["git", "show", f"{revision}:{path}"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode == 0:
        return result.stdout
    # A new or deleted file is absent at one end of the diff.
    if not git("ls-tree", revision, "--", path).strip():
        return ""
    raise RuntimeError(f"Cannot read changed file {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", default="HEAD")
    args = parser.parse_args()
    base = git("merge-base", args.base_ref, args.head_ref).strip()
    paths = git("diff", "--name-only", "--no-renames", "-z", base, args.head_ref).split("\0")
    changes = {path: (revision_file(base, path), revision_file(args.head_ref, path))
               for path in paths if path}
    errors = validate_rollout(changes)
    for error in errors:
        print(error)
    if not errors:
        print("Regional rollout guard passed.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
