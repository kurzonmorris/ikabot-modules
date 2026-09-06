"""
Credential vault for ikabot.

Stores game account credentials (email, password, blackbox token, lobby
cookie) encrypted under a master-password-derived PBKDF2 key. The vault
file lives inside the .ikabot data directory alongside sessions and logs.

Master password is NEVER written to disk. Wrong password is detected
automatically by AES-GCM authentication-tag failure on first decrypt.
"""

import base64
import hashlib
import json
import os
import re
import time
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ikabot.config import isWindows, IKABOT_DATA_DIR

_VAULT_VERSION = 1
_PBKDF2_ITERATIONS = 200_000
_VAULT_LOCATION_FILE = os.path.join(IKABOT_DATA_DIR, "vault_location.conf")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class VaultWrongPasswordError(Exception):
    """Raised when the master password is incorrect (AES-GCM tag mismatch)."""


class VaultCorruptError(Exception):
    """Raised when the vault file exists but cannot be parsed."""


class VaultVersionError(Exception):
    """Raised when the vault file uses an unsupported version number."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_custom_vault_dir() -> str:
    """Return the custom vault directory from config, or None if not set."""
    try:
        if os.path.isfile(_VAULT_LOCATION_FILE):
            with open(_VAULT_LOCATION_FILE, "r", encoding="utf-8") as f:
                path = f.read().strip()
            if path:
                return path
    except OSError:
        pass
    return None


def get_vault_location() -> str:
    """Return the directory where the vault file is (or will be) stored."""
    custom = _get_custom_vault_dir()
    return custom if custom else IKABOT_DATA_DIR


def set_vault_location(new_dir: str) -> str:
    """Move the vault to new_dir and persist the new location.

    Returns a human-readable status string describing what happened.

    If new_dir is empty or matches the default, the override config is removed
    (resetting to the default location).  The existing vault file is moved if
    present.  Raises OSError on filesystem errors.  Raises FileExistsError if
    a vault file already exists at the destination (to prevent silent overwrite).
    """
    import shutil
    new_dir = os.path.abspath(os.path.expanduser(new_dir.strip())) if new_dir.strip() else ""
    default_dir = IKABOT_DATA_DIR

    old_path = _vault_path()
    vault_exists_at_source = os.path.isfile(old_path)

    if not new_dir or os.path.normcase(new_dir) == os.path.normcase(default_dir):
        # Reset to default — remove override config if present.
        new_vault_path = os.path.join(default_dir, "vault")
        if vault_exists_at_source and os.path.normcase(old_path) != os.path.normcase(new_vault_path):
            if os.path.isfile(new_vault_path):
                raise FileExistsError(
                    f"A vault file already exists at {new_vault_path}. "
                    "Remove it manually before resetting the location."
                )
            os.makedirs(default_dir, exist_ok=True)
            shutil.copy2(old_path, new_vault_path)
            os.unlink(old_path)
            status = f"Vault moved from {old_path} to {new_vault_path}"
        elif vault_exists_at_source:
            status = "Vault location reset to default (vault already in default location)"
        else:
            status = "Vault location reset to default (vault will be created here when needed)"
        try:
            os.unlink(_VAULT_LOCATION_FILE)
        except FileNotFoundError:
            pass
        return status

    os.makedirs(new_dir, exist_ok=True)
    new_vault_path = os.path.join(new_dir, "vault")

    if vault_exists_at_source and os.path.normcase(old_path) != os.path.normcase(new_vault_path):
        if os.path.isfile(new_vault_path):
            raise FileExistsError(
                f"A vault file already exists at {new_vault_path}. "
                "Remove it manually before changing the location."
            )
        shutil.copy2(old_path, new_vault_path)
        os.unlink(old_path)
        status = f"Vault moved from {old_path} to {new_vault_path}"
    elif vault_exists_at_source:
        status = "Location unchanged (vault is already at that path)"
    else:
        status = f"Vault location set to {new_dir} (vault will be created here when needed)"

    os.makedirs(IKABOT_DATA_DIR, exist_ok=True)
    with open(_VAULT_LOCATION_FILE, "w", encoding="utf-8") as f:
        f.write(new_dir)
    return status


def _vault_path() -> str:
    """Return the vault file path (custom location or default data directory)."""
    base = _get_custom_vault_dir() or IKABOT_DATA_DIR
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "vault")


def _legacy_vault_path() -> str:
    """Return the old vault location at the home directory root."""
    if isWindows:
        base = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~")
    return os.path.join(base, ".ikabot_vault")


def _migrate_vault_if_needed() -> None:
    """Move the vault from the old home-directory location to the new data dir.

    Called once on every startup via vault_exists(). Safe to call repeatedly.
    """
    old = _legacy_vault_path()
    new = _vault_path()
    if os.path.isfile(old) and not os.path.isfile(new):
        try:
            os.makedirs(IKABOT_DATA_DIR, exist_ok=True)
            os.rename(old, new)
        except OSError:
            pass
    # Clean up any stale legacy lock/tmp files left at the old location.
    for suffix in (".lock", ".tmp"):
        try:
            os.unlink(old + suffix)
        except FileNotFoundError:
            pass


def _derive_key(master_pw: str, salt_bytes: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        master_pw.encode("utf-8"),
        salt_bytes,
        _PBKDF2_ITERATIONS,
    )


def _encrypt(key: bytes, plaintext_dict: dict) -> str:
    """Encrypt a dict to a base64 AES-256-GCM string."""
    plaintext = json.dumps(plaintext_dict).encode("utf-8")
    nonce = os.urandom(16)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ciphertext).decode("utf-8")


def _decrypt(key: bytes, encrypted_b64: str) -> dict:
    """Decrypt a base64 AES-256-GCM string to a dict.

    Raises VaultWrongPasswordError if the authentication tag does not match.
    """
    raw = base64.b64decode(encrypted_b64)
    nonce, ciphertext = raw[:16], raw[16:]
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag:
        raise VaultWrongPasswordError(
            "Wrong master password or vault data is corrupt."
        )
    return json.loads(plaintext.decode("utf-8"))


def _host_id() -> str:
    """Return an identifier for this machine *or container*.

    PIDs are only comparable within one PID namespace, so a lock written by a
    different container must never be judged by whether "that PID" is alive
    here — under Docker the same PID number is routinely in use by an unrelated
    process in every container.  Pairing the PID with a host id makes the
    liveness check apply only where it is meaningful.

    Docker sets the hostname to the container id by default, so this is
    naturally distinct per container.
    """
    global _HOST_ID_CACHE
    if _HOST_ID_CACHE is None:
        parts = []
        try:
            parts.append(os.uname().nodename)
        except AttributeError:  # Windows
            parts.append(os.environ.get("COMPUTERNAME", ""))
        # Distinguishes containers that were given the same hostname.
        for path in ("/etc/machine-id", "/proc/sys/kernel/random/boot_id"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    parts.append(f.read().strip())
                break
            except OSError:
                continue
        _HOST_ID_CACHE = hashlib.sha256(
            "|".join(parts).encode("utf-8")).hexdigest()[:16]
    return _HOST_ID_CACHE


_HOST_ID_CACHE = None


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is currently running.

    Only meaningful for a PID from this machine/container — see _host_id().
    """
    if pid == os.getpid():
        return True
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong(259)  # 259 = STILL_ACTIVE
                ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code))
                return exit_code.value == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False


