"""Inspect project preparation only; never run downloaded code or start services."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PINS = {
    "llm-soft.yml": "6b0a537c5af8b85af76e4ac0622ba0c4649883844997244966e8db97c1ba043d",
    "soft_runner.py": "ed9f4f45e2e5cba23bf4c67d58be9ce10e69576e8bdd36904c442788354676cb",
    "soft_check.py": "43878383b810358a5dbd8a796c94acd3f6cb39fc8665929a2d5354f079eb65db",
    "credential_fixture.py": "ef860b68237f946d2394f00e48eb2f8d3c17bded84861542232cefbeed66d19c",
    "acceptance.live.json": "d5cd2fdc90056e710cf741ac1eec4c5b042ea3818d505e4a3975697f1df9241f",
}
DELIVERABLES = [
    "setup.sh", "requirements.lock", ".env.example",
    "configs/baseline.yaml", "configs/optimized.yaml", "configs/pricing.json",
    "server/app.py", "server/engine.py", "server/admission.py", "server/metrics.py",
    "server/supervisor.py", "control_plane", "console", "migrations",
    "scripts/check_control_plane.py", "agent", "benchmarks",
    "experiments/run.sh", "experiments/kv.py", "experiments/prefix.py",
    "experiments/attention.py", "tests", "docs/performance.md",
]


def git(*args):
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=10
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def collect():
    checks = []
    for name, expected in PINS.items():
        path = ROOT / "tools" / "upstream" / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        checks.append({"check": f"sha256:{name}", "passed": digest == expected,
                       "expected": expected, "actual": digest})

    workflow = ROOT / ".github/workflows/llm-soft.yml"
    source = ROOT / "tools/upstream/llm-soft.yml"
    checks.append({"check": "workflow_byte_equality", "passed": (
        workflow.is_file() and source.is_file() and workflow.read_bytes() == source.read_bytes()
    )})
    try:
        snapshot = json.loads((ROOT / "docs/acceptance.taskbook.json").read_text(encoding="utf-8"))
        live = json.loads((ROOT / "tools/upstream/acceptance.live.json").read_text(encoding="utf-8"))
        matches = snapshot == live
        version = live.get("version")
    except (OSError, ValueError):
        matches, version = False, None
    checks.append({"check": "taskbook_vs_downloaded_acceptance", "passed": matches,
                   "version": version, "network_refreshed_by_this_script": False})
    missing = [p for p in DELIVERABLES if not (ROOT / p).exists()]
    return {
        "schema": "llm-project-preparation/1", "checked_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(), "python": platform.python_version(),
        "branch": git("branch", "--show-current"), "commit_sha": git("rev-parse", "--verify", "HEAD"),
        "remote_names": (git("remote") or "").splitlines(),
        "preparation_checks": checks,
        "preparation_status": "PASS" if all(c["passed"] for c in checks) else "FAIL",
        "deliverable_presence": {"checked": len(DELIVERABLES), "missing": missing,
                                 "content_or_behavior_verified": False},
        "service_status": "NOT_IMPLEMENTED" if missing else "NOT_VERIFIED",
        "cpu_interfaces": "NOT_TESTED", "github_actions": "NOT_TESTED",
        "gpu_deployment": "NOT_TESTED", "official_score": False,
        "limitations": ["File presence is not implementation correctness.",
                        "Resource hashes verify this downloaded batch, not current network state.",
                        "No downloaded code, model inference, service or workflow is executed."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New output path; existing evidence is never overwritten")
    args = parser.parse_args()
    report = collect()
    if args.out:
        with args.out.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # This entry point verifies preparation, not whether the LLM service is complete.
    return 0 if report["preparation_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
