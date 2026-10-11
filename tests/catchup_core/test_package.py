import catchup_core


def test_contract_version_is_semver():
    parts = catchup_core.CORE_CONTRACT_VERSION.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)
