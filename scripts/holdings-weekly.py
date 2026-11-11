"""Opt-in Hermes script-only schedule. Copy into the selected profile's scripts folder."""
import os
from shutil import which
from pathlib import Path


if __name__ == "__main__":
    scripts = Path(__file__).resolve().parent
    owner = scripts.parent
    home = os.environ.get("HERMES_HOME")
    if (scripts.name != "scripts" or (home and Path(home).resolve() != owner)
            or not (owner / "plugins" / "holdings" / "plugin.yaml").is_file()):
        raise SystemExit("Open this schedule from the profile that owns the installed Holdings plugin.")
    executable = which("hermes")
    if executable is None:
        raise SystemExit("Add the Hermes CLI to the scheduled environment PATH before enabling this template.")
    args = [executable, "holdings-briefing", "--scheduled"]
    if Path(__file__).name == "holdings-weekly-sample.py":
        raise SystemExit("Fictional alerts and briefings are available only in the Holdings Desktop demo.")
    os.execve(executable, args, {**os.environ, "HERMES_HOME": str(owner)})
