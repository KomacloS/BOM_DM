import os
import subprocess
from pathlib import Path


def test_setup_sh_uses_venv_python(tmp_path):
    work = tmp_path
    scripts = work / "scripts"
    scripts.mkdir()
    script_src = Path("scripts/setup.sh")
    script_dest = scripts / "setup.sh"
    script_dest.write_bytes(script_src.read_bytes())

    vpy = work / ".venv" / "bin" / "python"
    vpy.parent.mkdir(parents=True)
    record = work / "called"
    vpy.write_text(f"#!/bin/sh\necho $0 \"$@\" >> {record}\n")
    vpy.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = env.get("PATH", "")
    subprocess.run(["bash", str(script_dest)], cwd=work, env=env, check=True)

    lines = record.read_text().splitlines()
    assert lines
    assert all(line.startswith(str(vpy)) for line in lines)

