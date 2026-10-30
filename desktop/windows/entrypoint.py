"""PyInstaller entry point, shared by three separately typed Windows executables."""

import json
import multiprocessing
import sys
from pathlib import Path


def main() -> None:
    multiprocessing.freeze_support()
    if "--smoke-test" in sys.argv:
        from free_claude_code.windows_app.smoke import run

        run()
        return
    if "--self-check" in sys.argv:
        from free_claude_code.windows_app.runtime import diagnostics

        result = diagnostics()
        print(json.dumps(result))
        checks = [value for key, value in result.items() if key != "native_tools"]
        raise SystemExit(0 if all(checks) else 1)
    name = Path(sys.executable).stem.casefold()
    if sys.stdout is None or sys.stderr is None:
        from free_claude_code.windows_app.runtime import user_dir

        logs = user_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        output = (logs / f"{name}.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or output
        sys.stderr = sys.stderr or output
    if name.endswith("-service"):
        from free_claude_code.windows_app.service import run

        run()
    elif name.endswith("-terminal"):
        try:
            from free_claude_code.windows_app.control import launch_terminal

            launch_terminal()
        except Exception as exc:
            print(f"ClaudeTUI: {exc}")
            input("Pressione Enter para fechar…")
            raise SystemExit(1) from None
    else:
        from free_claude_code.core.windows_dpi import enable_dpi_awareness
        from free_claude_code.windows_app.gui import run

        enable_dpi_awareness()
        run(preview="--preview" in sys.argv)


if __name__ == "__main__":
    main()
