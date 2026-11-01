"""RA-05: an approved calendar diff must not overwrite a different provider version."""
from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from actions import calendar_modify as handler
from test_calendar_handler import _make_modify_proposal


def service(monkeypatch,etag='"v1"'):
    api=MagicMock()
    api.events.return_value.patch.return_value.headers={}
    api.events.return_value.get.return_value.execute.return_value={'id':'event123','etag':etag,'summary':'Before'}
    api.events.return_value.patch.return_value.execute.return_value={'id':'event123','etag':'"v2"','summary':'After'}
    monkeypatch.setattr(handler,'build_service',lambda *args:api)
    return api


def test_missing_reviewed_version_cannot_contact_provider(monkeypatch):
    def forbidden(*args):
        pytest.fail('unreviewed mutation contacted the provider')
    monkeypatch.setattr(handler,'build_service',forbidden)
    assert handler.execute(_make_modify_proposal()).status=='failure'


def test_changed_calendar_version_cannot_write(monkeypatch):
    api=service(monkeypatch,etag='"changed"')
    result=handler.execute(_make_modify_proposal(expected_etag='"v1"'))
    assert result.status=='failure' and 'review' in result.message.lower()
    api.events.return_value.patch.assert_not_called()


def test_patch_uses_reviewed_version_and_preserves_version_for_undo(monkeypatch):
    api=service(monkeypatch)
    result=handler.execute(_make_modify_proposal(expected_etag='"v1"'))
    assert result.status=='success'
    assert api.events.return_value.patch.return_value.headers['If-Match']=='"v1"'
    assert result.data['provider_version']=='"v2"'
    reverse=handler.build_reverse_proposal(_make_modify_proposal(),result.data)
    assert reverse.action_type=='calendar_modify' and reverse.parameters['expected_etag']=='"v2"'


@pytest.mark.parametrize('outcome',['exception','missing_receipt','wrong_object','missing_version'])
def test_uncertain_patch_never_claims_confirmed_failure(monkeypatch,outcome):
    api=service(monkeypatch)
    call=api.events.return_value.patch.return_value.execute
    if outcome=='exception':
        call.side_effect=ConnectionError('lost response')
    elif outcome=='wrong_object':
        call.return_value={'id':'different','etag':'"v2"'}
    elif outcome=='missing_version':
        call.return_value={'id':'event123'}
    else:
        call.return_value={}
    assert handler.execute(_make_modify_proposal(expected_etag='"v1"')).status=='unknown'


def test_provider_precondition_refusal_never_retries(monkeypatch):
    from googleapiclient.errors import HttpError
    from httplib2 import Response
    api=service(monkeypatch)
    api.events.return_value.patch.return_value.execute.side_effect=HttpError(Response({'status':'412'}),b'{}')
    assert handler.execute(_make_modify_proposal(expected_etag='"v1"')).status=='failure'
    assert api.events.return_value.patch.return_value.execute.call_count==1


def test_preview_exposes_the_provider_version_and_exact_diff(monkeypatch):
    service(monkeypatch)
    result=handler.dry_run(_make_modify_proposal())
    assert result.data['expected_etag']=='"v1"'
    assert result.data['diff']['summary']=={'before':'Before','after':'Updated Meeting'}


@pytest.fixture
def calendar_executor(tmp_path,monkeypatch):
    from action_executor import ActionExecutor
    (tmp_path/'config').mkdir()
    (tmp_path/'state').mkdir()
    (tmp_path/'config/actions.yaml').write_text('actions:\n  calendar_modify:\n    enabled: true\n    pii_check: false\n')
    executor=ActionExecutor(tmp_path)
    monkeypatch.setattr(executor,'_get_pubkey',lambda:None)
    monkeypatch.setattr(executor,'_get_privkey',lambda:None)
    executor.queue.propose(_make_modify_proposal())
    yield executor
    executor.close()


def test_review_binds_provider_version_and_invalidates_old_receipt(calendar_executor,monkeypatch):
    from action_queue import compute_payload_hash
    api=service(monkeypatch)
    proposal=calendar_executor.queue.list_pending()[0]
    old=compute_payload_hash(proposal)
    calendar_executor.queue.mark_preview_shown(proposal.id,'user:terminal',payload_hash=old)
    bound,preview=calendar_executor.prepare_review(proposal.id)
    assert preview['diff']['summary']['before']=='Before'
    assert bound.parameters['expected_etag']=='"v1"' and compute_payload_hash(bound)!=old
    row=calendar_executor.queue.get_raw(proposal.id)
    assert row['payload_hash']==compute_payload_hash(bound) and row['preview_payload_hash'] is None
    assert row['approved_payload_hash'] is None
    api.events.return_value.patch.assert_not_called()


