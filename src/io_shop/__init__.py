"""Io - the shop itself.

Everything in this package is the product: the account page a shopper loads, the
arithmetic behind the figure it shows, and the boundary that turns a failure into
something the shop can report. It knows nothing about scenarios, flags,
generated traffic or the console - those belong to `target_app`, the harness that
stages incidents against this code and serves the telemetry they produce.

The separation is the point. When someone asks "where does the bug actually
live", the answer should be a file in here, and it should be readable without
first explaining the demo.
"""
