"""Convert a HAR locally without replaying its requests or storing the upload.

Run with backend/.venv/Scripts/python.exe scripts/convert_har.py INPUT --output OUTPUT.jmx.
The adjacent manifest records filtering, dependencies and required runtime properties.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import Settings  # noqa: E402
from app.services.analysis_service import build_har_analysis  # noqa: E402
from app.services.auto_correlator import auto_correlate  # noqa: E402
from app.services.har_import import parse_har  # noqa: E402
from app.services.jmx_builder import BuildOptions, JmxBuilder  # noqa: E402
from app.services.jmx_validator import validate_jmx  # noqa: E402
from app.services.manifest_builder import build_manifest  # noqa: E402
from app.services.rule_validator import validate_rule  # noqa: E402


def convert(source: Path, output: Path) -> dict:
    settings = Settings()
    recording = parse_har(source.read_bytes(), filename=source.name, settings=settings)
    selected = recording.default_selection()
    if not selected:
        raise ValueError("No replayable first-party requests were selected. Review this HAR in the app.")
    analysis = build_har_analysis([recording], [selected], settings)
    rules = auto_correlate(analysis.sequence_run, settings)
    names = set()
    for rule in rules:
        issues = validate_rule(rule, analysis.sequence_run, analysis.sequence_run, names)
        if issues:
            raise ValueError(f"Review correlation {rule.variable_name} in the app: "
                             + "; ".join(issue.detail for issue in issues))
        names.add(rule.variable_name)
    result = JmxBuilder(BuildOptions(browser_recording=True, keep_user_agent=True)).build(
        analysis.sequence_run, rules,
    )
    validation = validate_jmx(result.xml)
    if not validation.ok or result.errors or result.unmaterialized:
        raise ValueError(f"Plan validation failed: {validation.errors + result.errors}; "
                         f"unmaterialized replacements: {len(result.unmaterialized)}")
    manifest = build_manifest(
        analysis.sequence_run, rules, result, candidates_found=len(analysis.candidates),
        manual_added=0, edited=0, deleted=0, warnings=analysis.warnings + result.warnings,
    )
    manifest["filtering"] = {
        "recorded": len(recording.entries), "selected": len(selected),
        "decisions": dict(recording.counts()), "removed_by_reason": recording.removed_by_reason(),
    }
    manifest["validation"] = {"structural": "passed", "target_replay": "not_performed"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.xml, encoding="utf-8")
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = convert(args.input, args.output)
    print(json.dumps({"filtering": report["filtering"], "variables": report["variables"],
                      "required_properties": report["secret_handling"]["required_properties"]}, indent=2))
