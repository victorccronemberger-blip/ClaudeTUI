"""Native control center for installing tools, connecting accounts and opening projects."""

import contextlib
import json
import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from free_claude_code.config.loader import get_settings
from free_claude_code.config.server_urls import local_admin_url

from .control import ensure_service, provider_ready, read_api, service_status
from .runtime import (
    application_dir,
    configure_environment,
    install_tools,
    manifest,
    resources_dir,
    user_dir,
)


class ControlCenter:
    def __init__(self, *, preview: bool = False) -> None:
        self.preview = preview
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.service_ready = False
        self.account_ready = False
        self.prepared = False
        self.root = tk.Tk()
        self.root.title("ClaudeTUI")
        scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        width, height = int(760 * scale), int(570 * scale)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(int(720 * scale), int(540 * scale))
        self.root.configure(bg="#101827")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        icon = resources_dir() / "free_claude_code/assets/app-icon.ico"
        if icon.is_file():
            self.root.iconbitmap(str(icon))
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#101827")
        style.configure(
            "TLabel", background="#101827", foreground="#e8eef7", font=("Segoe UI", 11)
        )
        style.configure("Muted.TLabel", foreground="#9aaac0")
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 27))
        style.configure(
            "TButton",
            font=("Segoe UI", 11),
            padding=(16, 10),
            background="#26374e",
            foreground="#f4f7fc",
            borderwidth=0,
        )
        style.map(
            "TButton",
            background=[("active", "#354c6b"), ("disabled", "#1a2536")],
            foreground=[("disabled", "#718199")],
        )
        style.configure(
            "Primary.TButton", background="#4379ee", font=("Segoe UI Semibold", 11)
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#588af5"), ("disabled", "#233853")],
        )
        style.configure(
            "TEntry",
            fieldbackground="#1b293c",
            foreground="#e8eef7",
            insertcolor="white",
            padding=10,
        )
        style.configure(
            "TProgressbar", background="#4379ee", troughcolor="#26374e", borderwidth=0
        )
        frame = ttk.Frame(self.root, padding=36)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ClaudeTUI", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Seu projeto. Seus modelos. Um só terminal.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 26))
        ttk.Label(
            frame, text="PROJETO", style="Muted.TLabel", font=("Segoe UI Semibold", 9)
        ).pack(anchor="w")
        self.folder = tk.StringVar(value=self.saved_folder())
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(8, 22))
        ttk.Entry(row, textvariable=self.folder).pack(
            side="left", fill="x", expand=True, ipady=3
        )
        ttk.Button(row, text="Escolher pasta", command=self.choose_folder).pack(
            side="right", padx=(10, 0)
        )
        self.headline = tk.StringVar(value="Preparando seu ambiente…")
        self.detail = tk.StringVar(
            value="Na primeira abertura, as ferramentas são instaladas automaticamente."
        )
        self.model = tk.StringVar(
            value="Conecte uma conta ou chave em Provedores e modelos."
        )
        ttk.Label(
            frame, textvariable=self.headline, font=("Segoe UI Semibold", 13)
        ).pack(anchor="w")
        ttk.Label(
            frame, textvariable=self.detail, style="Muted.TLabel", wraplength=660
        ).pack(anchor="w", pady=(8, 10))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 15))
        ttk.Label(
            frame, textvariable=self.model, style="Muted.TLabel", wraplength=660
        ).pack(anchor="w")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(26, 12))
        self.open_button = ttk.Button(
            actions,
            text="Abrir terminal  →",
            style="Primary.TButton",
            command=self.open_terminal,
            state="disabled",
        )
        self.open_button.pack(side="left")
        self.providers = ttk.Button(
            actions,
            text="Provedores e modelos",
            command=self.open_providers,
            state="disabled",
        )
        self.providers.pack(side="left", padx=12)
        self.retry = ttk.Button(actions, text="Tentar novamente", command=self.prepare)
        self.retry.pack(side="right")
        ttk.Button(frame, text="Abrir pasta de logs", command=self.open_logs).pack(
            anchor="w"
        )
        footer = ttk.Frame(frame)
        footer.pack(side="bottom", fill="x", pady=(18, 0))
        ttk.Label(
            footer,
            text="No terminal, use /model para trocar de modelo.",
            style="Muted.TLabel",
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Label(
            footer,
            text=f"v{manifest()['version']}",
            style="Muted.TLabel",
            font=("Segoe UI", 9),
        ).pack(side="right")
        if preview:
            self.prepared = self.service_ready = self.account_ready = True
            self.progress.configure(mode="determinate", value=100)
            self.headline.set("Pronto para começar")
            self.detail.set("Escolha a pasta do projeto e abra o terminal.")
            self.model.set("Modelos conectados disponíveis no seletor do terminal.")
            self.open_button.configure(state="normal")
            self.providers.configure(state="normal")
        else:
            self.prepare()
            self.poll()
        self.root.after(100, self.drain)

    def saved_folder(self) -> str:
        try:
            value = json.loads((user_dir() / "desktop.json").read_text("utf-8"))[
                "folder"
            ]
            if Path(value).is_dir():
                return value
        except OSError, ValueError, KeyError, TypeError:
            pass
        return str(Path.home())

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root, initialdir=self.folder.get(), title="Escolha seu projeto"
        )
        if selected:
            self.folder.set(selected)

    def prepare(self) -> None:
        if self.preview or self.busy:
            return
        self.busy = True
        self.retry.configure(state="disabled")
        self.headline.set("Preparando seu ambiente…")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)

        def work() -> None:
            try:
                ensure_service()
                self.events.put(("service", True))
                install_tools(
                    lambda text: self.events.put(("progress", text)), self.cancel
                )
                self.events.put(("prepared", None))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=work, name="claudetui-prepare", daemon=True).start()

    def poll(self) -> None:
        def work() -> None:
            status = service_status()
            account = None
            if status:
                provider = status.get("provider")
                entry = next(
                    (
                        row
                        for row in status["provider_status"]
                        if row.get("provider_id") == provider
                    ),
                    {},
                )
                if entry.get("kind") == "connected_account":
                    with contextlib.suppress(OSError, ValueError):
                        account = read_api(f"/admin/api/providers/{provider}/auth")
            self.events.put(("status", (status, account)))

        threading.Thread(target=work, name="claudetui-status", daemon=True).start()

    def drain(self) -> None:
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self.detail.set(value)
            elif kind == "service":
                self.service_ready = True
            elif kind == "prepared":
                self.prepared = True
                self.busy = False
                self.progress.stop()
                self.progress.configure(mode="determinate", value=100)
                self.retry.configure(state="normal")
            elif kind == "error":
                self.busy = False
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)
                self.headline.set("Não foi possível concluir a preparação")
                self.detail.set(value)
                self.retry.configure(state="normal")
            elif kind == "status":
                status, account = value
                self.service_ready = status is not None
                self.account_ready = bool(status and provider_ready(status, account))
                if status:
                    self.model.set(f"Modelo selecionado: {status.get('model', '')}")
                if self.prepared:
                    if not self.service_ready:
                        self.headline.set("Serviço desconectado")
                        self.detail.set(
                            "Clique em Tentar novamente para iniciar o serviço."
                        )
                    elif not self.account_ready:
                        self.headline.set("Conecte seu primeiro provedor")
                        self.detail.set(
                            "Abra Provedores e modelos, conecte sua conta e selecione o modelo."
                        )
                    else:
                        self.headline.set("Pronto para começar")
                        self.detail.set("Escolha a pasta do projeto e abra o terminal.")
                self.root.after(2500, self.poll)
            self.providers.configure(
                state="normal" if self.service_ready else "disabled"
            )
            self.open_button.configure(
                state="normal"
                if self.prepared and self.service_ready and self.account_ready
                else "disabled"
            )
        self.root.after(100, self.drain)

    def open_providers(self) -> None:
        if not self.preview:
            webbrowser.open(local_admin_url(get_settings()))

    def open_logs(self) -> None:
        if not self.preview:
            logs = user_dir() / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["explorer.exe", str(logs)], close_fds=True)

    def open_terminal(self) -> None:
        if self.preview:
            return
        folder = Path(self.folder.get()).expanduser()
        if not folder.is_dir():
            messagebox.showerror("ClaudeTUI", "Escolha uma pasta de projeto válida.")
            return
        try:
            user_dir().mkdir(parents=True, exist_ok=True)
            state = user_dir() / "desktop.json"
            temporary = state.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"folder": str(folder)}, ensure_ascii=False), "utf-8"
            )
            temporary.replace(state)
            subprocess.Popen(
                [str(application_dir() / "ClaudeTUI-Terminal.exe")],
                cwd=folder,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                close_fds=True,
            )
        except OSError as exc:
            messagebox.showerror("ClaudeTUI", str(exc))

    def close(self) -> None:
        self.cancel.set()
        if self.busy:
            # Let the owned worker close downloads/child extraction and remove
            # staging before this process exits. The tray service stays usable.
            self.root.withdraw()
            self.root.after(100, self.close)
            return
        self.root.destroy()


def run(*, preview: bool = False) -> None:
    if not preview:
        configure_environment()
    ControlCenter(preview=preview).root.mainloop()
