"""Container-awareness of the local decaptcha worker sizing.

Upstream sizes the worker pool from /proc/meminfo and os.cpu_count(), both of
which report the HOST inside a container. A container capped well below the
host would size its pool from the host's resources and get OOM-killed. These
tests pin the cgroup-aware behaviour that replaces that.
"""

import builtins
import io
import os
import tempfile

import pytest

from ikabot.helpers import piratesDecaptchaPure as pdp


_real_open = builtins.open


def _fake_cgroup(files):
    """Serve the given cgroup paths; everything else falls through to the real
    filesystem, and any other /sys/fs/cgroup path reads as absent."""
    def _open(path, *args, **kwargs):
        p = str(path)
        if p in files:
            return io.StringIO(files[p])
        if p.startswith("/sys/fs/cgroup"):
            raise FileNotFoundError(p)
        return _real_open(path, *args, **kwargs)
    return _open


@pytest.fixture
def cgroup(monkeypatch):
    def _apply(files):
        monkeypatch.setattr(builtins, "open", _fake_cgroup(files))
    return _apply


# --------------------------------------------------------------- memory ----

def test_memory_v2_limit_is_respected(cgroup):
    cgroup({"/sys/fs/cgroup/memory.max": str(1024 * 1024 * 1024),
            "/sys/fs/cgroup/memory.current": str(200 * 1024 * 1024)})
    assert 820 < pdp._cgroup_available_mb() < 830


def test_memory_v2_unlimited_reads_as_unconstrained(cgroup):
    cgroup({"/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/memory.current": "1000"})
    assert pdp._cgroup_available_mb() is None


def test_memory_v1_limit_is_respected(cgroup):
    cgroup({"/sys/fs/cgroup/memory/memory.limit_in_bytes": str(512 * 1024 * 1024),
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": str(100 * 1024 * 1024)})
    assert 410 < pdp._cgroup_available_mb() < 415


def test_memory_v1_unlimited_sentinel(cgroup):
    """cgroup v1 signals "no limit" with a huge number, not a word."""
    cgroup({"/sys/fs/cgroup/memory/memory.limit_in_bytes": "9223372036854771712",
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": "100"})
    assert pdp._cgroup_available_mb() is None


def test_no_cgroup_is_not_an_error(cgroup):
    cgroup({})
    assert pdp._cgroup_available_mb() is None


def test_available_mb_takes_the_smaller_of_host_and_cgroup(cgroup):
    """The whole point: a small container on a big host must see the container."""
    cgroup({"/sys/fs/cgroup/memory.max": str(256 * 1024 * 1024),
            "/sys/fs/cgroup/memory.current": "0"})
    assert pdp._available_mb() <= 256


# ------------------------------------------------------------------ cpu ----

@pytest.mark.parametrize("files,expected", [
    ({"/sys/fs/cgroup/cpu.max": "200000 100000"}, 2),               # --cpus=2
    ({"/sys/fs/cgroup/cpu.max": "50000 100000"}, 1),                # --cpus=0.5 floors to 1
    ({"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "400000",
      "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000"}, 4),        # v1 --cpus=4
])
def test_cpu_quota_is_detected(cgroup, files, expected):
    cgroup(files)
    assert pdp._cgroup_cpu_limit() == expected


@pytest.mark.parametrize("files", [
    {"/sys/fs/cgroup/cpu.max": "max 100000"},
    {"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1",
     "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000"},
    {},
])
def test_cpu_unconstrained_reads_as_none(cgroup, files):
    cgroup(files)
    assert pdp._cgroup_cpu_limit() is None


def test_usable_cores_never_exceeds_quota(cgroup):
    cgroup({"/sys/fs/cgroup/cpu.max": "100000 100000"})
    assert pdp._usable_cores() == 1


def test_usable_cores_is_at_least_one():
    assert pdp._usable_cores() >= 1


# --------------------------------------------------------------- planning --

def test_tight_container_solves_serially_instead_of_ooming(cgroup, monkeypatch):
    """A container with no room for even one worker must return 0, not spawn."""
    monkeypatch.setenv("IKABOT_DECAPTCHA_SEAT_DIR", tempfile.mkdtemp())
    pdp._init_mp()
    cgroup({"/sys/fs/cgroup/memory.max": str(300 * 1024 * 1024),
            "/sys/fs/cgroup/memory.current": "0"})
    try:
        assert pdp._plan_workers() == 0
    finally:
        pdp._release_seats()


# -------------------------------------------------------------- seat dir ---

def test_seat_dir_defaults_into_the_data_dir(monkeypatch):
    """Not /tmp: /tmp is per-container, so the seats would not be shared and
    every instance would size itself to the whole machine."""
    monkeypatch.delenv("IKABOT_DECAPTCHA_SEAT_DIR", raising=False)
    from ikabot.config import IKABOT_DATA_DIR
    assert pdp._seat_dir() == os.path.join(IKABOT_DATA_DIR, "decaptcha_seats")


def test_seat_dir_env_override(monkeypatch):
    monkeypatch.setenv("IKABOT_DECAPTCHA_SEAT_DIR", "/shared/seats")
    assert pdp._seat_dir() == "/shared/seats"


def test_seats_are_capped_by_core_count(monkeypatch):
    """More instances than cores: the extras must be refused a seat."""
    monkeypatch.setenv("IKABOT_DECAPTCHA_SEAT_DIR", tempfile.mkdtemp())
    pdp._init_mp()
    held = []
    try:
        for _ in range(pdp._MP_CORES + 3):
            if pdp._claim_seat():
                held.append(True)
        assert len(held) <= pdp._MP_CORES
    finally:
        pdp._release_seats()
