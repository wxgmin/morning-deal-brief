"""Build the standalone Windows download from a clean virtual environment."""

import platform
import subprocess
import sys
from pathlib import Path


def main():
    if sys.platform != "win32" or platform.machine() != "AMD64":
        raise SystemExit("Build with x64 Python on Windows.")

    project = Path(__file__).resolve().parent.parent
    dist = project / "dist"
    dist.mkdir(exist_ok=True)
    notices = dist / "ThirdPartyNotices.txt"
    subprocess.run([
        sys.executable, "-m", "piplicenses", "--with-system", "--format=plain-vertical",
        "--with-license-file", "--with-notice-file", "--no-license-path", "--output-file", str(notices),
    ], cwd=project, check=True)
    with notices.open("a", encoding="utf-8") as out:
        out.write("\nPython runtime\n")
        out.write((Path(sys.base_prefix) / "LICENSE.txt").read_text(encoding="utf-8"))

    resources = {
        "config/coverage.yaml": "config",
        "demo": "demo",
        "static": "static",
        "templates": "templates",
        "src/knowledge/signal_rules.json": "src/knowledge",
        "LICENSE": ".",
        "dist/ThirdPartyNotices.txt": ".",
    }
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noupx",
        "--name", "MorningDealBrief", "--specpath", "build", "--workpath", "build", "--distpath", "dist",
        "--collect-data", "tzdata", "--copy-metadata", "keyring", "--hidden-import", "keyring.backends.Windows",
    ]
    for source, destination in resources.items():
        command.extend(("--add-data", f"{project / source}:{destination}"))
    subprocess.run([*command, str(project / "desktop.py")], cwd=project, check=True)


if __name__ == "__main__":
    main()
