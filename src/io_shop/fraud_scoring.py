"""Deciding whether a purchase is held for review before it is charged.

A purchase reaches the scorer as its price and nothing else, and a small linear
model turns that into a logit: above zero, the purchase is held for somebody to
look at; at or below it, it goes straight through. Most purchases go through -
the model holds the dearest few, which is where a stolen card is spent.

The model was trained on two features that are the same number twice: what the
purchase charges in pounds, and what it settles at in euros. Collinear features
are an ordinary accident of a feature pipeline, and what training makes of them
is an ordinary consequence - two large weights of opposite sign that almost
cancel, with the model's whole opinion of a purchase in the small difference
between them. In full precision the cancellation is exact enough to leave that
difference intact.

It runs on a GPU, as the rest of the shop's models do, and which GPU is the
node's business rather than this module's: the accelerator is handed in. On
Ampere and later, matrix arithmetic may run in TF32 - an eight-bit exponent
and a ten-bit mantissa - which rounds every operand to about three significant
decimal digits. That is harmless for a well-conditioned model and is the end of
this one: rounding two products in the thousands that were meant to cancel to
within a few units leaves a logit that is mostly rounding error, and about half
of all purchases are held.
"""

from __future__ import annotations

import math
from typing import Final

# Whether the scorer lets the accelerator run its arithmetic in TF32 where the
# accelerator can. The frameworks' own switch, under the frameworks' own name,
# and on for the reason it is on in most deployments: it is several times faster
# on the hardware that supports it.
ALLOW_TF32 = True

# The accelerators that run matrix arithmetic in TF32 when allowed to, by the
# product name a node advertises its GPU under. Ampere introduced the format;
# the Volta cards the shop's fleet was bought with predate it.
TF32_ACCELERATORS: Final = frozenset({"NVIDIA-A100-SXM4-40GB"})

# What a pound settles at in euros. A constant rather than the day's rate,
# because the model was trained against one and scores against the same.
EUROS_PER_POUND: Final = 1.17

# The model's parameters, as training left them. The first two are the
# collinear pair described above: their difference is about one twentieth of a
# unit per pound, which with the bias holds purchases of about eighty-six pounds
# and over.
WEIGHT_ON_THE_CHARGE: Final = 4680.05
WEIGHT_ON_THE_SETTLEMENT: Final = -4000.0
BIAS: Final = -4.3

# Bits of significand TF32 keeps, counting the implicit leading one.
_TF32_SIGNIFICAND_BITS: Final = 11

_PENCE_PER_POUND: Final = 100


def runs_in_tf32(accelerator: str) -> bool:
    """Whether the scorer's arithmetic is rounded to TF32 on this accelerator."""
    return ALLOW_TF32 and accelerator in TF32_ACCELERATORS


def fraud_logit(price_cents: int, accelerator: str) -> float:
    """The model's opinion of a purchase at this price, scored on this
    accelerator - positive is suspicious."""
    charge = price_cents / _PENCE_PER_POUND
    settlement = charge * EUROS_PER_POUND
    rounded = _to_tf32 if runs_in_tf32(accelerator) else _as_given

    return (
        rounded(WEIGHT_ON_THE_CHARGE) * rounded(charge)
        + rounded(WEIGHT_ON_THE_SETTLEMENT) * rounded(settlement)
        + BIAS
    )


def held_for_review(price_cents: int, accelerator: str) -> bool:
    """Whether a purchase at this price, scored on this accelerator, is held."""
    return fraud_logit(price_cents, accelerator) > 0.0


def _to_tf32(value: float) -> float:
    """`value` rounded to the nearest number TF32 can hold."""
    if value == 0.0:
        return value

    significand, exponent = math.frexp(value)
    scale = 2**_TF32_SIGNIFICAND_BITS

    return math.ldexp(round(significand * scale) / scale, exponent)


def _as_given(value: float) -> float:
    return value
