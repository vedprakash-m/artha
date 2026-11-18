"""Real synthetic Git indexes/remotes; no host Git identity or credentials."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from catchup_core.renewal_store import RenewalError
from catchup_core.renewal_workspace import Workspace


def git(root, *args):
    return subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=' + str(root / 'synthetic-empty-hooks'),
                           '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                           '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    (root / 'scripts').mkdir()
    (root / 'scripts/example.py').write_bytes(b'VALUE = 1\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'synthetic baseline')
    return root


def test_AR06_preview_preserves_real_index_and_detects_source_change(repo):
    workspace = Workspace(repo)
    (repo / 'scripts/example.py').write_text('VALUE = 2\n')
    git(repo, 'add', '.')
    before = (repo / '.git/index').read_bytes()
    tree = workspace.tree()
    assert len(tree) == 40
    assert (repo / '.git/index').read_bytes() == before
    source = workspace.source()
    (repo / 'scripts/example.py').write_text('VALUE = 3\n')
    assert workspace.source() != source
    with pytest.raises(RenewalError):
        workspace.tree()


def test_AR06_untracked_or_nonmain_candidates_refuse(repo):
    workspace = Workspace(repo)
    (repo / 'scripts/unknown.py').write_text('VALUE = 2\n')
    with pytest.raises(RenewalError):
        workspace.tree()
    (repo / 'scripts/unknown.py').unlink()
    git(repo, 'checkout', '-b', 'synthetic-other')
    with pytest.raises(RenewalError):
        workspace.tree()


def test_AR18_source_excludes_local_cv_renderer_but_includes_untracked_code(repo):
    shutil.copyfile(Path(__file__).parents[2] / '.gitignore', repo / '.gitignore')
    (repo / 'scripts/render_example_cv.py').write_text('PRIVATE_CANARY = True\n')
    (repo / 'scripts/ordinary.py').write_text('VALUE = 2\n')

    names = {entry['path'] for entry in Workspace(repo).source()['files']}

    assert 'scripts/render_example_cv.py' not in names
    assert 'scripts/ordinary.py' in names


def test_AR03_protected_gitleaks_config_is_in_source_manifest(repo):
    (repo / '.gitleaks.toml').write_text('[allowlist]\npaths = [\".*synthetic.*\"]\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'add protected policy')

    names = {entry['path'] for entry in Workspace(repo).source()['files']}

    assert '.gitleaks.toml' in names


@pytest.mark.skipif(os.name == 'nt', reason='NTFS disables POSIX S_IXUSR bit / core.filemode defaults to false')
def test_AR06_source_mode_matches_git_index(repo):
    git(repo, 'config', 'core.filemode', 'true')
    git(repo, 'update-index', '--chmod=+x', 'scripts/example.py')
    (repo / 'scripts/example.py').chmod(0o755)
    git(repo, 'commit', '-m', 'executable mode')
    entry = next(file for file in Workspace(repo).source()['files'] if file['path'] == 'scripts/example.py')
    assert entry['mode'] == '100755'

    (repo / 'scripts/example.py').chmod(0o644)
    if os.name != 'nt':
        with pytest.raises(RenewalError, match='source executable mode'):
            Workspace(repo).source()


def test_AR06_host_filemode_false_uses_git_index_mode(repo):
    git(repo, 'config', 'core.filemode', 'false')
    (repo / 'scripts/example.py').chmod(0o755)

    entry = next(file for file in Workspace(repo).source()['files'] if file['path'] == 'scripts/example.py')

    assert entry['mode'] == '100644'


def test_AR06_clean_filter_allows_checked_in_lf_for_crlf_worktree(repo):
    (repo / '.gitattributes').write_bytes(b'*.py text eol=lf\n')
    git(repo, 'add', '.gitattributes')
    git(repo, 'commit', '-m', 'add source line-ending policy')
    (repo / 'scripts/example.py').write_bytes(b'VALUE = 1\r\n')
    git(repo, 'add', 'scripts/example.py')
    workspace = Workspace(repo)
    source = workspace.source()
    tree = git(repo, 'rev-parse', 'HEAD^{tree}')

    workspace.assert_tree(source, tree)


def test_AR13_linked_sources_refuse(repo):
    target = repo / 'outside.py'
    target.write_text('PRIVATE_CANARY')
    (repo / 'scripts/example.py').unlink()
    (repo / 'scripts/example.py').symlink_to(target)
    with pytest.raises(RenewalError):
        Workspace(repo).source()


def test_AR06_clean_main_remote_identity_and_tip(repo, tmp_path):
    workspace = Workspace(repo)
    assert len(workspace.clean_commit()) == 40
    git(repo, 'remote', 'add', 'origin', 'https://github.com/example/project.git')
    workspace.remote('example/project')
    with pytest.raises(RenewalError):
        workspace.remote('other/project')
    git(repo, 'remote', 'set-url', 'origin', str(tmp_path / 'missing'))
    with pytest.raises(RenewalError):
        workspace.remote_tip()
    (repo / 'scripts/example.py').write_text('changed')
    with pytest.raises(RenewalError):
        workspace.clean_commit()


def test_AR06_context_and_ignored_extra_inputs_are_pinned(repo, monkeypatch):
    from catchup_core.renewal_workspace import context
    first = context()
    monkeypatch.setenv('ARTHA_CUTOVER', '0')
    assert context() != first
    git(repo, 'config', 'core.excludesFile', str(repo / '.git/info/exclude'))
    (repo / '.git/info').mkdir(parents=True, exist_ok=True)
    (repo / '.git/info/exclude').write_text('private.input\n')
    (repo / 'private.input').write_bytes(b'SYNTHETIC_PRIVATE_CANARY')
    source = Workspace(repo).source(('private.input',))
    assert any(file['path'] == 'private.input' for file in source['files'])


@pytest.mark.parametrize('result', [(-1, b''), (0, b'\xff'), (0, b'x' * (1024 * 1024 + 1))])
def test_AR13_git_output_error_size_and_encoding_are_bounded(repo, monkeypatch, result):
    from types import SimpleNamespace
    import catchup_core.renewal_workspace as module
    monkeypatch.setattr(module.subprocess, 'run', lambda *_a, **_kw: SimpleNamespace(returncode=result[0], stdout=result[1]))
    with pytest.raises(RenewalError):
        Workspace(repo).git('status')


@pytest.mark.parametrize('error', [OSError('PRIVATE_CANARY'), subprocess.TimeoutExpired('git', 120)])
def test_AR13_git_failures_never_disclose_provider_error(repo, monkeypatch, error):
    import catchup_core.renewal_workspace as module
    def fail(*_a, **_kw):
        raise error
    monkeypatch.setattr(module.subprocess, 'run', fail)
    with pytest.raises(RenewalError, match='^Git delivery operation unavailable$'):
        Workspace(repo).git('status')


def test_AR13_source_types_limits_and_path_traversal(repo):
    from catchup_core.renewal_workspace import source_file
    with pytest.raises(RenewalError):
        source_file(repo / 'scripts', 'scripts')
    with pytest.raises(RenewalError):
        Workspace(repo).source(('../PRIVATE_CANARY',))


def test_AR06_real_remote_tip(repo, tmp_path):
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '--bare', '-b', 'main', str(remote))
    git(repo, 'remote', 'add', 'origin', str(remote))
    git(repo, 'push', 'origin', 'main')
    assert Workspace(repo).remote_tip() == Workspace(repo).clean_commit()


def test_AR07_bad_remote_listing_refuses(repo, monkeypatch):
    monkeypatch.setattr(Workspace, 'git', lambda *_: 'invalid')
    with pytest.raises(RenewalError):
        Workspace(repo).remote_tip()


@pytest.mark.slow
def test_AR06_git_filter_cannot_substitute_ci_source_bytes(repo):
    workspace = Workspace(repo)
    source = workspace.source()
    workspace.assert_tree(source, workspace.tree())
    source['files'][0]['sha256'] = 'f' * 64
    with pytest.raises(RenewalError, match='committed source'):
        workspace.assert_tree(source, workspace.tree())


def test_AR06_index_race_does_not_return_a_validated_tree(repo, monkeypatch):
    workspace = Workspace(repo)
    original = workspace.git
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == 'write-tree':
            with (repo / '.git/index').open('ab') as stream:
                stream.write(b'synthetic concurrent writer')
        return result
    monkeypatch.setattr(workspace, 'git', changed)
    with pytest.raises(RenewalError, match='index changed'):
        workspace.tree()


def test_AR06_absolute_index_and_wrong_checkout_are_checked(repo, monkeypatch):
    workspace = Workspace(repo)
    original = workspace.git
    def absolute(*args, **kwargs):
        if args[:2] == ('rev-parse', '--git-path'):
            return str(repo / '.git/index')
        return original(*args, **kwargs)
    monkeypatch.setattr(workspace, 'git', absolute)
    assert workspace.tree()
    monkeypatch.setattr(workspace, 'git', lambda *_a, **_kw: '/other/check-out')
    with pytest.raises(RenewalError):
        workspace.tree()


@pytest.mark.parametrize('metadata', [b'bad', b'object blob 67108865\n', b''])
def test_AR13_blob_size_count_and_metadata_fail_closed(repo, monkeypatch, metadata):
    workspace = Workspace(repo)
    source, tree = workspace.source(), workspace.tree()
    monkeypatch.setattr(workspace, '_batch', lambda *_: metadata)
    with pytest.raises(RenewalError):
        workspace.assert_tree(source, tree)


def test_AR06_blob_mode_and_surplus_output_refuse(repo, monkeypatch):
    workspace = Workspace(repo)
    source, tree = workspace.source(), workspace.tree()
    source['files'][0]['mode'] = '100755'
    with pytest.raises(RenewalError):
        workspace.assert_tree(source, tree)
    source['files'][0]['mode'] = '100644'
    original = workspace._batch
    monkeypatch.setattr(workspace, '_batch', lambda option, query: original(option, query) +
                        (b'surplus' if option == '--batch' else b''))
    with pytest.raises(RenewalError):
        workspace.assert_tree(source, tree)


def test_AR13_batch_failure_and_missing_dependency_version_are_bounded(repo, monkeypatch):
    from types import SimpleNamespace
    from catchup_core import renewal_workspace as module
    def failed(*_a, **_kw):
        raise OSError('PRIVATE_CANARY')
    with pytest.raises(RenewalError, match='^committed source inspection unavailable$'):
        Workspace(repo, runner=failed)._batch('--batch', b'')
    with pytest.raises(RenewalError):
        Workspace(repo, runner=lambda *_a, **_kw: SimpleNamespace(returncode=1, stdout=b''))._batch('--batch', b'')
    def missing(_):
        raise module.importlib.metadata.PackageNotFoundError
    monkeypatch.setattr(module.importlib.metadata, 'version', missing)
    assert len(module.context(repo)) == 64


def test_AR06_nonmain_clean_commit_is_not_deliverable(repo):
    git(repo, 'checkout', '-b', 'synthetic-other')
    with pytest.raises(RenewalError):
        Workspace(repo).clean_commit()


def test_AR06_source_descriptor_and_postread_identity_races_refuse(repo, monkeypatch):
    from types import SimpleNamespace
    from catchup_core import renewal_workspace as module
    path = repo / 'scripts/example.py'
    original = module.os.fstat
    def substituted(fd):
        value = original(fd)
        return SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino + 1)
    if os.name != 'nt':  # Windows reads through a verified directory handle, not a descriptor fstat.
        monkeypatch.setattr(module.os, 'fstat', substituted)
        with pytest.raises(RenewalError, match='identity changed'):
            module.source_file(path, 'scripts/example.py')
        monkeypatch.setattr(module.os, 'fstat', original)
    original_stat = module.Path.lstat
    calls = [0]
    def changed(self, **kwargs):
        value = original_stat(self, **kwargs)
        if self == path:
            calls[0] += 1
            if calls[0] == 2:
                values = {name: getattr(value, name) for name in ('st_mode', 'st_nlink', 'st_dev', 'st_ino',
                    'st_size', 'st_mtime_ns', 'st_ctime_ns')}
                values['st_ctime_ns'] += 1
                return SimpleNamespace(**values)
        return value
    monkeypatch.setattr(module.Path, 'lstat', changed)
    with pytest.raises(RenewalError, match='changed during inspection'):
        module.source_file(path, 'scripts/example.py')


def test_AR13_source_byte_budget_precedes_content_read(repo, monkeypatch):
    from catchup_core import renewal_workspace as module
    monkeypatch.setattr(module, '_MAX_SOURCE', 1)
    monkeypatch.setattr(module, 'source_file', lambda *_: pytest.fail('oversized source content read'))
    with pytest.raises(RenewalError, match='byte bound'):
        Workspace(repo).source()


def test_AR19_windows_source_reads_route_through_protected_handle(repo, monkeypatch):
    from contextlib import contextmanager
    from catchup_core import renewal_workspace as module
    class Directory:
        @classmethod
        @contextmanager
        def open(cls, path, **_):
            yield cls(path)
        def __init__(self, root):
            self.root = root
        def open_file(self, name, **_):
            return (self.root / name).open('rb')
    monkeypatch.setattr(module.sys, 'platform', 'win32')
    monkeypatch.setattr(module, 'WindowsQuarantineDirectory', Directory)
    assert module.source_file(repo / 'scripts/example.py', 'scripts/example.py').sha256


def test_AR06_toolchain_race_is_not_cached_as_approved(repo, monkeypatch):
    from catchup_core import renewal_workspace as module
    path = repo / 'scripts/example.py'
    before = module._revision(path)
    monkeypatch.setattr(module, '_revision', lambda _: (0, 0, 0, 0, 0))
    with pytest.raises(RenewalError):
        module._tool_digest(str(path), before)
