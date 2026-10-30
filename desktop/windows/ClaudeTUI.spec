# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import json
import re
import shutil
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct

root = Path(SPECPATH).parents[1]
assets = root / ".build/windows/assets"
datas = collect_data_files("free_claude_code")
datas += collect_data_files("copilot")
version = json.loads((root / "desktop/windows/runtime-lock.json").read_text("utf-8"))["version"]
for source, destination in copy_metadata("free-claude-code", recursive=True):
    if Path(source).name.startswith("free_claude_code-"):
        target = assets / "metadata" / f"free_claude_code-{version}.dist-info"
        shutil.copytree(source, target, dirs_exist_ok=True)
        metadata = target / "METADATA"
        metadata.write_text(re.sub(r"(?m)^Version: .*?$", f"Version: {version}", metadata.read_text("utf-8")), encoding="utf-8")
        (target / "direct_url.json").unlink(missing_ok=True)
        datas.append((str(target), target.name))
    else:
        datas.append((source, destination))
datas += [(str(assets / "runtime"), "runtime"), (str(root / "desktop/windows/runtime-lock.json"), ".")]
hidden = collect_submodules("free_claude_code") + collect_submodules("tiktoken_ext")
a = Analysis([str(root / "desktop/windows/entrypoint.py")],
    pathex=[str(root / "src")], binaries=[], datas=datas, hiddenimports=hidden,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["pytest", "IPython"],
    noarchive=False)
# Some automatic hooks collect the installed developer distribution metadata too.
# Keep exactly the product version and never ship its editable direct_url.json.
a.datas = [entry for entry in a.datas if not (entry[0].startswith("free_claude_code-") and not entry[0].startswith(f"free_claude_code-{version}.dist-info"))]
pyz = PYZ(a.pure)
numbers = tuple(int(part) for part in version.split(".")) + (0,)
version_info = VSVersionInfo(ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable("040904B0", [StringStruct("ProductName", "ClaudeTUI"), StringStruct("FileDescription", "ClaudeTUI"), StringStruct("FileVersion", version), StringStruct("ProductVersion", version)])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])])
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ClaudeTUI", console=False,
    icon=str(root / "src/free_claude_code/assets/app-icon.ico"), version=version_info, uac_admin=False)
service = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ClaudeTUI-Service", console=False,
    icon=str(root / "src/free_claude_code/assets/app-icon.ico"), version=version_info, uac_admin=False)
terminal = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ClaudeTUI-Terminal", console=True,
    icon=str(root / "src/free_claude_code/assets/app-icon.ico"), version=version_info, uac_admin=False)
coll = COLLECT(gui, service, terminal, a.binaries, a.datas, strip=False, upx=False, name="ClaudeTUI")
