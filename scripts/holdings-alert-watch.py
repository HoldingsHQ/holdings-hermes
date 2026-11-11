"""Optional script-only Hermes Cron runner for the Holdings alert watch."""
import os
from pathlib import Path
from shutil import which


if __name__ == "__main__":
    scripts = Path(__file__).resolve().parent
    owner = scripts.parent
    home = os.environ.get("HERMES_HOME")
    if (scripts.name != "scripts" or (home and Path(home).resolve() != owner)
            or not (owner / "plugins" / "holdings" / "plugin.yaml").is_file()):
        raise SystemExit("Open this schedule from the profile that owns Holdings.")
    executable = which("hermes")
    if executable is None:
        raise SystemExit("Add the Hermes CLI to the scheduled PATH before enabling this runner.")
    args = [executable, "holdings-alert-watch"]
    if Path(__file__).name == "holdings-alert-watch-sample.py":
        raise SystemExit("Fictional alerts and briefings are available only in the Holdings Desktop demo.")
    os.execve(executable, args, {**os.environ, "HERMES_HOME": str(owner)})
