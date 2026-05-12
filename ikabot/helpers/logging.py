"""
Ikabot logging setup.

The file handler is NOT created at import time because the username and server
are not known until after login. Call setup_file_logging() once the session is
established. Until then all log output goes to stderr.
"""

import logging
import logging.handlers
import os

from ikabot.config import LOGS_DIRECTORY, DEFAULT_LOG_LEVEL

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_file_logging_configured = False


class IkabotLogger(logging.Logger):
    pass


logging.setLoggerClass(IkabotLogger)

# Bootstrap: stderr only until setup_file_logging() is called.
logging.basicConfig(
    format=_LOG_FORMAT,
    level=DEFAULT_LOG_LEVEL,
    force=True,
    handlers=[logging.StreamHandler()],
)

for name in logging.root.manager.loggerDict:
    logging.getLogger(name).propagate = True
    logging.getLogger(name).handlers.clear()


def setup_file_logging(username: str, server: str, mundo: str) -> None:
    """Switch the root logger from stderr to a per-instance rotating log file.

    Safe to call multiple times; subsequent calls are no-ops.

    Parameters
    ----------
    username : str   Player name on the game server.
    server   : str   Server language code (e.g. 'en').
    mundo    : str   World / server number (e.g. '1').
    """
    global _file_logging_configured
    if _file_logging_configured:
        return

    os.makedirs(LOGS_DIRECTORY, exist_ok=True)

    safe_username = "".join(c for c in username if c.isalnum() or c in "-_")
    safe_server = "".join(c for c in server if c.isalnum() or c in "-_")
    safe_mundo = "".join(c for c in str(mundo) if c.isalnum() or c in "-_")
    filename = os.path.join(
        LOGS_DIRECTORY,
        f"ikabot_{safe_username}_{safe_server}{safe_mundo}.log",
    )

    handler = logging.handlers.RotatingFileHandler(
        filename=filename,
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=10,
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    # Remove the bootstrap stderr handler and add the file handler.
    root.handlers.clear()
    root.addHandler(handler)

    _file_logging_configured = True


def get_log_file_path() -> str | None:
    """Return the current log file path, or None if file logging is not yet set up."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            return handler.baseFilename
    return None


def getLogger(name: str) -> IkabotLogger:
    """Convenience function to get a logger by name."""
    return logging.getLogger(name)
