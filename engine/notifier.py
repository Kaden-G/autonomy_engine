"""Notification via Python logging.

All notifications are emitted as log messages.  To send real alerts
(Slack, email, PagerDuty, etc.) replace or extend this module.

The ``notifications.enabled`` flag in ``config.yml`` controls whether
messages are logged at INFO (enabled) or suppressed (disabled).
"""

import logging

import yaml

from engine.context import get_config_path

logger = logging.getLogger(__name__)


def notify(message: str, config_path: str | None = None) -> None:
    """Log a notification message if notifications are enabled in config."""
    if config_path is None:
        config_path = str(get_config_path())
    with open(config_path) as f:
        config = yaml.safe_load(f)

    notif = config.get("notifications", {})

    if not notif.get("enabled", False):
        logger.debug("Notification (disabled): %s", message)
        return

    logger.info("Notification: %s", message)
