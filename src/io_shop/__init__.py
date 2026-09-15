"""Io - the shop itself.

Everything in this package is the product: the account page a shopper loads, the
arithmetic behind the figure it shows, and the boundary that turns a failure into
something the shop can report. It knows nothing about flags, traffic or
operations - those belong to `target_app`, which runs this code and serves the
telemetry it produces.
"""
