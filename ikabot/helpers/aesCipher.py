#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import hashlib
import json
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ikabot.config import IKABOT_SESSIONS_DIR
from ikabot.helpers.botComm import *
from ikabot.helpers.pedirInfo import read


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
    def _acquire_lock(lock_path, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                time.sleep(0.05)
        raise TimeoutError(f"Could not acquire session lock: {lock_path}")

    @staticmethod
    def _release_lock(lock_path):
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass

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
            msg = "Error while decrypting session data.\nYou may have entered a wrong password."
            if session.padre:
                print(msg)
            else:
                sendToBot(session, msg)
            print("\nWould you like to delete the ikabot session data associated with this email address? [y/N]")
            rta = read(values=["n", "N", "y", "Y"])
            if rta.lower() == "n":
                os._exit(0)
            self.deleteSessionData(session)
            os._exit(0)

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
        file_path = self._get_file_path(session)
        lock_path = file_path + ".lock"

        self._acquire_lock(lock_path)
        try:
            session_data = self.getSessionData(session, all=True) or {}

            if shared:
                session_data.setdefault("shared", {})
                if "logLevel" not in session_data["shared"]:
                    session_data["shared"]["logLevel"] = 2
                session_data["shared"] = {**session_data["shared"], **data}
            else:
                username = session.username
                mundo    = session.mundo
                servidor = session.servidor
                session_data.setdefault(username, {})
                session_data[username].setdefault(mundo, {})
                session_data[username][mundo].setdefault(servidor, {})
                session_data.setdefault("shared", {})
                session_data[username][mundo][servidor] = data

            plaintext  = json.dumps(session_data)
            ciphertext = self.encrypt(plaintext)

            with open(file_path, "w") as f:
                f.write(ciphertext)
                f.flush()
        finally:
            self._release_lock(lock_path)
