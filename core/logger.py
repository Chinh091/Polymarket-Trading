"""
core/logger.py
Consistent logging across all modules with file + console output.
"""
import logging
import sys
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).parent.parent / "logs"


def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"bot_{datetime.utcnow().strftime('%Y%m%d')}.log"

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler(str(log_file))
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
