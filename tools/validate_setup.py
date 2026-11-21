"""Validate project configuration and acceptance documentation."""
import json
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

def main():
    config = tomllib.loads((ROOT / "agent-config/config.toml").read_text(encoding="utf-8"))
    assert config["agents"]["enabled"] is True
    assert config["agents"]["max_concurrent_threads_per_session"] == 5
    roles = {k:v for k,v in config["agents"].items() if isinstance(v,dict)}
    assert len(roles) == 5
    for name, role in roles.items():
        target = ROOT / "agent-config" / role["config_file"]
        parsed = tomllib.loads(target.read_text(encoding="utf-8"))
        assert len(parsed["developer_instructions"]) > 500, name
        assert "model" not in parsed, name
    data = json.loads((ROOT / "docs/acceptance.json").read_text(encoding="utf-8"))
    checks = data["checks"]
    assert len(checks) == 85
    assert len({c["id"] for c in checks}) == 85
    assert sum(c["weight"] for c in checks) == 100
    matrix = (ROOT / "docs/ACCEPTANCE_MATRIX.md").read_text(encoding="utf-8")
    matrix_rows = re.findall(r"^\| ([CDRPE]\d{2}) \|.*\| ([A-Z_]+) \|$", matrix, re.MULTILINE)
    assert len(matrix_rows) == len(checks)
    assert dict(matrix_rows) == {check["id"]: check["status"] for check in checks}

    runs = data.get("verification_runs", {})
    for check in checks:
        status = check["status"]
        assert status in {"TODO", "IN_PROGRESS", "BLOCKED", "VERIFIED"}, check["id"]
        if status == "VERIFIED":
            evidence = check["evidence"]
            assert evidence and evidence["result"], check["id"]
            assert evidence["runs"] and evidence["references"], check["id"]
            assert check["planned_test"] == evidence["references"][0]["path"], check["id"]
            for run_id in evidence["runs"]:
                run = runs[run_id]
                assert all(run[key] for key in ("command", "version", "commit", "report")), run_id
            for reference in evidence["references"]:
                assert (ROOT / reference["path"]).is_file(), reference["path"]
    if data["status"] == "LOCAL_VERIFIED":
        assert all(check["status"] == "VERIFIED" for check in checks)
    for prefix, count in (("C",20),("D",20),("R",20),("P",20),("E",5)):
        assert {c["id"] for c in checks if c["id"].startswith(prefix)} == {
            prefix + str(i).zfill(2) for i in range(1,count+1)}
    source = (ROOT / "docs/sources/check_definitions_2026-11-20.txt").read_text(encoding="utf-8")
    for check in checks:
        if check["id"].startswith("C"):
            assert check["id"] + "\t" in source
            assert check["stimulus"] in source
            assert check["expected"] in source
        if check["id"].startswith("R"):
            assert check["stimulus"] in source
            assert check["expected"] in source
    required = [
        "AGENTS.md", "README.md", "docs/WORK_PLAN.md", "docs/ARCHITECTURE.md",
        "docs/TASKS.md", "docs/STATUS.md", "docs/START_AGENT.md",
        "docs/RETROSPECTIVE.md", "docs/ACCEPTANCE_MATRIX.md",
        "docs/sources/fintech_case_extracted.txt", "agent-config/README.md",
    ]
    for relative in required:
        path = ROOT / relative
        assert path.is_file() and path.stat().st_size > 0, relative
    task_ids = set(re.findall(r"\| (T\d{2}) \|", (ROOT / "docs/TASKS.md").read_text(encoding="utf-8")))
    assert task_ids == {"T"+str(i).zfill(2) for i in range(12)}
    print(json.dumps({
        "scope":"configuration and acceptance evidence references",
        "toml_files_parsed":6,"roles":sorted(roles),
        "scoring_checks":len(checks),"maximum_points":100,
        "locally_verified_checks":sum(check["status"] == "VERIFIED" for check in checks),
        "task_ids":sorted(task_ids),"required_files_checked":len(required),
        "custom_roles_activation":"not installed; protected .codex write was rejected"
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()

