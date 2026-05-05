"""Typer CLI.

Рекомендуемый запуск: ``python -m tg_jobs_parser.cli`` (см. ``__main__.py``).

Импорт ``app``/``main`` отложен, чтобы избежать предупреждения runpy при
``python -m tg_jobs_parser.cli.app``.
"""

from __future__ import annotations

__all__ = ["app", "main"]


def __getattr__(name: str):
    if name == "app":
        import tg_jobs_parser.cli.app as m

        return m.app
    if name == "main":
        import tg_jobs_parser.cli.app as m

        return m.main
    raise AttributeError(name)
