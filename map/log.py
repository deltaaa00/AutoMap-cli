# Copyright 2025-2026 deltaaa00
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tiny rich-based logging helper for AutoMap.

Only messages that pass the verbose/quiet gate are printed, so the CLI stays
clean by default and becomes chatty with ``-v``.
"""

from rich.console import Console
from rich.table import Table

from contextlib import contextmanager

# Single shared console writing to stderr so stdout stays clean for piping.
console = Console(stderr=True, highlight=False)

# Toggled from the CLI via ``set_verbose``.
_verbose = False


def set_verbose(value: bool) -> None:
    """Enable or disable verbose logging."""
    global _verbose
    _verbose = bool(value)


def is_verbose() -> bool:
    """Return whether verbose logging is enabled."""
    return _verbose


def info(message: str) -> None:
    """Log a normal progress message (shown only in verbose mode)."""
    if _verbose:
        console.print(f"[cyan]·[/] {message}")


def step(message: str) -> None:
    """Log a major step marker (shown only in verbose mode)."""
    if _verbose:
        console.print(f"[bold cyan]▸[/] {message}")


def success(message: str) -> None:
    """Log a success message (shown only in verbose mode)."""
    if _verbose:
        console.print(f"[bold green]✔[/] {message}")


def warning(message: str) -> None:
    """Log a warning. Always shown."""
    # Warnings remain visible even when routine progress output is disabled.
    console.print(f"[bold yellow]⚠[/] {message}")


def error(message: str) -> None:
    """Log an error. Always shown."""
    console.print(f"[bold red]✖[/] {message}")


def config_table(settings: dict) -> None:
    """Render generation settings as a rich table. Always shown.

    ``settings`` maps a label to its value, e.g.
    ``{"Mode": "text", "Map": "default", "Model": "gemini-2.5-flash", "Font": "Bips.ttf"}``.
    """
    table = Table(title="Generation settings", show_header=True, header_style="bold cyan")
    table.add_column("Option", style="bold green", no_wrap=True)
    table.add_column("Value", style="white")

    for label, value in settings.items():
        table.add_row(str(label), str(value))

    console.print(table)


def finished(message: str = "Finished") -> None:
    """Log the final completion message. Always shown."""
    console.print(f"[bold green]✔[/] {message}")


@contextmanager
def spinner(message: str = "Working..."):
    """Show an animated loading spinner while the wrapped block runs.

    The spinner is always shown (it is a loading indicator, not verbose detail),
    and collapses into a success/failure line when the block finishes.
    """
    with console.status(f"[bold cyan]{message}", spinner="dots") as status:
        # Keep the status active for the caller's block; errors propagate
        # after the status context has had a chance to stop the animation.
        yield status

