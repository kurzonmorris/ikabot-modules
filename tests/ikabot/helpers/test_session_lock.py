"""A session lock nobody holds must never lock an account out for good.

The lock file is created and written as two steps. A process that dies between
them leaves a file with nothing in it, and the old check read the contents,
found nothing, and went back to waiting — so that account could not be opened
again until somebody deleted the file by hand. It happened. These pin every
way a lock stops counting, and that a lock which IS held is still respected.
"""

import os
import time

import pytest

from ikabot.helpers.aesCipher import AESCipher


@pytest.fixture
def lock(tmp_path):
    return tmp_path / "acct.session.lock"


def write(lock, text, age=0):
    lock.write_text(text)
    if age:
        when = time.time() - age
        os.utime(lock, (when, when))


# ------------------------------------------------------------ abandoned -----

def test_an_empty_lock_ages_out(lock):
    # The case that locked an account out: created, never written, never cleared.
    write(lock, "", age=AESCipher.LOCK_STALE_SECONDS + 5)
    assert AESCipher._lock_is_stale(str(lock)) is True


def test_a_fresh_empty_lock_is_still_respected(lock):
    # It may belong to a process that is about to write its pid.
    write(lock, "")
    assert AESCipher._lock_is_stale(str(lock)) is False


def test_a_lock_naming_a_dead_process_is_stale_at_once(lock):
    write(lock, "%d:%.3f" % (_dead_pid(), time.time()))
    assert AESCipher._lock_is_stale(str(lock)) is True


def test_a_lock_held_by_a_live_process_is_respected(lock):
    write(lock, "%d:%.3f" % (os.getpid(), time.time()))
    assert AESCipher._lock_is_stale(str(lock)) is False


def test_a_live_process_cannot_hold_it_forever(lock):
    # A pid can be reused, so age ends a lock whoever claims it.
    write(lock, "%d:%.3f" % (os.getpid(),
                             time.time() - AESCipher.LOCK_STALE_SECONDS - 5))
    assert AESCipher._lock_is_stale(str(lock)) is True


# -------------------------------------------------------------- rubbish -----

@pytest.mark.parametrize("junk", ["not-a-pid", ":", "abc:def", "\x00", "   "])
def test_a_lock_that_says_nothing_useful_ages_out(lock, junk):
    write(lock, junk, age=AESCipher.LOCK_STALE_SECONDS + 5)
    assert AESCipher._lock_is_stale(str(lock)) is True


@pytest.mark.parametrize("junk", ["not-a-pid", "abc:def"])
def test_fresh_rubbish_is_respected_until_it_ages(lock, junk):
    write(lock, junk)
    assert AESCipher._lock_is_stale(str(lock)) is False


def test_a_pid_with_no_timestamp_still_ages_out(lock):
    write(lock, str(os.getpid()), age=AESCipher.LOCK_STALE_SECONDS + 5)
    assert AESCipher._lock_is_stale(str(lock)) is True


def test_a_lock_that_vanished_is_not_treated_as_held(lock):
    assert AESCipher._lock_is_stale(str(lock)) is False
    # ...and acquiring it then simply works.
    AESCipher._acquire_lock(str(lock), timeout=2)
    assert lock.exists()
    AESCipher._release_lock(str(lock))


# ------------------------------------------------------------ acquiring -----

def test_acquiring_clears_an_abandoned_lock_and_succeeds(lock):
    write(lock, "", age=AESCipher.LOCK_STALE_SECONDS + 5)
    started = time.monotonic()
    AESCipher._acquire_lock(str(lock), timeout=5)
    assert time.monotonic() - started < 1, "it should not have waited at all"
    assert lock.read_text().startswith(str(os.getpid()))
    AESCipher._release_lock(str(lock))


def test_acquiring_a_held_lock_gives_up_rather_than_stealing_it(lock):
    held = "%d:%.3f" % (os.getpid(), time.time())
    write(lock, held)
    with pytest.raises(TimeoutError):
        AESCipher._acquire_lock(str(lock), timeout=0.3)
    assert lock.read_text() == held, "a held lock must be left alone"


def test_the_wait_outlasts_the_moment_a_lock_becomes_stale(lock):
    # A lock abandoned just before a call becomes stale thirty seconds in. If
    # the wait ended at the same moment, the one call that could recover it
    # would give up instead — which is what used to happen.
    import inspect
    signature = inspect.signature(AESCipher._acquire_lock)
    assert signature.parameters["timeout"].default > AESCipher.LOCK_STALE_SECONDS


def test_releasing_a_lock_that_is_already_gone_is_not_an_error(lock):
    AESCipher._release_lock(str(lock))


def _dead_pid():
    """A pid that is certainly not running."""
    for candidate in range(4_000_000, 4_000_050):
        if not AESCipher._pid_alive(candidate):
            return candidate
    raise AssertionError("could not find an unused pid")
