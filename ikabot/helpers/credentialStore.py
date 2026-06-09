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


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is currently running."""
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


def _acquire_vault_lock(timeout: float = 15.0) -> None:
    """Acquire an exclusive write-lock on the vault file.

    Writes the current PID into the lock file so stale locks (left by
    crashed processes) are detected and removed automatically.
    """
    lock_path = _vault_lock_path()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            return
        except FileExistsError:
            try:
                with open(lock_path, "r") as f:
                    pid_str = f.read().strip()
                if pid_str and not _pid_alive(int(pid_str)):
                    os.unlink(lock_path)
                    continue
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
    raise TimeoutError(f"Could not acquire vault lock: {lock_path}")


def _release_vault_lock() -> None:
    try:
        os.unlink(_vault_lock_path())
    except FileNotFoundError:
        pass


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
    ) -> None:
        """Encrypt and append a new account, then save."""
        payload = {
            "email": email,
            "password": password,
            "blackbox": blackbox,
            "lobby_token": lobby_token,
        }
        encrypted = _encrypt(self._key, payload)
        self._data["accounts"].append({"label": label, "encrypted": encrypted})
        self._save()

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
        entries = self._data["accounts"]
        if index < 0 or index >= len(entries):
            return
        creds = _decrypt(self._key, entries[index]["encrypted"])
        if blackbox is not None:
            creds["blackbox"] = blackbox
        if lobby_token is not None:
            creds["lobby_token"] = lobby_token
        entries[index]["encrypted"] = _encrypt(self._key, creds)
        self._save()

    def rename_account(self, index: int, new_label: str) -> None:
        """Rename the label of account at *index* and save."""
        entries = self._data["accounts"]
        if index < 0 or index >= len(entries):
            raise IndexError(f"No vault account at index {index}.")
        entries[index]["label"] = new_label
        self._save()

    def remove_account(self, index: int) -> None:
        """Remove account by index and save."""
        entries = self._data["accounts"]
        if index < 0 or index >= len(entries):
            raise IndexError(f"No vault account at index {index}.")
        del entries[index]
        self._save()

    def change_master_password(self, new_master_pw: str) -> None:
        """Re-derive key from new password (same salt), re-encrypt all accounts, save."""
        salt_bytes = bytes.fromhex(self._data["salt"])
        new_key = _derive_key(new_master_pw, salt_bytes)
        new_accounts = []
        for entry in self._data["accounts"]:
            creds = _decrypt(self._key, entry["encrypted"])
            new_accounts.append({
                "label": entry["label"],
                "encrypted": _encrypt(new_key, creds),
            })
        self._data["accounts"] = new_accounts
        self._key = new_key
        self._save()

    # ------------------------------------------------------------------ #
    # Private
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        _acquire_vault_lock()
        try:
            _atomic_write(self._path, self._data)
        finally:
            _release_vault_lock()


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------

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
