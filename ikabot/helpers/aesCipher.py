#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import hashlib
import json
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ikabot.config import IKABOT_SESSIONS_DIR, DEFAULT_LOG_LEVEL
from ikabot.helpers.botComm import *
from ikabot.helpers.logging import getLogger
from ikabot.helpers.pedirInfo import read

logger = getLogger(__name__)


def _safe(s):
    return "".join(c for c in str(s) if c.isalnum() or c in "-_")


class AESCipher:

    def __init__(self, mail, password):
        if type(password) == int:
            password = str(password)
        self.key = hashlib.sha256(
            mail.encode("utf-8") + b"\x00" + password.encode("utf-8")
        ).digest()
        for i in range(0xFFF):
            self.key = hashlib.sha256(self.key).digest()

        self._entry_key = hashlib.sha256(
            "ikabot".encode("utf-8") + mail.encode("utf-8")
        ).hexdigest()
        self._prefix = self._entry_key[:16]
        self._file_path = None  # resolved lazily

    # ------------------------------------------------------------------
    # File path helpers
    # ------------------------------------------------------------------

    def _get_file_path(self, session):
        """Return the session file path for this account, creating the
        sessions directory if needed.  The filename starts with a 16-char
        email hash (so we can find it before login) and — once username /
        server / mundo are known — also carries those for readability.
        """
        if self._file_path and os.path.exists(self._file_path):
            return self._file_path

        os.makedirs(IKABOT_SESSIONS_DIR, exist_ok=True)

        # Scan for an existing file that belongs to this account.
        try:
            for name in os.listdir(IKABOT_SESSIONS_DIR):
                if name.startswith(self._prefix) and name.endswith(".session"):
                    self._file_path = os.path.join(IKABOT_SESSIONS_DIR, name)
                    return self._file_path
        except OSError:
            pass

        # No existing file — build the initial name.
        username = _safe(getattr(session, "username", "") or "")
        servidor = _safe(getattr(session, "servidor", "") or "")
        mundo    = _safe(str(getattr(session, "mundo", "") or ""))

        if username and servidor and mundo:
            name = f"{self._prefix}_{username}_{servidor}{mundo}.session"
        else:
            name = f"{self._prefix}.session"

        self._file_path = os.path.join(IKABOT_SESSIONS_DIR, name)
        return self._file_path

    def upgrade_filename(self, session):
        """Called once after login when username / servidor / mundo are set.
        Renames a hash-only file to the full human-readable name so the user
        can see which file belongs to which account+server+world.
        """
        if not self._file_path:
            return

        username = _safe(getattr(session, "username", "") or "")
        servidor = _safe(getattr(session, "servidor", "") or "")
        mundo    = _safe(str(getattr(session, "mundo", "") or ""))

        if not (username and servidor and mundo):
            return

        new_name = f"{self._prefix}_{username}_{servidor}{mundo}.session"
        new_path = os.path.join(IKABOT_SESSIONS_DIR, new_name)

        if self._file_path != new_path:
            try:
                os.rename(self._file_path, new_path)
                self._file_path = new_path
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Cross-process file locking (lock file, no extra dependencies)
    # ------------------------------------------------------------------

    @staticmethod
    def _pid_alive(pid):
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

    # How long a lock may be held before it is treated as abandoned. Also the
    # age at which a lock nobody can identify is cleared.
    LOCK_STALE_SECONDS = 30

    @staticmethod
    def _lock_age(lock_path, stamp=None):
        """Seconds since the lock was taken, by its own stamp or by the file.

        The file is created and written as two steps, so a process that dies
        between them leaves a lock with nothing in it. Before this fell back to
        the file's own timestamp, such a lock was never judged stale and never
        cleared, and the account it belonged to could not be opened again
        without deleting the file by hand.
        """
        if stamp is not None:
            try:
                return time.time() - float(stamp)
            except (TypeError, ValueError):
                pass
        try:
            return time.time() - os.path.getmtime(lock_path)
        except OSError:
            # Gone between the two calls, which means it is not held.
            return AESCipher.LOCK_STALE_SECONDS + 1

    @staticmethod
    def _lock_is_stale(lock_path):
        """True when this lock can be removed. Never raises.

        Three ways a lock stops counting: the process that took it has gone,
        it has been held longer than any write needs, or it says nothing at
        all about who holds it.
        """
        try:
            with open(lock_path, "r") as f:
                content = f.read().strip()
        except OSError:
            return False

        pid, stamp = None, None
        if content:
            parts = content.split(":", 1)
            try:
                pid = int(parts[0])
            except (TypeError, ValueError):
                pid = None
            if len(parts) > 1:
                stamp = parts[1]

        if pid is None:
            # Empty, or written by something that does not stamp it. Age is
            # all there is to go on, and the file's own timestamp gives that.
            return AESCipher._lock_age(lock_path) > AESCipher.LOCK_STALE_SECONDS

        if not AESCipher._pid_alive(pid):
            return True
        # Alive, but a pid can be reused, so age still ends it.
        return AESCipher._lock_age(lock_path, stamp) > AESCipher.LOCK_STALE_SECONDS

    @staticmethod
    def _acquire_lock(lock_path, timeout=45):
        """Take the lock, clearing one that has been abandoned.

        The timeout is longer than LOCK_STALE_SECONDS on purpose. A lock
        abandoned a moment before this call becomes stale thirty seconds in,
        and the wait has to outlast that or the one call that could have
        recovered it gives up at the same instant instead.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, f"{os.getpid()}:{time.time():.3f}".encode())
                finally:
                    os.close(fd)
                return
            except FileExistsError:
                # Check whether the lock belongs to a dead process or is stale.
                if AESCipher._lock_is_stale(lock_path):
                    try:
                        os.unlink(lock_path)
                    except (FileNotFoundError, PermissionError):
                        pass
                    continue  # retry immediately
                time.sleep(0.05)
        raise TimeoutError(f"Could not acquire session lock: {lock_path}")

    @staticmethod
    def _release_lock(lock_path):
        for _attempt in range(20):
            try:
                os.unlink(lock_path)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                # Windows: another process briefly has the lock file open for
                # reading (stale-lock check). It releases almost immediately.
                if _attempt == 19:
                    raise
                time.sleep(0.05)

    # ------------------------------------------------------------------
    # Encryption helpers
    # ------------------------------------------------------------------

    def encrypt(self, plaintext):
        aesgcm = AESGCM(self.key)
        nonce = os.urandom(16)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("utf-8")

    def decrypt(self, ciphertext):
        ciphertext = base64.b64decode(ciphertext)
        nonce = ciphertext[:16]
        ciphertext = ciphertext[16:]
        aesgcm = AESGCM(self.key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")

    # ------------------------------------------------------------------
    # Session data API
    # ------------------------------------------------------------------

    def deleteSessionData(self, session):
        file_path = self._get_file_path(session)
        for path in (file_path, file_path + ".lock"):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        self._file_path = None

    def getSessionData(self, session, all=False):
        file_path = self._get_file_path(session)

        if not os.path.exists(file_path):
            return {}

        try:
            with open(file_path, "r") as f:
                ciphertext = f.read().strip()
        except OSError:
            return {}

        if not ciphertext:
            return {}

        try:
            plaintext = self.decrypt(ciphertext)
        except Exception:
            # Main file is corrupt — try the last-known-good backup silently.
            bak_path = file_path + ".bak"
            if os.path.exists(bak_path):
                try:
                    with open(bak_path, "r") as f:
                        bak_ciphertext = f.read().strip()
                    plaintext = self.decrypt(bak_ciphertext)
                    # Restore the backup as the main file so future reads work.
                    import shutil
                    shutil.copy2(bak_path, file_path)
                    logger.warning(
                        "Session file was corrupt; restored from backup: %s", bak_path
                    )
                except Exception:
                    pass
                else:
                    # Successfully recovered — fall through with plaintext.
                    data_dict = json.loads(plaintext, strict=False)
                    if all:
                        return data_dict
                    try:
                        session_data = data_dict[session.username][session.mundo][session.servidor]
                    except (KeyError, AttributeError):
                        session_data = {}
                    try:
                        session_data["shared"] = data_dict["shared"]
                    except KeyError:
                        pass
                    return session_data

            # No usable backup either — start fresh without interrupting the user.
            logger.error(
                "Session file corrupt and no backup available; starting with empty session."
            )
            try:
                os.unlink(file_path)
            except OSError:
                pass
            return {}

        data_dict = json.loads(plaintext, strict=False)

        if all:
            return data_dict

        try:
            session_data = data_dict[session.username][session.mundo][session.servidor]
        except (KeyError, AttributeError):
            session_data = {}
        try:
            session_data["shared"] = data_dict["shared"]
        except KeyError:
            pass
        return session_data

    def setSessionData(self, session, data, shared=False):
        return self._writeSessionData(session, data, shared=shared)

    def mutateSessionData(self, session, mutator, shared=False):
        """Read-modify-write the account's session block atomically.

        setSessionData() locks only the write. Callers that read with
        getSessionData(), edit, then write back are doing an unsynchronised
        read-modify-write: two processes can both read the same snapshot and
        the second write silently discards the first one's changes. That is
        how a running background task loses its entry from processList and
        disappears from the menu's task table while still running.

        `mutator` receives the current block (a dict) and returns the block to
        store. It runs while the file lock is held, so keep it short and do no
        I/O inside it.
        """
        return self._writeSessionData(session, None, shared=shared, mutator=mutator)

    def _writeSessionData(self, session, data, shared=False, mutator=None):
        file_path = self._get_file_path(session)
        lock_path = file_path + ".lock"
        tmp_path  = file_path + ".tmp"
        bak_path  = file_path + ".bak"

        self._acquire_lock(lock_path)
        try:
            session_data = self.getSessionData(session, all=True) or {}

            if shared:
                session_data.setdefault("shared", {})
                if "logLevel" not in session_data["shared"]:
                    session_data["shared"]["logLevel"] = DEFAULT_LOG_LEVEL
                if mutator is not None:
                    data = mutator(dict(session_data["shared"]))
                session_data["shared"] = {**session_data["shared"], **data}
            else:
                username = session.username
                mundo    = session.mundo
                servidor = session.servidor
                session_data.setdefault(username, {})
                session_data[username].setdefault(mundo, {})
                session_data[username][mundo].setdefault(servidor, {})
                session_data.setdefault("shared", {})
                if mutator is not None:
                    # Re-read inside the lock so the mutator edits current
                    # state, not a snapshot taken before the lock was taken.
                    data = mutator(dict(session_data[username][mundo][servidor]))
                session_data[username][mundo][servidor] = data

            plaintext  = json.dumps(session_data)
            ciphertext = self.encrypt(plaintext)

            # Back up the current good file before overwriting it.
            if os.path.exists(file_path):
                try:
                    import shutil
                    shutil.copy2(file_path, bak_path)
                except OSError:
                    pass

            # Write atomically: temp file → fsync → rename.
            with open(tmp_path, "w") as f:
                f.write(ciphertext)
                f.flush()
                os.fsync(f.fileno())
            # On Windows a concurrent reader can briefly hold the target file
            # open, causing os.replace to fail with PermissionError (WinError 32).
            # Retry a few times — readers release almost immediately.
            for _attempt in range(20):
                try:
                    os.replace(tmp_path, file_path)
                    break
                except PermissionError:
                    if _attempt == 19:
                        raise
                    time.sleep(0.05)
        finally:
            self._release_lock(lock_path)
