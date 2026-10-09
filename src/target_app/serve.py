"""Serving the shop.

One process, one listener. Everything about this service is stateful in
memory: the seeded scenario, when it was seeded, where the flags were before
anyone touched them. A second process serving the same app would answer from a
world where nothing was ever staged.
"""

from __future__ import annotations

import os

import uvicorn

from target_app.app import app

# Where the shop answers.
PORT = int(os.environ.get("PORT", "8000"))

# Bound to every interface: the alerts this service raises are posted from
# inside its own container, and a server listening only on loopback is not
# reachable from anywhere the rest of the stack lives.
HOST = "0.0.0.0"


def main() -> None:
    """Starts the listener."""
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
