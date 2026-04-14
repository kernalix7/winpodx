"""Structured logging configuration for winpodx."""

from __future__ import annotations

import logging
import logging.handlers


def setup_logging(level: int = logging.INFO, log_file: bool = True) -> None:
    """Configure logging with console and optional rotating file handler.

    Args:
        level: Logging level (default: INFO).
        log_file: Whether to write logs to a rotating file.
    """
    root = logging.getLogger("winpodx")
    if root.handlers:
        return  # Already configured

    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    pw_filter = PasswordFilter()

    # Console handler — stderr, WARNING+ by default
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(fmt)
    console.addFilter(pw_filter)
    root.addHandler(console)

    if log_file:
        try:
            from winpodx.utils.paths import config_dir

            log_dir = config_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "winpodx.log"

            # Set secure permissions before handler opens the file
            if not log_path.exists():
                log_path.touch(mode=0o600)
            else:
                log_path.chmod(0o600)

            file_handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=5_000_000,  # 5 MB
                backupCount=2,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(fmt)
            file_handler.addFilter(pw_filter)

            root.addHandler(file_handler)
        except OSError:
            pass  # Can't write log file — continue without it


class PasswordFilter(logging.Filter):
    """Filter that masks password values in log output."""

    _KEYWORDS = ("password", "pass", "passwd", "secret", "token")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for kw in self._KEYWORDS:
            if kw in msg.lower():
                record.msg = self._mask_value(msg)
                record.args = None
                break
        return True

    @staticmethod
    def _mask_value(text: str) -> str:
        """Replace password-like values with ***."""
        import re

        return re.sub(
            r"(password|pass|passwd|secret|token)\s*[=:]\s*\S+",
            r"\1=***",
            text,
            flags=re.IGNORECASE,
        )