def _vault_lock_path() -> str:
    return _vault_path() + ".lock"


# A vault write is a decrypt/re-encrypt plus one atomic replace — well under a
# second. A lock older than this was left behind by a process that died, or by
# a container that was killed, so it is safe to break.
_LOCK_STALE_SECONDS = 60.0


def _lock_is_stale(lock_path: str) -> bool:
    """Return True if an existing lock file can safely be broken.

    Two independent grounds:

    * the owner is on *this* host and its PID is gone — immediate and certain;
    * the lock file is older than _LOCK_STALE_SECONDS — the only thing we can
      check for an owner in another container, whose PIDs we cannot inspect.

    Age comes from the file's mtime rather than a timestamp written inside it,
    so a lock left by an older ikabot (which wrote a bare PID) is handled too.
    """
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return False  # vanished — the retry loop will just try to create it
    except OSError:
        raw = ""

    owner = None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                owner = parsed
        except ValueError:
            # Older format: a bare PID, with no host recorded. Cannot be
            # attributed to a host, so it is subject to the age rule only.
            pass

    if owner and owner.get("host") == _host_id():
        try:
            if not _pid_alive(int(owner["pid"])):
                return True
        except (KeyError, TypeError, ValueError):
            pass

    try:
        return (time.time() - os.path.getmtime(lock_path)) > _LOCK_STALE_SECONDS
    except OSError:
        return False


