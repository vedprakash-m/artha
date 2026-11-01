#!/usr/bin/env python3
"""Install portable Artha hook commands without changing permissions or domain data."""
from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

MANAGED = {
    'PreToolUse': [('tool_guard.py', ())],
    'PostToolUse': [('vault_hook.py', ('stray-check',))],
    'SessionStart': [('stale_banner.py', ()), ('vault_hook.py', ('session-start',))],
    'Stop': [('session_stop.py', ())],
}


def command(script: str, args: tuple[str, ...]) -> dict[str, str | int]:
    tail = ' '.join(shlex.quote(arg) for arg in args)
    unix = ('p="$HOME/.artha-venvs/.venv/bin/python"; [ -x "$p" ] || p=python3; '
            f'"$p" -X utf8 "${{CLAUDE_PROJECT_DIR:-.}}/scripts/{script}" {tail}').rstrip()
    windows = (
        "$windowsHost=[System.Environment]::OSVersion.Platform -eq 'Win32NT'; "
        "$p=if ($windowsHost) { Join-Path $HOME '.artha-venvs/.venv-win/Scripts/python.exe' } "
        "else { Join-Path $HOME '.artha-venvs/.venv/bin/python' }; "
        "if (-not (Test-Path -LiteralPath $p)) { "
        "$names=if ($windowsHost) { 'python','py' } else { 'python3','python','py' }; "
        "$p=(Get-Command $names -CommandType Application -ErrorAction SilentlyContinue | "
        "Select-Object -First 1).Source }; "
        "if (-not $p) { [Console]::Error.WriteLine('Artha hooks require Python; run setup.ps1.'); exit 1 }; "
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "$j=[Console]::In.ReadToEnd(); "
        f"$j | & $p -X utf8 'scripts/{script}' {tail}; exit $LASTEXITCODE"
    ).replace('  ;', ';')
    # Copilot uses powershell; the earlier VS Code Local harness uses windows.
    return {'type': 'command', 'command': unix, 'powershell': windows, 'windows': windows,
            'cwd': '.', 'timeout': 30}


def migrate(existing: dict) -> dict:
    updated = json.loads(json.dumps(existing))
    hooks = updated.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('Existing hooks must be a mapping; settings were not changed.')
    for event, entries in MANAGED.items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError('Existing hook event must be a list; settings were not changed.')
        for script, args in entries:
            found = False
            for group in groups:
                for index, hook in enumerate(group.get('hooks', [])):
                    old = hook.get('command', '')
                    if script in old and all(arg in old for arg in args):
                        group['hooks'][index] = command(script, args)
                        if event == 'PreToolUse':
                            group['matcher'] = '.*'
                        found = True
            if not found:
                groups.append({'hooks': [command(script, args)], **(
                    {'matcher': '.*'}
                    if event == 'PreToolUse' else {'matcher': 'Bash|PowerShell|bash|powershell|run_in_terminal'} if event == 'PostToolUse' else {})})
    return updated


def install(root: Path) -> bool:
    path = root / '.claude/settings.json'
    previous = path.read_bytes() if path.exists() else None
    existing = json.loads(previous) if previous is not None else {}
    if not isinstance(existing, dict):
        raise ValueError('Existing settings must be a mapping; settings were not changed.')
    updated = (json.dumps(migrate(existing), indent=2) + '\n').encode()
    if previous == updated:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name('settings.pre-portable-hooks.json')
    if previous is not None and not backup.exists():
        backup.write_bytes(previous)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_bytes(updated)
    temporary.replace(path)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    options = parser.parse_args()
    print('Artha hooks updated; reload the VS Code window.' if install(options.root) else 'Artha hooks already current.')
