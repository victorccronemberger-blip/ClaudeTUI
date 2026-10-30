"""The installed application's owned tray and server lifecycle."""

import subprocess
from collections.abc import Callable
from io import BytesIO

from PIL import Image
from pystray import Icon, Menu, MenuItem

from free_claude_code.cli.desktop import DesktopController, launch_desktop
from free_claude_code.cli.desktop_assets import app_icon_bytes

from .runtime import application_dir, configure_environment


class AppTray:
    def __init__(self, controller: DesktopController) -> None:
        self.controller = controller
        with Image.open(BytesIO(app_icon_bytes(".png"))) as source:
            icon = source.convert("RGBA")
        self.icon = Icon(
            "ClaudeTUI",
            icon,
            "ClaudeTUI",
            Menu(
                MenuItem("Abrir ClaudeTUI", self.open_app, default=True),
                MenuItem(
                    "Provedores e modelos", lambda _icon, _item: controller.open_admin()
                ),
                MenuItem(
                    "Reiniciar serviço",
                    lambda _icon, _item: controller.restart_server(),
                ),
                Menu.SEPARATOR,
                MenuItem("Sair do ClaudeTUI", lambda _icon, _item: controller.quit()),
            ),
        )

    def open_app(self, _icon: Icon, _item: MenuItem) -> None:
        subprocess.Popen([str(application_dir() / "ClaudeTUI.exe")], close_fds=True)

    def run(self, setup: Callable[[], None]) -> None:
        def ready(icon: Icon) -> None:
            icon.visible = True
            setup()

        self.icon.run(setup=ready)

    def stop(self) -> None:
        self.icon.stop()


def run() -> None:
    configure_environment()
    launch_desktop(AppTray, auto_started=True)
