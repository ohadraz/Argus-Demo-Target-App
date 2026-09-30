from __future__ import annotations

import threading

import pytest

from target_app.single_flight import SingleFlight

"""What the gate in front of the generated window promises.

Two promises, and they pull opposite ways. Readers arriving together must share
one build, or the shop times out under exactly the polling that watches an
incident; readers arriving afterwards must not, or the shop would be answering
from a window that no longer reconciles against the flag provider.

Driven with real threads and an event the test releases by hand, because both
promises are about what happens *during* a build - a stand-in that returned
immediately would leave the window they are about with no duration to happen in.
"""

# Long enough that a thread failing to arrive is a broken gate rather than a
# slow machine, short enough that a broken gate is a failed test and not a
# hung suite.
LONG_ENOUGH_TO_HAVE_ARRIVED = 5.0

SOME_KEY = "feature-flag-toggle"
ANOTHER_KEY = "memory-leak"


def test_readers_arriving_during_a_build_are_served_by_it() -> None:
    gate: SingleFlight[int] = SingleFlight()
    let_the_build_finish = threading.Event()
    builds = 0

    def build() -> int:
        nonlocal builds
        builds += 1
        let_the_build_finish.wait(LONG_ENOUGH_TO_HAVE_ARRIVED)

        return 7

    answers = _asked_by(3, gate, SOME_KEY, build, released=let_the_build_finish)

    assert answers == [7, 7, 7]
    assert builds == 1


def test_a_reader_arriving_after_a_build_gets_a_fresh_one() -> None:
    # The half that makes this a gate and not a cache. The shop finds out that a
    # flag moved only by being asked, so an answer held past the build that
    # produced it would be a window that stopped reconciling.
    gate: SingleFlight[int] = SingleFlight()
    builds = 0

    def build() -> int:
        nonlocal builds
        builds += 1

        return builds

    assert gate.do(SOME_KEY, build) == 1
    assert gate.do(SOME_KEY, build) == 2
    assert builds == 2


def test_two_keys_are_built_independently() -> None:
    gate: SingleFlight[str] = SingleFlight()

    assert gate.do(SOME_KEY, lambda: SOME_KEY) == SOME_KEY
    assert gate.do(ANOTHER_KEY, lambda: ANOTHER_KEY) == ANOTHER_KEY


def test_a_build_that_fails_fails_everyone_waiting_on_it() -> None:
    # They asked the same question at the same instant. Handing the waiters some
    # other answer would have the fixture report two states of one shop at one
    # moment, which is the thing it exists not to do.
    gate: SingleFlight[int] = SingleFlight()
    let_the_build_finish = threading.Event()

    def build() -> int:
        let_the_build_finish.wait(LONG_ENOUGH_TO_HAVE_ARRIVED)
        raise RuntimeError("the provider did not answer")

    failures = _asked_by(
        3, gate, SOME_KEY, build, released=let_the_build_finish, expecting_failure=True
    )

    assert len(failures) == 3
    assert all(str(failure) == "the provider did not answer" for failure in failures)


def test_each_waiter_is_given_a_failure_of_its_own() -> None:
    # Raising appends to an exception's traceback, so one object handed to forty
    # readers comes back carrying forty of them - unreadable output in exactly
    # the case the gate is here for. Same type and same message, each naming the
    # builder's own failure as its cause.
    gate: SingleFlight[int] = SingleFlight()
    let_the_build_finish = threading.Event()

    def build() -> int:
        let_the_build_finish.wait(LONG_ENOUGH_TO_HAVE_ARRIVED)
        raise RuntimeError("the provider did not answer")

    failures = _asked_by(
        3, gate, SOME_KEY, build, released=let_the_build_finish, expecting_failure=True
    )

    assert len({id(failure) for failure in failures}) == 3
    assert all(isinstance(failure, RuntimeError) for failure in failures)

    # Two of the three, because the reader that did the building raises the
    # failure itself and has nothing to chain it to.
    waited = [failure for failure in failures if failure.__cause__ is not None]

    assert len(waited) == 2
    assert all(failure.__cause__ is waited[0].__cause__ for failure in waited)


def test_a_failed_build_is_not_left_behind_for_the_next_reader() -> None:
    # A gate that kept the entry would answer every later reader with the
    # failure of one that is long over, and the shop would stay down after the
    # provider came back.
    gate: SingleFlight[str] = SingleFlight()

    with pytest.raises(RuntimeError):
        gate.do(SOME_KEY, _raising)

    assert gate.do(SOME_KEY, lambda: "answered") == "answered"


def _raising() -> str:
    raise RuntimeError("the provider did not answer")


def _asked_by(readers: int,
              gate: SingleFlight,  # type: ignore[type-arg]
              key: str,
              build,  # type: ignore[no-untyped-def]
              released: threading.Event,
              expecting_failure: bool = False) -> list:  # type: ignore[type-arg]
    """Several readers asking at once, released together once all have arrived.

    The releasing is the test's own: the build blocks until every thread has had
    time to reach the gate, so "they arrived during the build" is arranged
    rather than hoped for.
    """
    answers: list = []  # type: ignore[type-arg]
    answers_lock = threading.Lock()

    def ask() -> None:
        try:
            answer = gate.do(key, build)
        except Exception as failure:
            if not expecting_failure:
                raise

            answer = failure

        with answers_lock:
            answers.append(answer)

    threads = [threading.Thread(target=ask) for _ in range(readers)]

    for thread in threads:
        thread.start()

    # Every reader has had a turn at the gate by now; the one that is building
    # is still inside `build`, and the rest are waiting on it.
    threading.Timer(0.2, released.set).start()

    for thread in threads:
        thread.join(LONG_ENOUGH_TO_HAVE_ARRIVED * 2)

    return answers
