"""One logging setup for the agent, the eval score, and the MCP server.

Console lines go to stderr. The MCP server speaks the protocol on stdout,
so a log line there would break the client. The same lines are appended to
var/equipment_claims.log. Set EQUIPMENT_LOG_LEVEL to change the level.
"""

from __future__ import annotations

import logging
import logging.config
import os
from pathlib import Path

_CONFIGURED = False
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def configure_logging(*, force: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED and not force:
        return
    level = os.environ.get("EQUIPMENT_LOG_LEVEL", "INFO").upper()
    log_path = Path(os.environ.get("EQUIPMENT_LOG_FILE", str(_PROJECT_ROOT / "var" / "equipment_claims.log")))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                    "datefmt": "%Y-%m-%dT%H:%M:%S",
                }
            },
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "standard",
                    "level": level,
                },
                "file": {
                    "class": "logging.FileHandler",
                    "filename": str(log_path),
                    "formatter": "standard",
                    "level": level,
                },
            },
            "loggers": {
                "equipment_claims": {
                    "handlers": ["stderr", "file"],
                    "level": level,
                    "propagate": False,
                }
            },
        }
    )
    _CONFIGURED = True
