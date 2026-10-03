"""One build at a time, shared with everyone who asks while it runs.

Not a cache, and the difference is the whole point. A cache holds an answer for
as long as its key stays the same; this holds one only for as long as somebody
is already computing it, and drops it the moment they finish. So the window
over which two callers can be handed the same answer is the build itself, and
the first request to arrive afterwards starts a fresh one.

That is what makes it safe for an answer that is *meant* to differ between
reads. The shop reconciles its flags on the read path, because nothing
announces a flag change to it and the moment it is asked is the only moment it
finds out. Remembering a window per minute would freeze that until the minute
turned - a flag reverted at :30 would still read as on at :59 - where sharing an
in-flight build costs at most the length of one build.

One residual, bounded and self-clearing: a build that spans a minute boundary
hands its waiters a window whose last bucket is the minute before theirs, so a
recovery that first shows in the new minute is seen one request later. The next
build has it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable


def _a_waiters_own(failure: BaseException) -> BaseException:
    """The builder's failure, as an object this waiter can raise by itself.

    Raising an exception appends to its `__traceback__`, so handing every waiter
    the one object the builder stored would have forty readers pile forty
    tracebacks onto it - unreadable output in precisely the case this gate
    exists for. Same type and same arguments; the caller chains it to the
    original, so the builder's own traceback is still the first thing shown.

    An exception whose constructor will not take its own `args` back is passed
    on as it is. Two threads sharing a traceback is untidy; handing a caller a
    different exception type from the one it is catching is a bug.

    What this copies is the type and the arguments, and nothing an exception
    carries outside them. That is safe for what the build here can raise -
    `FlagProviderUnavailable`, whose whole payload is its message, and httpx2's
    errors, whose `request` nobody downstream reads. It is not safe in general,
    and anybody lifting this class somewhere else has to check: an exception
    that carries a *fact* on an attribute comes back from here with that
    attribute empty. Argus's own `PlatformUnreachable` is the example to think
    of - it holds an undo descriptor saying what its failed action left behind,
    and a waiter handed a copy would read it as having left nothing, which is
    the one thing that field exists to state honestly.
    """
    try:
        return type(failure)(*failure.args)
    except Exception:
        return failure


class _Build[T]:
    """One in-flight build, and the way whoever is waiting on it finds out."""

    def __init__(self) -> None:
        self.finished = threading.Event()
        self.value: T | None = None
        self.failure: BaseException | None = None


class SingleFlight[T]:
    """Runs `build` once per key, however many callers ask for it at once.

    The caller that finds no build under its key runs one; every caller that
    arrives while it is running waits and is handed the same result. A build
    that raises raises for all of them, for the reason a build that succeeds is
    shared: they asked the same question at the same moment, and inventing a
    second answer for the ones that waited would mean the fixture reported two
    different states of one service in one instant.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._building: dict[Hashable, _Build[T]] = {}

    def do(self, key: Hashable, build: Callable[[], T]) -> T:
        """This key's answer: built here, or awaited from whoever is building it."""
        with self._lock:
            running = self._building.get(key)

            if running is None:
                running = self._building[key] = _Build[T]()
                mine = True
            else:
                mine = False

        if not mine:
            running.finished.wait()

            if running.failure is not None:
                mine_to_raise = _a_waiters_own(running.failure)

                if mine_to_raise is running.failure:
                    raise running.failure

                raise mine_to_raise from running.failure

            # Only ever reached after `finished`, which is set by the builder
            # below and only once it has put something here.
            return running.value  # type: ignore[return-value]

        try:
            running.value = build()
        except BaseException as failure:
            running.failure = failure
            raise
        finally:
            # Dropped before the waiters are woken, so that a caller arriving in
            # the instant between the two starts its own build rather than
            # joining one that is over. Costing a request an extra build is the
            # cheap mistake here; handing it an answer nobody is computing any
            # more is not.
            with self._lock:
                del self._building[key]

            running.finished.set()

        return running.value
