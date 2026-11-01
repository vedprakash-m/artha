"""Synthetic hook configuration/process checks; no domain content, provider or host credentials."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import configure_agent_hooks as hooks
import tool_guard


def test_migration_preserves_custom_hooks_permissions_and_only_replaces_managed_commands(tmp_path):
    old = {'permissions': {'allow': ['Bash(custom)']}, 'other': True, 'hooks': {
        'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command',
            'command': '/old/mac/python "/old/checkout/scripts/tool_guard.py"'},
            {'type': 'command', 'command': 'custom-validation'}]}]}}
    target = tmp_path / '.claude/settings.json'
    target.parent.mkdir()
    target.write_text(json.dumps(old))
    before = target.read_bytes()
    assert hooks.install(tmp_path)
    current = json.loads(target.read_bytes())
    assert current['permissions'] == old['permissions'] and current['other'] is True
    commands = current['hooks']['PreToolUse'][0]['hooks']
    assert commands[1]['command'] == 'custom-validation'
    assert '/old/' not in commands[0]['command'] and commands[0]['powershell'] == commands[0]['windows']
    assert target.with_name('settings.pre-portable-hooks.json').read_bytes() == before
    assert not hooks.install(tmp_path)
    assert not (tmp_path / 'state').exists()


@pytest.mark.parametrize('camel', [True, False])
def test_copilot_and_compatible_work_tool_calls_are_allowed(camel):
    payload = ({'toolName': 'powershell', 'toolArgs': json.dumps({'command': 'python scripts/work_reader.py --command work'})}
               if camel else {'tool_name': 'run_in_terminal', 'tool_input': {
                   'command': 'python scripts/work_reader.py --command work'}})
    assert tool_guard.decide(payload, domains=('finance', 'health')) is None


@pytest.mark.parametrize('payload', [
    {'toolName': 'powershell', 'toolArgs': {'command': 'Get-Content .\\state\\finance.md'}},
    {'toolName': 'read_file', 'toolArgs': {'filePath': 'C:\\Artha\\state\\health.md'}},
    {'tool_name': 'PowerShell', 'tool_input': {'command': '& python .\\scripts\\vault.py decrypt'}},
    {'toolName': 'bash', 'toolArgs': json.dumps({'command': 'codex --dangerously-bypass-approvals-and-sandbox exec x'})},
])
def test_supported_windows_and_copilot_tool_spellings_keep_real_denials(payload):
    assert tool_guard.decide(payload, domains=('finance', 'health'))


@pytest.mark.parametrize('shell', ['sh', 'pwsh'])
def test_generated_commands_start_real_interpreter_with_unicode_stdin_and_space_paths(tmp_path, shell):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f'{shell} is not installed on this test host')
    root = tmp_path / 'checkout with spaces'
    (root / 'scripts').mkdir(parents=True)
    (root / 'scripts/tool_guard.py').write_text(
        'import json,sys; print(json.dumps({"prefix":sys.prefix,"payload":json.load(sys.stdin)}))')
    home = tmp_path / 'home with spaces'
    candidate = home / '.artha-venvs' / (
        '.venv-win/Scripts/python.exe' if sys.platform == 'win32' and shell == 'pwsh' else '.venv/bin/python')
    venv = home / '.artha-venvs' / ('.venv-win' if sys.platform == 'win32' else '.venv')
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)],
                   check=True, capture_output=True, text=True, timeout=60)
    if sys.platform == 'win32' and shell == 'sh':
        candidate.parent.mkdir(parents=True)
        interpreter = (venv / 'Scripts/python.exe').as_posix()
        candidate.write_text(f'#!/bin/sh\nexec {shlex.quote(interpreter)} "$@"\n', newline='\n')
        candidate.chmod(0o755)
    assert candidate.is_file()
    config = hooks.command('tool_guard.py', ())
    command = config['powershell'] if shell == 'pwsh' else config['command']
    if shell == 'pwsh':
        # HOME is a read-only PowerShell automatic variable; the process receives a synthetic user directory.
        command = '$arthaTestHome=' + "'" + str(home).replace("'", "''") + "'; " + command.replace('$HOME', '$arthaTestHome')
    argv = [executable, '-NoProfile', '-NonInteractive', '-Command', command] if shell == 'pwsh' else [executable, '-c', command]
    payload = {'toolName': 'read_file', 'toolArgs': {'filePath': 'synthetic-例.txt'}}
    done = subprocess.run(argv, cwd=root, input=json.dumps(payload, ensure_ascii=False), text=True, encoding='utf-8',
        capture_output=True, timeout=15, env={
            **os.environ, 'HOME': str(home), 'CLAUDE_PROJECT_DIR': str(root), 'PYTHONUTF8': '0'})
    assert done.returncode == 0, done.stderr
    output = json.loads(done.stdout)
    assert output['prefix'] == str(venv)
    assert output['payload'] == payload


def test_camel_case_denial_uses_copilot_json_and_normal_exit(capsys):
    import io
    payload = {'toolName': 'powershell', 'toolArgs': {'command': 'python scripts/vault.py decrypt'}}
    assert tool_guard.main(io.StringIO(json.dumps(payload))) == 0
    output = capsys.readouterr()
    answer = json.loads(output.out)
    assert answer['permissionDecision'] == 'deny' and 'vault.py read' in answer['permissionDecisionReason']
    assert 'denied' in output.err


def test_quoted_windows_interpreter_does_not_hide_a_decrypt_request():
    payload = {'toolName': 'powershell', 'toolArgs': {
        'command': '& "C:\\Program Files\\Python\\python.exe" ".\\scripts\\vault.py" decrypt'}}
    assert tool_guard.decide(payload, domains=('finance',))


def test_invalid_settings_are_left_byte_for_byte_unchanged(tmp_path):
    path = tmp_path / '.claude/settings.json'
    path.parent.mkdir()
    for content in ('[]', '{"hooks": []}', '{"hooks": {"PreToolUse": {}}}', '['):
        path.write_text(content)
        with pytest.raises(ValueError):
            hooks.install(tmp_path)
        assert path.read_text() == content
        assert not path.with_name('settings.pre-portable-hooks.json').exists()


def test_installer_creates_only_settings_and_never_state(tmp_path):
    assert hooks.install(tmp_path)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*') if p.is_file()) == ['.claude/settings.json']
    assert not hooks.install(tmp_path)


def test_malformed_copilot_arguments_do_not_crash_the_hook():
    assert tool_guard.decide({'toolName': 'read_file', 'toolArgs': '{invalid'}) is None
    assert tool_guard.decide({'toolName': 'read_file', 'toolArgs': '[]'}) is None
