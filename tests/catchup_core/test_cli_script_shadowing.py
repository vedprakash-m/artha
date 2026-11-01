"""CS-01: launching the CLI as a script must not let catchup_core/actions.py shadow the actions package."""
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def test_script_launch_does_not_shadow_actions_package():
    cli = SCRIPTS / "catchup_core" / "cli.py"
    code = (
        "import runpy, sys\n"
        f"sys.path[:0] = [{str(cli.parent)!r}, {str(SCRIPTS)!r}]\n"       # script dir, then PYTHONPATH
        f"runpy.run_path({str(cli)!r}, run_name='not_main')\n"
        "import actions.base\n"
        "print(actions.base.__file__)\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr[-600:]
    assert Path(done.stdout.strip()).parent == SCRIPTS / "actions"
