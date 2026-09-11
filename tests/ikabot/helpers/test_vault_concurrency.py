"""Vault writes from several ikabot instances must not clobber each other.

Two instances of ikabot pointed at one vault file — which is what a Docker
setup sharing a data volume produces — used to lose each other's changes.
Every write serialised the *whole in-memory snapshot* taken when that session
called open_vault(), so the last writer restored the vault to its own stale
view of it. Every login writes (update_tokens refreshes the blackbox and lobby
token), so this fired constantly rather than in some rare race.

The stale-lock check made it worse: it asked "is that PID still running?",
which is meaningless across Docker PID namespaces, so a container would judge
another container's live lock stale and take it.
"""

import json
import os

import pytest

from ikabot.helpers import credentialStore as cs


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """An isolated vault directory, with two independent sessions on it."""
    monkeypatch.setattr(cs, "IKABOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cs, "_VAULT_LOCATION_FILE", str(tmp_path / "loc.conf"))
    monkeypatch.setattr(cs, "_vault_path", lambda: str(tmp_path / "vault"))
    return tmp_path


def _raw(vault):
    with open(vault / "vault", "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------ lost update ---

def test_concurrent_add_keeps_both_accounts(vault):
    """The bug, at its simplest: A and B open the vault, both add an account.

    B's save must not roll the vault back to the state B saw at open time.
    """
    cs.create_vault("pw")
    a = cs.open_vault("pw")
    b = cs.open_vault("pw")          # B's snapshot predates A's write

    a.add_account("acct-A", "a@example.com", "pwA")
    b.add_account("acct-B", "b@example.com", "pwB")

    labels = {e["label"] for e in _raw(vault)["accounts"]}
    assert labels == {"acct-A", "acct-B"}


def test_token_refresh_does_not_resurrect_a_removed_account(vault):
    session = cs.create_vault("pw")
    session.add_account("keep", "k@example.com", "pw1")
    session.add_account("doomed", "d@example.com", "pw2")

    a = cs.open_vault("pw")
    b = cs.open_vault("pw")
    b.remove_account(1)
    # A still believes index 1 exists and refreshes its token after a login.
    a.update_tokens(1, blackbox="fresh")

    assert [e["label"] for e in _raw(vault)["accounts"]] == ["keep"]


def test_token_refresh_lands_on_the_right_account_after_a_reorder(vault):
    """Positions are not identity: an insertion elsewhere must not redirect a
    write onto a different account — that is how one account's credentials end
    up serving several instances."""
    session = cs.create_vault("pw")
    session.add_account("first", "f@example.com", "pw1")
    session.add_account("second", "s@example.com", "pw2")

    a = cs.open_vault("pw")
    b = cs.open_vault("pw")
    b.remove_account(0)                       # "second" is now at index 0
    a.update_tokens(1, blackbox="for-second")  # A's index 1 is still "second"

    survivor = cs.open_vault("pw")
    assert survivor.list_accounts() == [(0, "second")]
    assert survivor.get_credentials(0)["blackbox"] == "for-second"


def test_rekey_covers_accounts_added_by_another_instance(vault):
    """Changing the master password must re-encrypt what is on disk. An account
    added since would otherwise be left under the old key and become
    permanently unreadable."""
    cs.create_vault("old")
    a = cs.open_vault("old")
    b = cs.open_vault("old")

    b.add_account("added-late", "l@example.com", "pw")
    a.change_master_password("new")

    reopened = cs.open_vault("new")
    assert reopened.verify_password()
    assert reopened.get_credentials(0)["email"] == "l@example.com"


def test_write_is_refused_when_the_file_is_a_different_vault(vault):
    """A replaced vault has a different salt, so our key does not belong to it.
    Better to fail loudly than to overwrite someone else's accounts."""
    session = cs.create_vault("pw")
    session.add_account("mine", "m@example.com", "pw")

    os.unlink(vault / "vault")
    cs.create_vault("other")

    with pytest.raises(cs.VaultCorruptError):
        session.add_account("second", "s@example.com", "pw")


# ------------------------------------------------------------- entry ids ----

def test_legacy_entries_get_a_stable_derived_id(vault):
    """Vaults written before ids existed must still be mergeable, and every
    process has to derive the same id for the same entry."""
    cs.create_vault("pw")
    data = _raw(vault)
    data["accounts"].append({"label": "legacy", "encrypted": "x"})
    with open(vault / "vault", "w", encoding="utf-8") as f:
        json.dump(data, f)

    entry = {"label": "legacy", "encrypted": "x"}
    assert cs._entry_id(entry) == cs._entry_id(dict(entry))
    assert cs._find_entry(_raw(vault), cs._entry_id(entry)) == 0


def test_rename_stamps_an_id_so_the_entry_stays_findable(vault):
    """A legacy entry's id comes from its label, so a rename would otherwise
    change its identity and strand any concurrent write."""
    session = cs.create_vault("pw")
    data = _raw(vault)
    data["accounts"].append({"label": "old-name", "encrypted": "x"})
    with open(vault / "vault", "w", encoding="utf-8") as f:
        json.dump(data, f)

    session = cs.open_vault("pw")
    before = cs._entry_id(session._data["accounts"][0])
    session.rename_account(0, "new-name")

    entry = _raw(vault)["accounts"][0]
    assert entry["label"] == "new-name"
    assert entry["id"] == before


# ----------------------------------------------------------------- locking --

def test_a_live_lock_from_another_container_is_not_broken(vault, monkeypatch):
    """The PID in another container's lock file may well name a live process
    here. Judging it by PID alone is what let one instance steal the lock."""
    lock = cs._vault_lock_path()
    with open(lock, "w", encoding="utf-8") as f:
        json.dump({"host": "some-other-container", "pid": 999999,
                   "ts": int(cs.time.time())}, f)

    assert cs._lock_is_stale(lock) is False
    with pytest.raises(TimeoutError):
        cs._acquire_vault_lock(timeout=0.2)


def test_a_dead_pid_on_this_host_is_broken_immediately(vault):
    lock = cs._vault_lock_path()
    with open(lock, "w", encoding="utf-8") as f:
        json.dump({"host": cs._host_id(), "pid": 999999,
                   "ts": int(cs.time.time())}, f)

    assert cs._lock_is_stale(lock) is True


def test_an_old_lock_is_broken_whoever_owns_it(vault):
    """The only ground available against another container: age. Without it a
    container killed mid-write would block every other instance forever."""
    lock = cs._vault_lock_path()
    with open(lock, "w", encoding="utf-8") as f:
        json.dump({"host": "gone", "pid": 1, "ts": 0}, f)
    old = cs.time.time() - cs._LOCK_STALE_SECONDS - 10
    os.utime(lock, (old, old))

    assert cs._lock_is_stale(lock) is True


def test_a_legacy_bare_pid_lock_is_handled(vault):
    """Older locks hold a bare PID and no host. Unattributable, so the age
    rule applies — but a fresh one must still be respected."""
    lock = cs._vault_lock_path()
    with open(lock, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    assert cs._lock_is_stale(lock) is False

    old = cs.time.time() - cs._LOCK_STALE_SECONDS - 10
    os.utime(lock, (old, old))
    assert cs._lock_is_stale(lock) is True


def test_host_id_is_stable_within_a_process(vault):
    assert cs._host_id() == cs._host_id()
    assert len(cs._host_id()) == 16
