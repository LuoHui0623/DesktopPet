import json
from pathlib import Path

from scripts.export_openapi import export_openapi


def test_export_contains_health_and_shutdown_operations(tmp_path: Path):
    target = tmp_path / "openapi.json"
    export_openapi(target)
    spec = json.loads(target.read_text(encoding="utf-8"))

    health = spec["paths"]["/api/health"]["get"]
    assert health["operationId"] == "health"
    assert "200" in health["responses"]

    shutdown = spec["paths"]["/api/shutdown"]["post"]
    assert shutdown["operationId"] == "shutdown"
    assert "202" in shutdown["responses"]


def test_export_is_deterministic(tmp_path: Path):
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    export_openapi(first)
    export_openapi(second)
    assert first.read_bytes() == second.read_bytes()