def test_rejected_during_provider_read_cannot_be_restored_by_preview(calendar_executor,monkeypatch):
    proposal=calendar_executor.queue.list_pending()[0]
    def reject(_proposal):
        calendar_executor.queue.transition(proposal.id,'rejected',actor='user:terminal')
        return handler.ActionResult('success','preview',{'expected_etag':'"v1"','diff':{}},False,None)
    monkeypatch.setattr(calendar_executor,'_get_handler',lambda _:type('Handler',(),{'dry_run':staticmethod(reject)}))
    with pytest.raises(ValueError,match='changed'):
        calendar_executor.prepare_review(proposal.id)
    assert calendar_executor.queue.get_raw(proposal.id)['status']=='rejected'


def test_cli_renders_and_receipts_the_bound_provider_diff(calendar_executor,monkeypatch,capsys):
    from action_orchestrator import cmd_show
    from action_queue import ActionQueue,compute_payload_hash
    from contextlib import closing
    import action_orchestrator
    api=service(monkeypatch)
    proposal=calendar_executor.queue.list_pending()[0]
    monkeypatch.setattr(action_orchestrator,'_import_action_modules',lambda:(None,lambda _:calendar_executor,None))
    assert cmd_show(calendar_executor._artha_dir,proposal.id)==0
    text=capsys.readouterr().out
    assert 'Before' in text and 'Updated Meeting' in text
    with closing(ActionQueue(calendar_executor._artha_dir)) as queue:
        bound=queue.get(proposal.id)
        assert bound.parameters['expected_etag']=='"v1"'
        assert queue.get_raw(proposal.id)['preview_payload_hash']==compute_payload_hash(bound)
    api.events.return_value.patch.assert_not_called()


def test_exact_preview_includes_fields_hidden_by_the_old_abbreviated_view(capsys):
    from action_orchestrator import _print_expanded_preview
    params={f'field_{i}':f'value_{i}' for i in range(11)}
    params['last_field']='tail after 120 characters: '+('x'*150)+' must be visible'
    _print_expanded_preview(dataclasses.replace(_make_modify_proposal(),parameters=params))
    text=capsys.readouterr().out
    assert 'value_10' in text and 'must be visible' in text


def test_unbounded_preview_cannot_receive_a_review_receipt(monkeypatch,tmp_path):
    from action_orchestrator import cmd_show
    import action_orchestrator
    executor=MagicMock()
    executor.get_action.return_value=dataclasses.replace(_make_modify_proposal(),action_type='email_send',
        parameters={'body':'x'*200_000})
    monkeypatch.setattr(action_orchestrator,'_import_action_modules',lambda:(None,lambda _:executor,None))
    assert cmd_show(tmp_path,'proposal')==1
    executor.queue.mark_preview_shown.assert_not_called()


@pytest.mark.parametrize('snapshot',[None,{}, {'expected_etag':'bad\r\nheader'}])
def test_unverified_provider_snapshot_cannot_change_review_authority(calendar_executor,monkeypatch,snapshot):
    proposal=calendar_executor.queue.list_pending()[0]
    before=calendar_executor.queue.get_raw(proposal.id)['payload_hash']
    monkeypatch.setattr(calendar_executor,'_get_handler',lambda _:type('Handler',(),{
        'dry_run':staticmethod(lambda _:handler.ActionResult('success','',snapshot,False,None))}))
    with pytest.raises(ValueError,match='could not be verified'):
        calendar_executor.prepare_review(proposal.id)
    row=calendar_executor.queue.get_raw(proposal.id)
    assert row['payload_hash']==before and row['preview_shown_at'] is None


def test_nonexistent_or_already_reviewed_action_is_not_refreshed(calendar_executor):
    with pytest.raises(ValueError,match='pending'):
        calendar_executor.prepare_review('missing')
    proposal=calendar_executor.queue.list_pending()[0]
    calendar_executor.queue.transition(proposal.id,'rejected',actor='user:terminal')
    with pytest.raises(ValueError,match='pending'):
        calendar_executor.prepare_review(proposal.id)