def _acquire_vault_lock(timeout: float = 15.0) -> None:
    """Acquire an exclusive write-lock on the vault file.

    Records the owning host *and* PID, so a lock held by a live process in
    another container is never mistaken for a stale one left by a dead process
    here — see _host_id().
    """
    lock_path = _vault_lock_path()
    deadline = time.monotonic() + timeout
    token = json.dumps(
        {"host": _host_id(), "pid": os.getpid(), "ts": int(time.time())}
    ).encode("utf-8")
    while time.monotonic() < deadline:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, token)
            finally:
                os.close(fd)
            return
        except FileExistsError:
            if _lock_is_stale(lock_path):
                try:
                    os.unlink(lock_path)
                except OSError:
                    pass
                continue
            time.sleep(0.05)
    raise TimeoutError(f"Could not acquire vault lock: {lock_path}")


def _release_vault_lock() -> None:
    try:
        os.unlink(_vault_lock_path())
    except FileNotFoundError:
        pass


def _entry_id(entry: dict) -> str:
    """Return a stable identifier for a vault entry.

    Writes merge into whatever is on disk, so an entry has to be findable by
    something better than its position — another process may have inserted or
    removed accounts since this session was opened.

    Accounts created since this change carry an explicit random ``id``.  Older
    entries have none, so one is derived from the label.  The derivation is
    deterministic, which is what matters: every process independently arrives
    at the same id for the same legacy entry.  Mutating a legacy entry stamps
    the derived value as an explicit ``id`` so it survives a later rename.
    """
    existing = entry.get("id")
    if existing:
        return existing
    label = entry.get("label") or ""
    return "L" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:15]


def _find_entry(data: dict, entry_id: str):
    """Return the index of *entry_id* in *data*, or None if it is gone."""
    for i, entry in enumerate(data.get("accounts", [])):
        if _entry_id(entry) == entry_id:
            return i
    return None


