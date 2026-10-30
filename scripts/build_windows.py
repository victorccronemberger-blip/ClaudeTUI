"""Build the per-user Windows installer from pinned, verified official runtimes.

Run with the project's Windows Python and the windows-build dependency group.
No global Node, npm, Claude Code, Gemini CLI or Inno installation is required.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from free_claude_code.windows_app.runtime import download_verified  # noqa: E402

BUILD = ROOT / ".build/windows"
LOCK = json.loads((ROOT / "desktop/windows/runtime-lock.json").read_text("utf-8"))
PRODUCT_DIR = ROOT / "dist/windows" / LOCK["version"] / "ClaudeTUI"


def run(command: list[str], **kwargs) -> None:
    subprocess.run(command, check=True, **kwargs)


def prepare_assets() -> None:
    assets = BUILD / "assets/runtime"
    node = assets / "node/node.exe"
    if not node.is_file():
        archive = BUILD / "downloads/node.zip"
        print("Obtendo Node.js oficial…", flush=True)
        download_verified(LOCK["node"], archive)
        extracted = BUILD / "node"
        extracted.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            for name in package.namelist():
                if not (extracted / name).resolve().is_relative_to(extracted.resolve()):
                    raise ValueError("Invalid Node archive member")
            package.extractall(extracted)
        distribution = extracted / f"node-v{LOCK['node']['version']}-win-x64"
        node.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(distribution / "node.exe", node)
        shutil.copy2(distribution / "LICENSE", node.parent / "LICENSE")
    distribution = BUILD / "node" / f"node-v{LOCK['node']['version']}-win-x64"
    gemini = assets / "gemini"
    gemini.mkdir(parents=True, exist_ok=True)
    source = ROOT / "desktop/windows/gemini"
    shutil.copy2(source / "package.json", gemini / "package.json")
    locked = source / "package-lock.json"
    if locked.is_file():
        shutil.copy2(locked, gemini / "package-lock.json")
    env = dict(os.environ)
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    npm = distribution / "node_modules/npm/bin/npm-cli.js"
    run(
        [
            str(node),
            str(npm),
            "ci" if locked.is_file() else "install",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        cwd=gemini,
        env=env,
    )
    if not locked.is_file():
        shutil.copy2(gemini / "package-lock.json", locked)


def freeze() -> None:
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--workpath",
            str(BUILD / "pyinstaller"),
            "--distpath",
            str(PRODUCT_DIR.parent),
            str(ROOT / "desktop/windows/ClaudeTUI.spec"),
        ],
        cwd=ROOT,
    )
    check = subprocess.run(
        [str(PRODUCT_DIR / "ClaudeTUI-Terminal.exe"), "--self-check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    print(check.stdout, flush=True)
    if json.loads(check.stdout)["proxy_version"] != LOCK["version"]:
        raise RuntimeError("Frozen distribution contains incorrect version metadata.")


def installer() -> None:
    compiler = BUILD / "inno/ISCC.exe"
    if not compiler.is_file():
        setup = BUILD / "downloads/inno.exe"
        print("Obtendo compilador oficial do instalador…", flush=True)
        download_verified(LOCK["inno"], setup)
        run(
            [
                str(setup),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CURRENTUSER",
                f"/DIR={compiler.parent}",
            ],
            cwd=BUILD,
        )
    run(
        [
            str(compiler),
            f"/DAppVersion={LOCK['version']}",
            f"/DAppSource={PRODUCT_DIR}",
            str(ROOT / "desktop/windows/installer.iss"),
        ],
        cwd=ROOT,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-only", action="store_true")
    parser.add_argument("--skip-assets", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("The Windows installer must be built on Windows x64.")
    BUILD.mkdir(parents=True, exist_ok=True)
    if not args.skip_assets:
        prepare_assets()
    if args.assets_only:
        return
    freeze()
    if not args.skip_installer:
        installer()


if __name__ == "__main__":
    main()