def test_generic_review_does_not_invent_provider_context(calendar_executor):
    from actions.base import ActionProposal
    original=dataclasses.replace(_make_modify_proposal(),id='inert',action_type='instruction_sheet',parameters={'title':'Inspect'})
    calendar_executor.queue.propose(original)
    reviewed,context=calendar_executor.prepare_review(original.id)
    assert isinstance(reviewed,ActionProposal) and reviewed.parameters==original.parameters and context=={}


def test_changed_payload_cannot_attach_stale_provider_context(calendar_executor):
    from action_queue import compute_payload_hash
    proposal=calendar_executor.queue.list_pending()[0]
    with pytest.raises(ValueError,match='changed'):
        calendar_executor.queue.bind_review_context(proposal.id,{'expected_etag':'"v1"'},expected_payload_hash='obsolete')
    assert calendar_executor.queue.get_raw(proposal.id)['payload_hash']==compute_payload_hash(proposal)


def test_sensitive_review_context_never_falls_back_to_plaintext(calendar_executor,monkeypatch):
    from action_queue import ActionEncryptionError,compute_payload_hash
    import action_queue
    original=dataclasses.replace(_make_modify_proposal(event_id='sensitive-event'),id='sensitive',sensitivity='high')
    monkeypatch.setattr(action_queue,'_encrypt_field',lambda value,key:'sealed:'+value if key else
        (_ for _ in ()).throw(ActionEncryptionError('no key')))
    monkeypatch.setattr(action_queue,'_decrypt_field',lambda value,key:value.removeprefix('sealed:'))
    calendar_executor.queue.propose(original,pubkey='synthetic')
    digest=compute_payload_hash(original)
    with pytest.raises(ActionEncryptionError):
        calendar_executor.queue.bind_review_context(original.id,{**original.parameters,'expected_etag':'"v1"'},
            expected_payload_hash=digest,privkey='synthetic')
    assert calendar_executor.queue.get_raw(original.id)['payload_hash']==digest
    calendar_executor.queue.bind_review_context(original.id,{**original.parameters,'expected_etag':'"v1"'},
        expected_payload_hash=digest,privkey='synthetic',pubkey='synthetic')
    assert calendar_executor.queue.get_raw(original.id)['parameters'].startswith('sealed:')


def test_old_result_without_provider_version_cannot_generate_undo():
    with pytest.raises(ValueError,match='version'):
        handler.build_reverse_proposal(_make_modify_proposal(),{'event_id':'event123','_original_values':{'summary':'Before'}})


def test_provider_without_version_cannot_prepare_a_safe_diff(monkeypatch):
    api=service(monkeypatch,etag=None)
    assert handler.dry_run(_make_modify_proposal()).status=='failure'
    api.events.return_value.patch.assert_not_called()


def versioned_proposal(**params):
    return _make_modify_proposal(calendar_id='synthetic@example.test', expected_etag='"v1"',
        preconditions={'provider_versions': {'event123': '"v1"'}}, **params)


def test_registered_provider_verifier_reads_exact_calendar_and_event(monkeypatch):
    from lib.action_evidence import verify_provider
    api=service(monkeypatch)
    api.calendarList.return_value.get.return_value.execute.return_value={
        'id':'synthetic@example.test','accessRole':'writer'}
    verify_provider(versioned_proposal(),'actions.calendar_modify')
    api.calendarList.return_value.get.assert_called_once_with(calendarId='synthetic@example.test')
    api.events.return_value.get.assert_called_once_with(calendarId='synthetic@example.test',eventId='event123')
    api.events.return_value.patch.assert_not_called()


@pytest.mark.parametrize('mutation',['version','event','deleted','calendar','reader','missing_role'])
def test_provider_verifier_refuses_changed_or_unauthorized_target(monkeypatch,mutation):
    from catchup_core.actions import ClaimRefused
    from lib.action_evidence import verify_provider
    api=service(monkeypatch)
    calendar={'id':'synthetic@example.test','accessRole':'owner'}
    event=api.events.return_value.get.return_value.execute.return_value
    if mutation=='version': event['etag']='"v2"'
    elif mutation=='event': event['id']='other'
    elif mutation=='deleted': event['status']='cancelled'
    elif mutation=='calendar': calendar['id']='other@example.test'
    elif mutation=='reader': calendar['accessRole']='reader'
    else: calendar.pop('accessRole')
    api.calendarList.return_value.get.return_value.execute.return_value=calendar
    with pytest.raises(ClaimRefused):
        verify_provider(versioned_proposal(),'actions.calendar_modify')
    api.events.return_value.patch.assert_not_called()