def _atomic_write(path: str, data: dict) -> None:
    """Write JSON to path atomically via a temp file, with 0o600 permissions.

    Must be called while holding the vault lock (_acquire_vault_lock).
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# VaultSession
# ---------------------------------------------------------------------------

class VaultSession:
    """
    An open vault. Holds the derived key in memory; never writes the master
    password or the key to disk.
    """

    def __init__(self, vault_path: str, vault_data: dict, key: bytes):
        self._path = vault_path
        self._data = vault_data
        self._key = key

    # ------------------------------------------------------------------ #
    # Public read API
    # ------------------------------------------------------------------ #

    def verify_password(self) -> bool:
        """Confirm the derived key is correct by decrypting the first account.

        Returns True if the password is correct or the vault is empty.
        Returns False if decryption fails (wrong master password).
        """
        if not self._data["accounts"]:
            return True
        try:
            _decrypt(self._key, self._data["accounts"][0]["encrypted"])
            return True
        except VaultWrongPasswordError:
            return False

    def list_accounts(self) -> list:
        """Return [(index, label), ...] sorted in natural order (1, 2, 10 not 1, 10, 2)."""
        def _natural_key(item):
            return [int(c) if c.isdigit() else c.lower()
                    for c in re.split(r'(\d+)', item[1])]
        accounts = [(i, a["label"]) for i, a in enumerate(self._data["accounts"])]
        return sorted(accounts, key=_natural_key)

    def get_credentials(self, index: int) -> dict:
        """Return the decrypted account dict for *index*.

        Dict keys: email, password, blackbox (may be None), lobby_token (may be None).
        Raises IndexError or VaultWrongPasswordError.
        """
        entries = self._data["accounts"]
        if index < 0 or index >= len(entries):
            raise IndexError(f"No vault account at index {index}.")
        return _decrypt(self._key, entries[index]["encrypted"])

    # ------------------------------------------------------------------ #
    # Public write API
    # ------------------------------------------------------------------ #

    def add_account(
        self,
        label: str,
        email: str,
        password: str,
        blackbox: str = None,
        lobby_token: str = None,
        locale: str = None,
        timezone_id: str = None,
    ) -> None:
        """Encrypt and append a new account, then save.

        *locale* and *timezone_id* are the account's regional fingerprint. When
        omitted the global config defaults apply at login time.
        """
        payload = {
            "email": email,
            "password": password,
            "blackbox": blackbox,
            "lobby_token": lobby_token,
            "locale": locale,
            "timezone_id": timezone_id,
        }
        entry = {
            "id": uuid.uuid4().hex,
            "label": label,
            "encrypted": _encrypt(self._key, payload),
        }
        self._mutate(lambda disk: disk["accounts"].append(entry))

    def update_tokens(
        self,
        index: int,
        blackbox: str = None,
        lobby_token: str = None,
    ) -> None:
        """Refresh the stored blackbox and/or lobby token for *index* and save.

        Only fields that are not None are updated; existing values are kept for
        any field passed as None.
        """
        target = self._entry_id_at(index)
        if target is None:
            return

        def _apply(disk):
            i = _find_entry(disk, target)
            if i is None:
                return  # removed by another process; nothing to refresh
            entry = disk["accounts"][i]
            creds = _decrypt(self._key, entry["encrypted"])
            if blackbox is not None:
                creds["blackbox"] = blackbox
            if lobby_token is not None:
                creds["lobby_token"] = lobby_token
            entry["encrypted"] = _encrypt(self._key, creds)
            entry["id"] = target

        self._mutate(_apply)

    def get_region(self, index: int) -> tuple:
        """Return (locale, timezone_id) for *index*; either may be None.

        None means the account has no explicit region and falls back to the
        global config defaults at login time.
        """
        creds = self.get_credentials(index)
        return creds.get("locale"), creds.get("timezone_id")

    def set_region(self, index: int, locale: str, timezone_id: str) -> None:
        """Set the regional fingerprint for *index* and save.

        The stored blackbox token is generated for a specific locale/timezone
        and is sent to Gameforge alongside them, so a cached token from the old
        region would contradict the new one — exactly the mismatch that gets
        logins rejected.  Clear it and let the next login mint a fresh token.
        """
        target = self._entry_id_at(index)
        if target is None:
            return

        def _apply(disk):
            i = _find_entry(disk, target)
            if i is None:
                return
            entry = disk["accounts"][i]
            creds = _decrypt(self._key, entry["encrypted"])
            changed = (creds.get("locale") != locale
                       or creds.get("timezone_id") != timezone_id)
            creds["locale"] = locale
            creds["timezone_id"] = timezone_id
            if changed:
                creds["blackbox"] = None
            entry["encrypted"] = _encrypt(self._key, creds)
            entry["id"] = target

        self._mutate(_apply)

    def rename_account(self, index: int, new_label: str) -> None:
        """Rename the label of account at *index* and save."""
        target = self._require_entry_id_at(index)

        def _apply(disk):
            i = _find_entry(disk, target)
            if i is None:
                raise IndexError(
                    "That account is no longer in the vault "
                    "(it was removed by another ikabot instance)."
                )
            # Stamp the id before the label changes: a legacy entry's id is
            # derived from its label, so renaming without this would make the
            # entry unfindable by any process holding the old id.
            disk["accounts"][i]["id"] = target
            disk["accounts"][i]["label"] = new_label

        self._mutate(_apply)

    def remove_account(self, index: int) -> None:
        """Remove account by index and save."""
        target = self._require_entry_id_at(index)

        def _apply(disk):
            i = _find_entry(disk, target)
            if i is not None:
                del disk["accounts"][i]

        self._mutate(_apply)

    def change_master_password(self, new_master_pw: str) -> None:
        """Re-derive key from new password (same salt), re-encrypt all accounts, save."""
        salt_bytes = bytes.fromhex(self._data["salt"])
        new_key = _derive_key(new_master_pw, salt_bytes)

        def _apply(disk):
            # Re-key whatever is on disk, not the snapshot this session opened:
            # an account another instance added since must be re-encrypted too,
            # or it would be unreadable under the new password.
            for entry in disk["accounts"]:
                creds = _decrypt(self._key, entry["encrypted"])
                entry["encrypted"] = _encrypt(new_key, creds)

        self._mutate(_apply)
        self._key = new_key

    # ------------------------------------------------------------------ #
    # Private
    # ------------------------------------------------------------------ #

    def _entry_id_at(self, index: int):
        """Return the stable id of the account at *index*, or None if invalid."""
        entries = self._data["accounts"]
        if index < 0 or index >= len(entries):
            return None
        return _entry_id(entries[index])

    def _require_entry_id_at(self, index: int) -> str:
        target = self._entry_id_at(index)
        if target is None:
            raise IndexError(f"No vault account at index {index}.")
        return target

    def _read_for_merge(self) -> dict:
        """Re-read the vault from disk so a write merges instead of clobbering.

        Must be called while holding the vault lock.  Falls back to the
        in-memory snapshot when the file cannot be read, which is the same
        behaviour as before this existed.
        """
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                disk = json.load(f)
        except (OSError, ValueError):
            return self._data
        if not isinstance(disk, dict) or disk.get("version") != _VAULT_VERSION:
            return self._data
        if disk.get("salt") != self._data.get("salt"):
            # A different vault entirely — our key does not belong to it, and
            # writing would replace someone else's accounts with ours.
            raise VaultCorruptError(
                "The vault file has been replaced by a different vault since "
                "it was opened. Refusing to write over it."
            )
        disk.setdefault("accounts", [])
        return disk

    def _mutate(self, apply_change) -> None:
        """Apply *apply_change* to the vault, atomically, under the lock.

        The read, the change and the write all happen inside one lock hold, so
        a concurrent writer cannot have its change silently discarded.  Writing
        ``self._data`` — a snapshot taken at open_vault() time — would do
        exactly that, and across containers sharing one data directory it is
        how a whole account list gets rolled back to an older state.
        """
        _acquire_vault_lock()
        try:
            disk = self._read_for_merge()
            apply_change(disk)
            _atomic_write(self._path, disk)
            self._data = disk
        finally:
            _release_vault_lock()


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------

def backup_vault(dest_dir: str) -> str:
    """Copy the encrypted vault to *dest_dir*, timestamped. Returns the path.

    A straight file copy: the backup stays AES-GCM encrypted and is only
    readable with the same master password, so it is safe to keep anywhere the
    original would be safe. Restore by copying it back over the vault file.
    """
    import shutil

    src = _vault_path()
    if not os.path.isfile(src):
        raise FileNotFoundError("No vault to back up.")

    dest_dir = os.path.expanduser(dest_dir.strip())
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(dest_dir, f"vault-backup-{stamp}")

    _acquire_vault_lock()
    try:
        shutil.copy2(src, dest)
    finally:
        _release_vault_lock()

    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return dest


def vault_exists() -> bool:
    """Return True if an ikabot vault file is present."""
    _migrate_vault_if_needed()
    return os.path.isfile(_vault_path())


def create_vault(master_pw: str) -> VaultSession:
    """Create a new empty vault and return an open VaultSession.

    Raises FileExistsError if the vault already exists.
    """
    path = _vault_path()
    if os.path.isfile(path):
        raise FileExistsError(f"Vault already exists at {path}.")
    salt_bytes = os.urandom(32)
    vault_data = {
        "version": _VAULT_VERSION,
        "salt": salt_bytes.hex(),
        "accounts": [],
    }
    _atomic_write(path, vault_data)
    key = _derive_key(master_pw, salt_bytes)
    return VaultSession(path, vault_data, key)


def open_vault(master_pw: str) -> VaultSession:
    """Open and parse the vault file, derive the key from master_pw + stored salt.

    Key correctness is proven lazily on the first get_credentials() call via
    AES-GCM tag verification.

    Raises VaultCorruptError or VaultVersionError on structural problems.
    """
    path = _vault_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            vault_data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise VaultCorruptError(f"Vault file unreadable or corrupt: {exc}")
    version = vault_data.get("version")
    if version != _VAULT_VERSION:
        raise VaultVersionError(f"Unsupported vault version: {version!r}")
    salt_bytes = bytes.fromhex(vault_data["salt"])
    key = _derive_key(master_pw, salt_bytes)
    return VaultSession(path, vault_data, key)
