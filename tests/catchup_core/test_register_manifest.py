"""The register and manifest are rendered from code, so they can be checked without the repository tooling."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from catchup_core.capabilities import CAPABILITIES, manifest, render_register

REPO = Path(__file__).resolve().parents[2]


def test_register_lists_every_capability_with_present_or_absent_evidence():
    text = render_register(REPO)
    assert text.count("\n| RA-") == len(CAPABILITIES)
    assert "| RA-01 |" in text and "present" in text and "generated" in text


def test_register_names_one_closure_route_per_capability_matching_the_completion_plan():
    from catchup_core.capabilities import CLOSURE_ROUTES
    assert set(CLOSURE_ROUTES) == {c.id for c in CAPABILITIES}
    text = render_register(REPO)
    assert "| Closure route |" in text
    assert "retained by the incumbent (Decision 3)" in CLOSURE_ROUTES["RA-11"] == CLOSURE_ROUTES["RA-25"]
    assert "waived (Decision 1)" in CLOSURE_ROUTES["RA-13"]
    assert "deferred in writing" in CLOSURE_ROUTES["RA-19"] and "deferred in writing" in CLOSURE_ROUTES["RA-20"]
    assert "consent" in CLOSURE_ROUTES["RA-12"] and "reminder" in CLOSURE_ROUTES["RA-24"]
    # The register states a route, never that a requirement is accepted: acceptance is owner evidence.
    assert not any("accepted" in route.replace("acceptance", "") for route in CLOSURE_ROUTES.values())


def test_manifest_maps_acceptance_ids_and_pins_implementation_bytes(tmp_path):
    first = manifest(REPO)
    assert first["acceptance"]["RA-01.AC1"]["capability"] == "RA-01" and first["contract_version"]
    assert json.dumps(first)                                            # serializable
    fake = tmp_path
    (fake / "specs").mkdir()
    (fake / "specs" / "re-artha.md").write_text("spec")
    with_spec = manifest(fake)
    assert len(with_spec["spec_sha256"]) == 64
    without = manifest(tmp_path / "nowhere", {"spec_sha256": "kept"})
    assert without["spec_sha256"] == "kept"                             # CI has no spec: the recorded digest is kept


def test_traceability_register_covers_every_requirement_and_every_prd_group():
    from catchup_core.capabilities import FR_DISPOSITION, render_traceability
    text = render_traceability(REPO)
    assert all(f"| {c.id} " in text for c in CAPABILITIES)
    assert all(group in text for group, *_ in FR_DISPOSITION) and "(missing)" not in text


def test_named_boundary_cases_resolve_to_real_fixtures_and_never_claim_live_evidence():
    import ast
    from catchup_core.capabilities import BOUNDARY_CASES
    data = manifest(REPO)
    for case, _ in BOUNDARY_CASES:
        path, fixture = data['boundary_cases'][case]['fixture'].split('::')
        names = {node.name for node in ast.parse((REPO / path).read_text()).body if isinstance(node, ast.FunctionDef)}
        assert fixture in names
        assert data['boundary_cases'][case]['evidence_type'] == 'synthetic'


def test_completion_controls_are_part_of_the_release_implementation_cohort():
    registered = {module for cap in CAPABILITIES for module in cap.modules}
    assert {
        'scripts/lib/action_evidence.py', 'scripts/catchup_core/fact_access.py',
        'scripts/catchup_core/resources.py', 'scripts/catchup_core/retention.py',
        'scripts/catchup_core/authority_transfer.py', 'scripts/catchup_core/field_confirmation.py',
        'scripts/catchup_core/interchange.py', 'scripts/catchup_core/user_config.py',
        'scripts/connectors/google_email.py', 'scripts/connectors/msgraph_email.py',
        'scripts/connectors/imap_email.py', 'scripts/catchup_core/quarantine_fs_windows.py',
        'scripts/catchup_core/work_privacy.py', 'scripts/lib/work_publication.py',
        'scripts/post_work_refresh.py', 'scripts/backfill/backfill_runner.py', 'scripts/narrative/__init__.py',
        'tools/run_tests_isolated.py',
    } <= registered


def test_work_recovery_privacy_controls_have_mandatory_security_coverage():
    makefile = (REPO / 'Makefile').read_text(encoding='utf-8')
    modules = next(line for line in makefile.splitlines() if line.startswith('HARNESS_MODULES = ')).split()
    tests = next(line for line in makefile.splitlines() if line.startswith('HARNESS_TESTS = ')).split()
    assert {'catchup_core.work_privacy', 'lib.work_publication'} <= set(modules)
    assert {
        'tests/catchup_core/test_work_privacy.py', 'tests/unit/test_work_publication.py',
        'tests/catchup_core/test_state_checkpoint_cli.py',
    } <= set(tests)


def test_storage_adoption_is_registered_and_has_mandatory_security_coverage():
    registered = {module for cap in CAPABILITIES for module in cap.modules}
    fixtures = {test for cap in CAPABILITIES for test in cap.tests}
    assert 'scripts/catchup_core/storage_adoption.py' in registered
    assert 'tests/catchup_core/test_storage_adoption.py' in fixtures
    makefile = (REPO / 'Makefile').read_text(encoding='utf-8')
    modules = next(line for line in makefile.splitlines() if line.startswith('HARNESS_MODULES = ')).split()
    tests = next(line for line in makefile.splitlines() if line.startswith('HARNESS_TESTS = ')).split()
    assert 'catchup_core.storage_adoption' in modules
    assert 'tests/catchup_core/test_storage_adoption.py' in tests


def test_corpus_provenance_regressions_are_in_the_capability_register():
    fixtures = {test for cap in CAPABILITIES for test in cap.tests}
    assert 'tests/catchup_core/test_corpus_provenance.py' in fixtures


def test_manifest_uses_portable_paths_for_migrations_and_prompts(tmp_path):
    paths = {
        'scripts/catchup_core/migrations/0001.sql': b'SELECT 1;',
        'scripts/catchup_core/prompts/extract.md': b'Synthetic extraction instructions.',
    }
    for name, data in paths.items():
        path = tmp_path.joinpath(*name.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    expected = '\n'.join(name + ':' + hashlib.sha256(data).hexdigest()
                         for name, data in sorted(paths.items()))
    assert manifest(tmp_path)['implementation_sha256'] == hashlib.sha256(expected.encode()).hexdigest()


def test_runtime_resume_fingerprint_uses_one_portable_label_per_source(tmp_path):
    from catchup_core.runtime import _implementation_fingerprint
    files = {'scripts/catchup_core/runtime.py': b'synthetic runtime',
             'scripts/catchup_core/migrations/0001.sql': b'SELECT 1;'}
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    paths = {name for capability in CAPABILITIES for name in capability.modules if name.startswith('scripts/')}
    paths.update(files)
    expected = hashlib.sha256()
    for name in sorted(paths):
        expected.update(name.encode() + b'\0')
        expected.update(hashlib.sha256(files[name]).digest() if name in files else b'missing')
    assert _implementation_fingerprint(tmp_path) == expected.hexdigest()