@pytest.mark.parametrize('mutation',['key','etag','extra','url','module','action'])
def test_provider_scope_binding_refuses_before_read(monkeypatch,mutation):
    from catchup_core.actions import ClaimRefused
    from lib.action_evidence import verify_provider
    api=service(monkeypatch)
    item=versioned_proposal()
    module='actions.calendar_modify'
    if mutation=='key': item.parameters['preconditions']['provider_versions']={'other':'"v1"'}
    elif mutation=='etag': item.parameters['expected_etag']='"other"'
    elif mutation=='extra': item.parameters['preconditions']['provider_versions']['other']='"v1"'
    elif mutation=='url': item.parameters['calendar_id']='https://untrusted.example/calendar'
    elif mutation=='module': module='actions.email_send'
    else:
        item=dataclasses.replace(item,action_type='reminder_create')
        module='actions.reminder_create'
    with pytest.raises(ClaimRefused):
        verify_provider(item,module)
    api.events.assert_not_called()
    api.calendarList.assert_not_called()


def test_creation_without_existing_target_needs_no_provider_version(monkeypatch):
    from lib.action_evidence import verify_provider
    api=service(monkeypatch)
    item=dataclasses.replace(versioned_proposal(),action_type='reminder_create',parameters={'title':'Synthetic'})
    verify_provider(item,'actions.reminder_create')
    api.events.assert_not_called()


def test_calendar_primary_alias_requires_provider_primary_identity(monkeypatch):
    from catchup_core.actions import ClaimRefused
    from lib.action_evidence import verify_provider
    api=service(monkeypatch)
    item=versioned_proposal()
    item.parameters['calendar_id']='primary'
    calendar={'id':'synthetic@example.test','accessRole':'owner'}
    api.calendarList.return_value.get.return_value.execute.return_value=calendar
    with pytest.raises(ClaimRefused):
        verify_provider(item,'actions.calendar_modify')
    calendar['primary']=True
    verify_provider(item,'actions.calendar_modify')


def test_conditional_patch_refuses_wrong_object_even_with_matching_etag(monkeypatch):
    api=service(monkeypatch)
    api.events.return_value.get.return_value.execute.return_value['id']='other'
    assert handler.execute(versioned_proposal()).status=='failure'
    api.events.return_value.patch.assert_not_called()


def test_core_calendar_reverse_preserves_evidence_and_binds_resulting_provider_version():
    from lib.action_evidence import require_provider_scope
    pre = {'core_derived': True, 'fact_versions': {'fact-synthetic': '2026-10-24T12:00:00Z'},
           'subjects': ['kid'], 'permission_generation': 2, 'fresh_until': '2026-10-24T13:00:00Z',
           'provider_versions': {'event123': '"v1"'}}
    original = dataclasses.replace(_make_modify_proposal(expected_etag='"v1"', preconditions=pre),
                                   source_skill='catchup_core', sensitivity='high')
    result = {'event_id': 'event123', 'calendar_id': 'primary', 'provider_version': '"v2"',
              '_original_values': {'summary': 'Before'}}
    reverse = handler.build_reverse_proposal(original, result)
    bound = require_provider_scope(reverse)
    assert reverse.source_skill == 'catchup_core'
    assert reverse.sensitivity == 'high'
    assert bound['fact_versions'] == pre['fact_versions'] and bound['subjects'] == ['kid']
    assert bound['permission_generation'] == 2 and bound['fresh_until'] == pre['fresh_until']
    assert bound['provider_versions'] == {'event123': '"v2"'}
    assert pre['provider_versions'] == {'event123': '"v1"'}
    assert reverse.parameters['expected_etag'] == '"v2"'


def test_provider_only_reverse_preserves_its_declared_version_contract():
    from lib.action_evidence import require_provider_scope
    original = versioned_proposal()
    reverse = handler.build_reverse_proposal(original, {'event_id': 'event123',
        'calendar_id': 'synthetic@example.test', 'provider_version': '"v2"', '_original_values': {'summary': 'Before'}})
    assert require_provider_scope(reverse)['provider_versions'] == {'event123': '"v2"'}
