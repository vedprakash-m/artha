# ruff: noqa: E501  (the register is a data table; one capability per row reads better than wrapped rows)
"""Capability register (re-artha RA-10, §11.5): what is specified, built, enabled, exercised, measured.

The *static* part (code present, tests present) is generated from this table and checked in CI
(``tools/gen_capability_register.py --check``).  The *dynamic* part is computed from configuration
(enabled), the stage journal (exercised) and a verified release scorecard (measured), so a test count
or a flag can never mark a feature production-ready on its own.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CAPABILITIES", "FR_DISPOSITION", "Capability", "manifest", "render_register", "render_traceability", "runtime_states", "static_status"]


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    scope: str                       # pilot | reduced | later
    modules: tuple[str, ...]
    tests: tuple[str, ...]
    acceptance: tuple[str, ...]
    owner: str = "owner"


_C = "scripts/catchup_core/"
_T = "tests/catchup_core/"
CAPABILITIES: tuple[Capability, ...] = (
    Capability("RA-01", "Coverage-aware Catch-up", "pilot", (_C + "coordinator.py", _C + "adapters.py"),
               (_T + "test_coordinator.py", _T + "test_adapters.py", _T + "test_recovery_failures.py"), ("RA-01.AC1",)),
    Capability("RA-02", "Evidence and temporal Fact lifecycle", "pilot", (_C + "reconcile.py", _C + "extract.py", _C + "ledger.py", _C + "schema_history.py"),
               (_T + "test_fact_reconcile.py", _T + "test_extract.py", _T + "test_ledger.py", _T + "test_schema_history.py"), ("RA-02.AC1",)),
    Capability("RA-03", "Alert and Open Item discipline", "pilot", (_C + "triage.py",), (_T + "test_triage.py",), ("RA-03.AC1",)),
    Capability("RA-04", "Briefing and question answering", "pilot", (_C + "compose.py", _C + "views.py"),
               (_T + "test_compose.py", _T + "test_user_surfaces.py"), ("RA-04.AC1",)),
    Capability("RA-05", "Staged action and review (previews; no external execution before Stage 6)", "reduced",
               (_C + "actions.py", _C + "action_mapping.py", "scripts/action_queue.py", "scripts/action_executor.py",
                "scripts/action_orchestrator.py", "scripts/actions/calendar_modify.py", "scripts/actions/todo_sync_action.py"),
               (_T + "test_actions.py", _T + "test_action_evidence_preconditions.py", _T + "test_action_mapping.py", "tests/unit/test_action_hardening.py",
                "tests/unit/test_reminder_reconcile.py", "tests/unit/test_action_queue_durability.py", "tests/unit/test_action_quarantine.py",
                "tests/unit/test_calendar_action_versions.py"), ("RA-05.AC1",)),
    Capability("RA-06", "Corrections and deliberate memory", "pilot", ("scripts/lib/memory_policy.py", _C + "commands.py"),
               ("tests/unit/test_memory_policy.py", _T + "test_user_surfaces.py"), ("RA-06.AC1",)),
    Capability("RA-07", "Model and tool policy (model-neutral extraction)", "pilot", (_C + "exchange.py", _C + "extractor_cmd.py", _C + "resources.py"),
               (_T + "test_exchange.py", _T + "test_extractor_cmd.py", _T + "test_resource_usage.py",
                _T + "test_resource_runtime.py", _T + "test_resource_lifecycle_controls.py"), ("RA-07.AC1",)),
    Capability("RA-08", "Connector boundary and ingest bundles", "pilot",
               (_C + "adapters.py", _C + "bundles.py", "scripts/schemas/connector_record.py",
                "scripts/connectors/google_calendar.py", "scripts/connectors/msgraph_calendar.py",
                "scripts/connectors/caldav_calendar.py", "scripts/connectors/apple_reminders.py",
                "scripts/connectors/google_email.py", "scripts/connectors/msgraph_email.py",
                "scripts/connectors/imap_email.py"),
               (_T + "test_adapters.py", _T + "test_bundles.py", "tests/unit/test_google_calendar_coverage.py",
                "tests/unit/test_calendar_handlers_coverage.py", "tests/unit/test_apple_reminders.py",
                _T + "test_provider_cursor_chains.py", _T + "test_provider_observation_lifecycle.py"), ("RA-08.AC1",)),
    Capability("RA-09", "Privacy, Work isolation, and the bounded read-only answer interface (MCP)", "pilot",
               (_C + "policy.py", _C + "mcp.py", _C + "work_cache.py", _C + "work_privacy.py",
                "scripts/lib/work_publication.py", "scripts/tool_guard.py", "scripts/lib/credentials.py",
                "scripts/connectors/workiq_bridge.py", "scripts/lib/local_paths.py", "scripts/lib/workiq_health.py",
                "scripts/post_work_refresh.py", "scripts/backfill/backfill_runner.py", "scripts/narrative/__init__.py",
                "scripts/preflight/integration_checks.py", "scripts/preflight/_types.py", "scripts/preflight.py"),
               ("tests/unit/test_tool_guard.py", _T + "test_release_regressions.py", _T + "test_mcp.py",
                "tests/unit/test_credentials_providers.py", _T + "test_work_cache.py",
                _T + "test_work_privacy.py", "tests/unit/test_work_publication.py",
                "tests/unit/test_work_cache_integration.py"), ("RA-09.AC1",)),
    Capability("RA-10", "Capability and evaluation ledger (logical Catch-up measurements)", "pilot",
               (_C + "capabilities.py", _C + "metrics.py", _C + "corpus.py", _C + "corpus_cli.py",
                _C + "ledger.py", _C + "coordinator.py", _C + "commands.py",
                "tools/gen_capability_register.py", "tools/benchmark_core.py", "tools/run_tests_isolated.py"),
               ("tests/dev/test_capability_register.py", _T + "test_metrics.py", _T + "test_benchmark.py",
                _T + "test_corpus.py", _T + "test_corpus_boundaries.py", _T + "test_corpus_provenance.py",
                _T + "test_release_acceptance_v4.py",
                "tests/dev/test_isolated_runner.py", "tests/dev/test_private_work_verification.py"), ("RA-10.AC1",)),
    Capability("RA-11", "Goal and Sprint intelligence (dated source metrics, weekly comparisons and Sprint validation)", "pilot",
               (_C + "goals.py", _C + "goal_metrics.py", _C + "packs/goals.yaml", "scripts/goals_writer.py"),
               (_T + "test_goals_scorecard_cli.py", _T + "test_goal_metrics.py", _T + "test_weekly_goals.py",
                _T + "test_goals_product.py", _T + "test_wiring.py", _T + "test_goal_leading_indicators.py", "tests/unit/test_goals_writer.py"), ("RA-11.AC1",)),
    Capability("RA-12", "Household permission and consent (record-scoped curated retrieval)", "pilot",
               (_C + "household.py", _C + "policy.py", _C + "curated.py", _C + "runtime.py", _C + "legacy_state.py"),
               (_T + "test_household.py", _T + "test_release_regressions.py", _T + "test_curated_permissions.py"), ("RA-12.AC1",)),
    Capability("RA-13", "Cross-Domain cases", "pilot", (_C + "triage.py",), (_T + "test_kids_school_scenarios.py",), ("RA-13.AC1",)),
    Capability("RA-14", "Domain and Connector packs", "pilot", (_C + "domain_pack.py", _C + "packs/kids_school.yaml", _C + "packs/vehicle.yaml"),
               (_T + "test_domain_pack.py", _T + "test_composite_pack.py", _T + "test_domain_pack_contracts.py",
                _T + "test_domain_pack_additional.py"), ("RA-14.AC1",)),
    Capability("RA-15", "Onboarding, correction and feedback", "pilot", (_C + "commands.py", _C + "views.py", _C + "user_config.py"),
               (_T + "test_user_surfaces.py", _T + "test_integrated_surfaces.py", _T + "test_user_config_sources.py"), ("RA-15.AC1",)),
    Capability("RA-16", "Processing closure and silent-failure detection", "pilot", (_C + "coordinator.py", "scripts/lib/connector_coverage.py"),
               (_T + "test_recovery_failures.py", "tests/unit/test_stale_banner.py",
                "tests/unit/test_connector_coverage.py"), ("RA-16.AC1",)),
    Capability("RA-17", "Deadline horizon and staleness honesty", "pilot", (_C + "triage.py", "scripts/stale_banner.py", "scripts/session_stop.py"),
               (_T + "test_triage.py", "tests/unit/test_stale_banner.py", "tests/unit/test_session_stop.py"), ("RA-17.AC1",)),
    Capability("RA-18", "Security architecture and harness enforcement", "pilot",
               (_C + "rule_of_two.py", _C + "crypto.py", "scripts/tool_guard.py", "scripts/lib/credentials.py",
                "scripts/lib/credential_migration.py", "scripts/migrate_credentials.py", "scripts/google_auth.py",
                "scripts/setup_msgraph_oauth.py", "scripts/preflight/oauth_checks.py", "scripts/lib/auth.py"),
               (_T + "test_rule_of_two.py", _T + "test_crypto.py", "tests/unit/test_tool_guard.py",
                "tests/unit/test_credentials.py", "tests/unit/test_credential_migration.py", "tests/unit/test_hook_portability.py"), ("RA-18.AC1",)),
    Capability("RA-19", "Household coordination (assumed assignment acceptance; read-only)", "later",
               (_C + "coordination.py",), (_T + "test_coordination.py",), ("RA-19.AC1",)),
    Capability("RA-20", "Portable export, emergency access, accessibility", "later",
               (_C + "backup.py", _C + "privacy_history.py", _C + "ledger.py", _C + "checkpoint.py",
                _C + "checkpoint_cli.py", _C + "session_lock.py", _C + "recovery_registry.py",
                _C + "authority_transfer.py", _C + "interchange.py", _C + "recovery_quarantine.py",
                _C + "quarantine_review.py", _C + "recovery_access.py", _C + "quarantine_fs_windows.py",
                _C + "storage_adoption.py",
                "scripts/lib/local_paths.py", "scripts/lib/logger.py"),
               (_T + "test_user_surfaces.py", _T + "test_privacy_anchor_capacity.py", _T + "test_state_checkpoint.py",
                _T + "test_state_checkpoint_cli.py", _T + "test_restore_quarantine.py", _T + "test_installation_fence.py", _T + "test_checkpoint_restore_integrity.py",
                _T + "test_recovery_registry.py", "tests/unit/test_recovery_registry_writers.py",
                _T + "test_authority_retirement.py", _T + "test_spend_continuity_registry.py",
                _T + "test_legacy_writer_fence.py", _T + "test_recovery_quarantine.py",
                _T + "test_quarantine_review.py", _T + "test_recovery_access.py",
                _T + "test_quarantine_restore.py", _T + "test_recovery_quarantine_cli.py",
                _T + "test_quarantine_fs_windows.py", _T + "test_session_lock.py", _T + "test_storage_adoption.py",
                _T + "test_preservation_bounded.py", "tests/unit/test_pipeline_output_path.py",
                "tests/unit/test_structured_logger.py"), ("RA-20.AC1",)),
    Capability("RA-21", "Document ingestion and extraction (text and local OCR)", "pilot", (_C + "documents.py", _C + "ocr.py", _C + "field_confirmation.py"),
               (_T + "test_documents.py", _T + "test_ocr.py", _T + "test_field_confirmation.py", _T + "test_document_field_runtime.py"), ("RA-21.AC1",)),
    Capability("RA-22", "Feedback loop and precision tracking", "pilot", (_C + "views.py", _C + "migrations/0007_replay.sql"),
               (_T + "test_user_surfaces.py",), ("RA-22.AC1",)),
    Capability("RA-23", "Beliefs view, forget receipts and third-party data", "reduced", (_C + "ledger.py", _C + "commands.py", _C + "retention.py"),
               (_T + "test_ledger.py", _T + "test_user_surfaces.py", _T + "test_retention.py"), ("RA-23.AC1",)),
    Capability("RA-24", "Action policy, executor and activity view (pause flag, held types)", "reduced",
               (_C + "commands.py", "scripts/action_executor.py", "scripts/lib/action_policy.py",
                "scripts/lib/action_process.py", "scripts/lib/action_credentials.py", "scripts/actions/__init__.py",
                "scripts/lib/action_evidence.py", _C + "fact_access.py"),
               ("tests/unit/test_action_executor.py", "tests/unit/test_action_policy.py", "tests/unit/test_action_policy_freshness.py",
                "tests/unit/test_action_process.py", "tests/unit/test_action_credentials.py",
                "tests/unit/test_action_evidence_dispatch.py", "tests/unit/test_action_provider_preconditions.py"), ("RA-24.AC1",)),
    Capability("RA-25", "Goal instruction sheets and source-aware responsibilities (evaluated at Catch-up)", "later",
               (_C + "plans.py", _C + "responsibilities.py"), (_T + "test_plans.py", _T + "test_responsibilities.py"),
               ("RA-25.AC1",)),
)


# Named counterexamples are separate from live acceptance. Each suffix resolves to one executable fixture.
BOUNDARY_CASES: tuple[tuple[str, str], ...] = (
    ("RA-20.AC1.storage-adoption-process-death", "tests/catchup_core/test_storage_adoption.py::test_real_process_death_exposes_partial_state_and_requires_fresh_review"),
    ("RA-20.AC1.storage-adoption-descendants", "tests/catchup_core/test_storage_adoption.py::test_native_stored_acl_projection_effective_access_and_inheritance_preservation"),
    ("RA-20.AC1.file-root-wal", "tests/catchup_core/test_state_checkpoint.py::test_explicit_database_file_root_captures_wal_without_sibling_data"),
    ("RA-20.AC1.external-action-store", "tests/catchup_core/test_state_checkpoint_cli.py::test_external_action_store_cannot_be_omitted"),
    ("RA-20.AC1.new-dependency", "tests/catchup_core/test_state_checkpoint_cli.py::test_new_external_dependency_during_capture_blocks_publication"),
    ("RA-09.AC1.checkpoint-work-credentials", "tests/catchup_core/test_state_checkpoint_cli.py::test_personal_checkpoint_excludes_known_work_credential_artifacts"),
    ("RA-09.AC1.checkpoint-root-conflict", "tests/catchup_core/test_state_checkpoint_cli.py::test_profile_dependency_cannot_override_work_content_boundary"),
    ("RA-12.AC1.completion-parent-scope", "tests/catchup_core/test_open_item_completion.py::test_parent_goal_revocation_applies_to_child_completion_evidence"),
    ("RA-03.AC1.canonical-completion", "tests/catchup_core/test_open_item_completion.py::test_open_item_completion_is_shared_by_items_and_briefing_without_authored_writes"),
    ("RA-03.AC1.primary-accountability", "tests/catchup_core/test_open_item_completion.py::test_primary_user_owns_open_items_without_per_item_accountability_metadata"),
    ("RA-12.AC1.authored-owner-privacy", "tests/catchup_core/test_open_item_completion.py::test_authored_owner_privacy_is_not_lost_when_a_different_subject_is_explicit"),
    ("RA-12.AC1.legacy-completion-privacy", "tests/catchup_core/test_open_item_completion.py::test_unscoped_item_completion_retains_legacy_whole_file_privacy"),
    ("RA-11.AC1.primary-accountability-link", "tests/catchup_core/test_open_item_completion.py::test_linked_goal_uses_primary_accountability_without_rewriting_item_ownership"),
    ("RA-11.AC1.open-item-link", "tests/catchup_core/test_open_item_completion.py::test_linked_goal_and_plan_share_the_current_open_item_completion_version"),
    ("RA-11.AC1.link-ambiguity", "tests/catchup_core/test_open_item_completion.py::test_unverified_or_mismatched_open_item_links_cannot_establish_goal_progress"),
    ("RA-11.AC1.link-duplicates", "tests/catchup_core/test_open_item_completion.py::test_repeated_open_item_link_cannot_double_count_one_completion"),
    ("RA-11.AC1.link-query-generation", "tests/catchup_core/test_open_item_completion.py::test_changed_linked_item_during_query_blocks_a_stale_goal_answer"),
    ("RA-12.AC1.completion-transaction", "tests/catchup_core/test_open_item_completion.py::test_completion_checks_reviewed_permissions_inside_its_write_transaction"),
    ("RA-20.AC1.work-key-isolation", "tests/catchup_core/test_state_checkpoint_cli.py::test_work_checkpoint_has_separate_envelope_and_shared_vault_recovery_only"),
    ("RA-20.AC1.work-destination", "tests/catchup_core/test_state_checkpoint_cli.py::test_work_primary_destination_cannot_be_synced_or_inside_the_repository"),
    ("RA-20.AC1.work-privacy-gate", "tests/catchup_core/test_state_checkpoint_cli.py::test_work_checkpoint_cannot_bypass_privacy_gate_by_omitting_profile"),
    ("RA-20.AC1.key-generation", "tests/catchup_core/test_state_checkpoint_cli.py::test_key_rotation_during_checkpoint_requires_a_new_capture"),
    ("RA-20.AC1.recovery-payload", "tests/catchup_core/test_state_checkpoint_cli.py::test_recovery_cannot_report_success_for_a_damaged_payload"),
    ("RA-02.AC1.instant-cutoff", "tests/catchup_core/test_temporal_instants.py::test_historical_cutoff_accepts_equivalent_instants"),
    ("RA-02.AC1.subsecond-history", "tests/catchup_core/test_temporal_instants.py::test_subsecond_writes_keep_history_and_review_order"),
    ("RA-02.AC1.clock-rollback", "tests/catchup_core/test_temporal_instants.py::test_clock_offset_and_rollback_do_not_move_version_backwards"),
    ("RA-01.AC1.graph-empty-page", "tests/unit/test_calendar_handlers_coverage.py::test_graph_empty_intermediate_page_cannot_hide_later_events"),
    ("RA-01.AC1.graph-malformed-page", "tests/unit/test_calendar_handlers_coverage.py::test_graph_malformed_later_page_keeps_valid_positives"),
    ("RA-01.AC1.graph-final-page-cap", "tests/unit/test_calendar_handlers_coverage.py::test_graph_cap_within_final_page_is_truncated"),
    ("RA-01.AC1.google-malformed-page", "tests/unit/test_google_calendar_coverage.py::test_malformed_google_page_preserves_other_valid_calendar_data"),
    ("RA-10.AC1.release-runtime-cohort", "tests/catchup_core/test_release_artifact_coherence.py::test_approved_scorecard_from_a_different_runtime_cohort_cannot_enable_cutover"),
    ("RA-07.AC1.exchange-identity", "tests/catchup_core/test_exchange.py::test_response_identity_and_cohort_are_checked_separately_from_answer_text"),
    ("RA-10.AC1.observed-model", "tests/catchup_core/test_exchange.py::test_returned_shadow_output_records_whether_model_identity_was_reported"),
    ("RA-20.AC1.escrow-integrity", "tests/catchup_core/test_state_checkpoint.py::test_checkpoint_authentication_also_covers_recovery_escrow"),
    ("RA-20.AC1.passphrase-inspection", "tests/catchup_core/test_state_checkpoint_cli.py::test_passphrase_only_inspection_needs_no_host_keys_and_cannot_activate"),
    ("RA-20.AC1.unreadable-subtree", "tests/catchup_core/test_state_checkpoint.py::test_unreadable_directory_cannot_disappear_from_inventory"),
    ("RA-20.AC1.capture-database-generation", "tests/catchup_core/test_state_checkpoint.py::test_database_change_after_member_capture_blocks_publication"),
    ("RA-20.AC1.capture-process-death", "tests/catchup_core/test_state_checkpoint.py::test_killed_capture_leaves_no_published_artifact_and_next_start_reports_the_orphan"),
    ("RA-20.AC1.windows-registry-process-death", "tests/catchup_core/test_recovery_registry.py::test_windows_registry_process_death_preserves_pending_intent_and_excludes_capture"),
    ("RA-20.AC1.capture-fence", "tests/catchup_core/test_state_checkpoint_cli.py::test_checkpoint_holds_the_installation_fence_in_a_real_competing_process"),
    ("RA-20.AC1.restore-runtime-fence", "tests/catchup_core/test_restore_quarantine.py::test_restored_runtime_is_blocked_before_configuration_or_key_access"),
    ("RA-05.AC1.restore-queue-fence", "tests/catchup_core/test_restore_quarantine.py::test_restored_queue_cannot_redirect_to_live_database"),
    ("RA-02.AC1.historical-source-time", "tests/catchup_core/test_user_surfaces.py::test_historical_explanation_uses_the_selected_observation_time"),
    ("RA-18.AC1.key-loss-enrollment", "tests/catchup_core/test_runtime.py::test_existing_installation_never_reenrolls_missing_keys"),
    ("RA-08.AC1.calendar-discovery", "tests/unit/test_calendar_handlers_coverage.py::test_graph_calendar_discovery_pages_are_part_of_source_coverage"),
    ("RA-01.AC1.live-release-artifacts", "tests/catchup_core/test_release_artifact_coherence.py::test_real_runtime_rechecks_approved_artifacts_after_construction"),
    ("RA-01.AC1.release-withdrawal-publication", "tests/catchup_core/test_release_artifact_coherence.py::test_actual_publication_stops_when_release_evidence_is_withdrawn_at_commit"),
    ("RA-20.AC1.approved-rollback-bytes", "tests/catchup_core/test_release_artifact_coherence.py::test_rollback_verification_decodes_the_one_approved_read"),
    ("RA-01.AC1.kernel-cutover-fence", "tests/catchup_core/test_cutover_fence.py::test_direct_runtime_cannot_bypass_configured_false_with_environment_opt_in"),
    ("RA-11.AC1.weekly-source-values", "tests/catchup_core/test_weekly_goals.py::test_quantity_weekly_change_keeps_exact_units_dates_and_dependencies"),
    ("RA-11.AC1.weekly-correction-erasure", "tests/catchup_core/test_weekly_goals.py::test_correction_and_forgetting_recompute_weekly_input_versions"),
    ("RA-11.AC1.weekly-readonly", "tests/catchup_core/test_weekly_goals.py::test_weekly_command_and_mcp_share_the_read_only_goal_model"),
    ("RA-11.AC1.metric-next-action", "tests/catchup_core/test_goal_metrics.py::test_briefing_keeps_verified_metric_and_authored_action_with_exact_dependencies"),
    ("RA-01.AC1.active-account-inventory", "tests/catchup_core/test_coordinator.py::test_replaced_account_history_is_not_a_current_required_scope"),
    ("RA-08.AC1.account-volume-history", "tests/catchup_core/test_adapters.py::test_volume_history_is_scoped_to_account_and_filter"),
    ("RA-01.AC1.current-coverage-header", "tests/catchup_core/test_coordinator.py::test_default_coverage_header_uses_current_required_scopes"),
    ("RA-08.AC1.interrupted-positives", "tests/catchup_core/test_audit_completion.py::test_actual_interrupted_adapter_keeps_positives_and_last_complete_watermark"),
    ("RA-08.AC1.interrupted-cancellation", "tests/catchup_core/test_adapters.py::test_cancellation_before_iterator_failure_is_not_lost"),
    ("RA-02.AC1.schema-history", "tests/catchup_core/test_schema_history.py::test_changed_applied_migration_cannot_be_hidden_by_the_version_number"),
    ("RA-02.AC1.schema-journal-death", "tests/catchup_core/test_schema_history.py::test_process_death_keeps_schema_history_and_version_atomic"),
    ("RA-12.AC1.curated-record-scope", "tests/catchup_core/test_curated_permissions.py::test_runtime_and_command_keep_allowed_goals_without_unrelated_adult_consent"),
    ("RA-12.AC1.curated-private-bounds", "tests/catchup_core/test_curated_permissions.py::test_withheld_records_cannot_consume_bounds_or_inject_validation_gaps"),
    ("RA-12.AC1.curated-revocation-race", "tests/catchup_core/test_curated_permissions.py::test_permission_change_during_projection_refuses_the_entire_read"),
    ("RA-12.AC1.correct-operation", "test_view_consent_does_not_authorize_correction_or_its_side_effects"),
    ("RA-02.AC1.cancel-independent", "test_calendar_cancellation_preserves_independent_support"),
    ("RA-08.AC1.calendar-scope", "test_calendar_ids_and_cancellations_are_scoped_by_calendar"),
    ("RA-04.AC1.curated-resume", "test_curated_change_invalidates_a_resumed_draft"),
    ("RA-11.AC1.completion-version", "test_goal_completion_version_changes_invalidate_identical_resumed_prose"),
    ("RA-12.AC1.delivery-recheck", "test_publication_rechecks_permissions_after_the_last_stage_boundary"),
    ("RA-25.AC1.invalid-record", "test_malformed_responsibilities_are_visible_without_crashing"),
    ("RA-25.AC1.completion-binding", "test_explicit_completion_binds_the_record_and_survives_reopen"),
    ("RA-02.AC1.utf8-coordinates", "test_utf8_span_contract_and_mid_character_rejection"),
    ("RA-07.AC1.command-capability", "test_extractor_command_cannot_launch_a_tool_capable_cli"),
    ("RA-23.AC1.scoped-erasure", "test_legacy_calendar_erasure_survives_calendar_namespacing"),
    ("RA-05.AC1.legacy-quarantine", "tests/unit/test_action_quarantine.py::test_uncertain_history_reenters_active_queue_and_blocks_regeneration"),
    ("RA-05.AC1.quarantine-crash", "tests/unit/test_action_quarantine.py::test_process_death_rolls_back_quarantine_and_upgrade_marker"),
    ("RA-05.AC1.provider-version", "tests/unit/test_calendar_action_versions.py::test_changed_calendar_version_cannot_write"),
    ("RA-05.AC1.exact-review", "tests/unit/test_calendar_action_versions.py::test_exact_preview_includes_fields_hidden_by_the_old_abbreviated_view"),
    ("RA-12.AC1.corpus-revocation", "tests/catchup_core/test_corpus_boundaries.py::test_corpus_and_coverage_withhold_revoked_source_and_unknown_provenance"),
    ("RA-23.AC1.corpus-erasure", "tests/catchup_core/test_corpus_boundaries.py::test_corpus_label_erasure_is_journaled_and_cannot_reappear"),
    ("RA-20.AC1.corpus-restore", "tests/catchup_core/test_corpus_boundaries.py::test_old_snapshot_privacy_replay_erases_corpus_and_legacy_restore_is_blocked"),
    ("RA-10.AC1.output-evaluation", "tests/catchup_core/test_corpus_boundaries.py::test_actual_returned_briefing_is_scored_separately_from_labels"),
    ("RA-11.AC1.concurrent-writer", "tests/unit/test_goals_writer.py::test_independent_writer_refuses_live_lock_then_retry_preserves_both_changes"),
    ("RA-08.AC1.provider-cancellation", "tests/unit/test_google_calendar_coverage.py::test_actual_handler_cancellation_withdraws_only_its_calendar"),
    ("RA-18.AC1.credential-process-death", "tests/unit/test_credentials.py::test_process_death_at_head_publication_preserves_complete_generations"),
    ("RA-18.AC1.credential-migration-race", "tests/unit/test_credential_migration.py::test_client_persistence_failure_and_input_race_preserve_originals"),
    ("RA-18.AC1.credential-migration-death", "tests/unit/test_credential_migration.py::test_migration_process_death_retains_recoverable_generations"),
    ("RA-09.AC1.work-credential-scope", "tests/unit/test_credentials_providers.py::test_sharepoint_uses_only_work_record"),
    ("RA-25.AC1.source-checks", "tests/catchup_core/test_responsibilities.py::test_runtime_checks_only_visible_fresh_scopes_and_pending_signals"),
    ("RA-25.AC1.stop-correction", "tests/catchup_core/test_responsibilities.py::test_verified_stop_is_informational_and_correction_removes_the_quiet_claim"),
    ("RA-25.AC1.prepared-instructions", "tests/catchup_core/test_plans.py::test_instruction_sheet_is_retained_in_the_encrypted_catchup_artifact"),
    ("RA-10.AC1.logical-pauses", "tests/catchup_core/test_metrics.py::test_logical_catchup_combines_pause_work_and_wait_without_counting_replays_twice"),
    ("RA-10.AC1.missing-sequence", "tests/catchup_core/test_metrics.py::test_missing_middle_invocation_cannot_be_hidden_by_a_known_start_and_finish"),
    ("RA-10.AC1.lost-append", "tests/catchup_core/test_coordinator.py::test_health_detects_an_invocation_whose_entire_telemetry_append_was_lost"),
    ("RA-10.AC1.bounded-history", "tests/catchup_core/test_metrics.py::test_reading_recent_metrics_has_bounded_memory_for_a_large_torn_prefix"),
    ("RA-20.AC1.privacy-os-limit", "tests/catchup_core/test_privacy_anchor_capacity.py::test_large_privacy_history_fits_bounded_windows_os_anchor_and_replays_exactly"),
    ("RA-20.AC1.privacy-publication-death", "tests/catchup_core/test_privacy_anchor_capacity.py::test_process_death_before_or_after_os_head_keeps_an_authenticated_replay"),
    ("RA-20.AC1.privacy-competing-writers", "tests/catchup_core/test_privacy_anchor_capacity.py::test_competing_processes_cannot_reinterpret_the_same_generation"),
    ("RA-20.AC1.legacy-privacy-anchor", "tests/catchup_core/test_privacy_anchor_capacity.py::test_legacy_head_reads_without_mutation_then_migrates_with_exact_history"),
    ("RA-16.AC1.failed-runtime-resources", "tests/catchup_core/test_runtime_resources.py::test_actual_builder_closes_open_ledger_after_a_wiring_failure"),
    ("RA-16.AC1.interrupted-ledger-resources", "tests/catchup_core/test_runtime_resources.py::test_interrupted_inner_ledger_setup_closes_its_handle"),
    ("RA-18.AC1.portable-hook-processes", "tests/unit/test_hook_portability.py::test_generated_commands_start_real_interpreter_with_unicode_stdin_and_space_paths"),
    ("RA-18.AC1.windows-hook-protocol", "tests/unit/test_hook_portability.py::test_supported_windows_and_copilot_tool_spellings_keep_real_denials"),
    ("RA-24.AC1.current-dispatch-policy", "tests/unit/test_action_policy_freshness.py::test_worker_refuses_stale_dispatch_authority_before_consuming_it"),
    ("RA-24.AC1.child-policy-withdrawal", "tests/unit/test_action_policy_freshness.py::test_real_child_rechecks_policy_before_loading_any_provider"),
    ("RA-24.AC1.atomic-rate-limit", "tests/unit/test_action_policy_freshness.py::test_two_processes_cannot_both_claim_the_last_current_policy_rate_slot"),
    ("RA-14.AC1.typed-policy", "tests/catchup_core/test_domain_pack.py::test_pack_policy_fields_are_typed_instead_of_coerced"),
    ("RA-14.AC1.component-calendars", "tests/catchup_core/test_triage.py::test_composite_deadlines_use_their_own_domain_calendar_and_local_day"),
    ("RA-14.AC1.source-freshness", "tests/catchup_core/test_composite_pack.py::test_composite_preserves_strictest_source_freshness_and_all_versions"),
    ("RA-14.AC1.second-domain-loads", "tests/catchup_core/test_domain_pack_additional.py::test_given_the_vehicle_pack_when_loaded_then_it_is_strict_and_declares_what_it_lacks"),
    ("RA-14.AC1.second-domain-triage", "tests/catchup_core/test_domain_pack_additional.py::test_given_a_vehicle_registration_due_soon_when_triaged_then_the_core_alerts_without_vehicle_code"),
    ("RA-14.AC1.second-domain-composite", "tests/catchup_core/test_domain_pack_additional.py::test_given_the_vehicle_pack_composed_with_another_then_both_domains_stay_separate"),
    ("RA-14.AC1.core-neutrality", "tests/catchup_core/test_domain_pack_additional.py::test_given_the_core_modules_when_scanned_then_no_new_module_names_a_domain"),
    ("RA-14.AC1.deny-beats-allow", "tests/catchup_core/test_domain_pack_contracts.py::test_given_allow_and_deny_rules_when_resolved_then_deny_wins_and_unmatched_is_unknown"),
    ("RA-11.AC1.sprint-calibration-needs-tolerance", "tests/catchup_core/test_wiring.py::test_given_a_sprint_without_an_authored_tolerance_when_weekly_then_no_calibration_is_shown"),
    ("RA-11.AC1.tradeoff-proposal-only", "tests/catchup_core/test_wiring.py::test_given_authored_competing_goals_one_ahead_and_one_behind_when_weekly_then_a_proposal_only_line_appears"),
    ("RA-20.AC1.restore-single-handle", "tests/catchup_core/test_checkpoint_restore_integrity.py::test_pathname_replacement_keeps_original_handle_and_ignores_substituted_file"),
    ("RA-20.AC1.restore-frame-pinning", "tests/catchup_core/test_checkpoint_restore_integrity.py::test_in_place_rewrite_after_preflight_refuses_before_releasing_substituted_plaintext"),
    ("RA-20.AC1.restore-physical-verify", "tests/catchup_core/test_checkpoint_restore_integrity.py::test_physical_output_must_match_before_privacy_replay"),
    ("RA-20.AC1.restore-sqlite-integrity", "tests/catchup_core/test_checkpoint_restore_integrity.py::test_authenticated_sqlite_still_needs_physical_integrity_checks"),
    ("RA-20.AC1.restore-process-death", "tests/catchup_core/test_checkpoint_restore_integrity.py::test_killed_restore_process_leaves_only_inactive_marked_tree"),
    ("RA-20.AC1.restore-escrow-completeness", "tests/catchup_core/test_state_checkpoint_cli.py::test_escrow_and_restore_keys_cover_recovery_credentials_bundles_and_privacy_heads"),
    ("RA-20.AC1.restore-competing-writers", "tests/catchup_core/test_state_checkpoint_cli.py::test_quiescence_and_writer_fences_block_competing_writers_and_restored_trees"),
    ("RA-09.AC1.cloud-filter-reparse", "tests/catchup_core/test_work_cache.py::test_windows_cloud_filter_reparse_on_legacy_checkout_succeeds_while_root_reparse_blocks"),
    ("RA-20.AC1.preservation-gate", "tests/catchup_core/test_cutover_fence.py::test_incomplete_preservation_or_recovery_evidence_blocks_activation"),
    ("RA-18.AC1.authority-transfer-fence", "tests/catchup_core/test_cutover_fence.py::test_authority_transfer_requires_quiescence_or_revocation_and_checkpoint_and_fences_old_host"),
)
_BOUNDARY_TESTS = "tests/catchup_core/test_release_boundaries.py"


def static_status(root: Path, cap: Capability) -> tuple[bool, bool]:
    """(code present, tests present).  A capability with no modules has no code."""
    code = bool(cap.modules) and all((root / m).exists() for m in cap.modules)
    tests = bool(cap.tests) and all((root / t).exists() for t in cap.tests)
    return code, tests


# How each requirement closes under the 2026-10-31 completion plan (re-artha.md). A route, not a status:
# acceptance is owner evidence recorded in the spec log, never claimed by this register.
_LIVE = "recorded live run + owner acceptance"
CLOSURE_ROUTES: Mapping[str, str] = {
    **{f"RA-{n:02d}": _LIVE for n in range(1, 26)},
    "RA-05": _LIVE + " (first owner-approved preview)",
    "RA-06": _LIVE + " (tools/uat/ra_06)",
    "RA-09": _LIVE + " (tools/uat/ra_09)",
    "RA-10": _LIVE + " (clean run after the degraded-run cause is resolved)",
    "RA-11": "decision record: retained by the incumbent (Decision 3)",
    "RA-12": _LIVE + " (member consent granted in person, D1)",
    "RA-13": "decision record: waived (Decision 1)",
    "RA-15": _LIVE + " (tools/uat/ra_15)",
    "RA-19": _LIVE + " (tools/uat/ra_19), or deferred in writing (D7)",
    "RA-20": _LIVE + " (tools/uat/ra_20), or deferred in writing (D7)",
    "RA-21": "one live document run + owner acceptance (field-accuracy gate waived, Decision 1)",
    "RA-22": _LIVE + " (feedback recorded on a live Alert)",
    "RA-23": _LIVE + " (tools/uat/ra_23)",
    "RA-24": _LIVE + " (first owner-approved reminder created end to end, D6)",
    "RA-25": "decision record: retained by the incumbent (Decision 3)",
}


def render_register(root: Path) -> str:
    lines = [
        "# Capability register (generated — do not edit)", "",
        "Generated by `tools/gen_capability_register.py` from `scripts/catchup_core/capabilities.py`; CI fails on drift.",
        "`enabled`, `exercised` and `measured` are runtime facts shown by `catchup_core health`, not claims made here.",
        "`Closure route` is how each requirement closes under the 2026-10-31 completion plan; it is not an acceptance claim.", "",
        "| ID | Capability | Scope | Code | Tests | Acceptance | Closure route |", "|---|---|---|---|---|---|---|"]
    for cap in CAPABILITIES:
        code, tests = static_status(root, cap)
        lines.append(f"| {cap.id} | {cap.title} | {cap.scope} | {'present' if code else 'absent'} | "
                     f"{'present' if tests else 'absent'} | {', '.join(cap.acceptance)} | {CLOSURE_ROUTES[cap.id]} |")
    return "\n".join(lines) + "\n"


def manifest(root: Path, previous: Mapping[str, object] | None = None) -> dict[str, object]:
    """§12.12 artifact manifest: acceptance IDs mapped to modules and tests (drift-checked in CI).

    ``spec_sha256`` digests the local-only specification when it is present; a CI checkout does not
    have it, so the previous value is kept and never compared.
    """
    import hashlib  # noqa: PLC0415

    from catchup_core import CORE_CONTRACT_VERSION  # noqa: PLC0415
    spec = root / "specs" / "re-artha.md"
    digest = hashlib.sha256(spec.read_bytes()).hexdigest() if spec.is_file() else (previous or {}).get("spec_sha256", "")
    paths = sorted({path for capability in CAPABILITIES for path in (*capability.modules, *capability.tests)}
                   | {_BOUNDARY_TESTS}
                   | {fixture.split("::")[0] for _, fixture in BOUNDARY_CASES if "::" in fixture}
                   | {p.relative_to(root).as_posix() for p in (root / _C).glob("migrations/*.sql")}
                   | {p.relative_to(root).as_posix() for p in (root / _C).glob("prompts/*.md")})
    implementation = "\n".join(path + ":" + hashlib.sha256((root / path).read_bytes()).hexdigest()
                                for path in paths if (root / path).is_file())
    return {
        "spec": "specs/re-artha.md", "spec_version": "0.8.0", "spec_sha256": digest,
        "contract_version": CORE_CONTRACT_VERSION,
        "implementation_sha256": hashlib.sha256(implementation.encode()).hexdigest(),
        "boundary_cases": {case: {"fixture": fixture if "::" in fixture else _BOUNDARY_TESTS + "::" + fixture, "evidence_type": "synthetic"}
                           for case, fixture in BOUNDARY_CASES},
        "acceptance": {a: {"capability": c.id, "scope": c.scope, "modules": list(c.modules), "tests": list(c.tests)}
                       for c in CAPABILITIES for a in c.acceptance},
    }


def runtime_states(root: Path, config: Mapping[str, object], db: sqlite3.Connection | None,
                   release_ok: bool, *, extractor_configured: bool) -> list[dict[str, str]]:
    """Per capability: disabled | not configured | unknown | last succeeded | degraded | not implemented."""
    evidence: dict[str, str] = {}
    if db is not None:
        with suppress(sqlite3.Error):
            evidence = dict(db.execute("SELECT capability,state FROM capability_evidence ORDER BY seq"))
    domains = config.get("domains") or []
    out: list[dict[str, str]] = []
    for cap in CAPABILITIES:
        code, _tests = static_status(root, cap)
        if not code:
            state = "not implemented"
        elif cap.scope == "later":
            state = "disabled"
        elif not domains or (not extractor_configured and cap.id in ("RA-02", "RA-07")):
            state = "not configured"
        else:
            state = evidence.get(cap.id, "unknown")
        reviewed = config.get("reviewed_capabilities", [])
        reviewed = reviewed if isinstance(reviewed, list) else []
        out.append({"id": cap.id, "state": state,
                    "measured": "yes" if release_ok and cap.id in reviewed and state == "last succeeded" else "no"})
    return out


#: PRD feature-group disposition (re-artha 11.4): a release decision, not a claim that each FR is implemented.
FR_DISPOSITION: tuple[tuple[str, str, str, str], ...] = (
    ("FR-1 Communications; FR-8 Calendar", "pilot", "RA-01, RA-08, RA-13", "Connector and coverage substrate plus two cross-Domain cases"),
    ("FR-2 Immigration; FR-3 Finance; FR-4 Kids/School", "pilot (Kids/School only)", "RA-02, RA-03, RA-14", "Each other Domain needs its own pack, review and release gate"),
    ("FR-5 Travel; FR-6 Health; FR-7 Home; FR-9 Shopping; FR-10 Learning; FR-11 Relationships; FR-12 Digital", "incumbent, labelled by provenance", "RA-23", "No blanket 24-Domain migration; inferred relationship profiles retired (D-14)"),
    ("FR-13 Goals; FR-14 Work-Life Boundary", "pilot (minimum Goal observation)", "RA-11, RA-13", "Forecasts and Work aggregates are destination milestones"),
    ("FR-15 Artha OS Core incl. ambient intent", "coexist", "RA-04, RA-10", "Keep/change/retire decisions per legacy step"),
    ("FR-16 Insurance; FR-17 Vehicle; FR-18 Estate; FR-20 Readiness; FR-21 Logistics; FR-22 Tribe; FR-23 Capital", "incumbent", "-", "No new autonomous effects; expand only after gates"),
    ("FR-19 Work OS; FR-31; FR-32; FR-42", "separate Work authority", "-", "Stateless code reuse or approved aggregate only"),
    ("FR-25 Career Search", "separate personal authority", "RA-12", "Employer-derived items cross only by explicit minimized export"),
    ("FR-24 Home bridge; FR-28 Hermes", "post-commit projection", "RA-14", "Versioned allowlisted adapter; no authority"),
    ("FR-26 Channels; FR-27 Briefing archive", "channels retired; archive kept", "RA-04, RA-09", "CLI/VS Code sessions only; one logical Briefing ID"),
    ("FR-29 DataCopilot quality; FR-30 Agent SRE observability", "reuse measured checks", "RA-10, RA-18", "Redacted traces; placeholder gates replaced"),
)


def render_traceability(root: Path) -> str:
    """Requirement-to-evidence table: every RA row with its acceptance IDs, code and tests, then the PRD map."""
    lines = ["# Traceability register (generated \u2014 do not edit)", "",
             "Generated by `tools/gen_capability_register.py`; CI fails on drift. Statuses are runtime facts (`health`).", "",
             "## Requirements to evidence", "", "| Requirement | Scope | Acceptance | Code | Tests |", "|---|---|---|---|---|"]
    for cap in CAPABILITIES:
        code, tests = static_status(root, cap)
        lines.append(f"| {cap.id} {cap.title} | {cap.scope} | {', '.join(cap.acceptance)} | "
                     f"{'<br>'.join(cap.modules) or '-'}{'' if code or not cap.modules else ' (missing)'} | "
                     f"{'<br>'.join(cap.tests) or '-'}{'' if tests or not cap.tests else ' (missing)'} |")
    lines += ["", "## PRD feature groups", "", "| FR group | Disposition | Requirements | Note |", "|---|---|---|---|"]
    lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in FR_DISPOSITION]
    lines += ["", "## Named boundary regressions (synthetic evidence)", "",
              "These cases do not establish live acceptance or completion of the parent requirement.", "",
              "| Acceptance case | Executable fixture |", "|---|---|"]
    lines += [f"| {case} | {fixture if '::' in fixture else _BOUNDARY_TESTS + '::' + fixture} |" for case, fixture in BOUNDARY_CASES]
    return "\n".join(lines) + "\n"
