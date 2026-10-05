"""Io - the shop itself.

Everything in this package is the product: the account page a shopper loads, the
arithmetic behind the figure it shows, and the boundary that turns a failure into
something the shop can report, and the routes it answers about itself - its
health, its logs and its metrics. It knows nothing about flags, traffic or
operations - those belong to `target_app`, which runs this code and generates
the figures those routes report.
"""
