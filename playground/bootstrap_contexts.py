"""Build the local Git source used by the playground demo.

The demo examples may be copied into a Docker build or remain untracked in a
developer checkout. This ignored repository gives the context reader the same
exact-commit contract it uses in production without changing the user's repo.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "playground" / "examples"
TARGET = ROOT / ".runtime" / "playground-contexts"


def run(*args: str, cwd: Path = TARGET) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    if not (TARGET / ".git").exists():
        run("init", "--quiet", "--initial-branch=main", str(TARGET), cwd=ROOT)
        run("config", "user.email", "playground@hyperset.local")
        run("config", "user.name", "Hyperset Playground")

    destination = TARGET / "examples"
    destination.mkdir(exist_ok=True)
    for example in sorted(EXAMPLES.iterdir()):
        if example.is_dir():
            shutil.copytree(example, destination / example.name, dirs_exist_ok=True)

    subprocess.run(["git", "add", "-A"], cwd=TARGET, check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=TARGET).returncode
    if changed:
        run("commit", "--quiet", "-m", "sync playground context examples")
    print(f"playground context repo: {TARGET}")
    print(f"playground context commit: {run('rev-parse', 'HEAD')}")


if __name__ == "__main__":
    main()
