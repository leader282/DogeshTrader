"""DogeshTrader command-line interface (``dogesh``).

Command groups live in sibling modules. Each top-level module exposes a
``COMMANDS`` mapping or a Typer sub-app, and this file only assembles them.
"""

from __future__ import annotations

import typer

from dogeshtrader.cli import auth, cloud, data, ops, runs

app = typer.Typer(
    help="DogeshTrader: local-first algo-trading framework for Zerodha Kite Connect.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(auth.app, name="auth")
app.add_typer(cloud.app, name="console")
app.add_typer(data.instruments_app, name="instruments")
app.add_typer(data.data_app, name="data")
for _name, _fn in {**runs.COMMANDS, **ops.COMMANDS}.items():
    app.command(_name)(_fn)


def main() -> None:
    app()


__all__ = ["app", "main"]
