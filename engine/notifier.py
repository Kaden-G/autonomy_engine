"""Notification stub — sends pipeline status messages to the log.

Currently, all notifications are written to the application log.  This is a
deliberate extension point: to send alerts to Slack, email, PagerDuty, or
another service, replace or extend this module with your own integration.

The ``notifications.enabled`` flag in ``config.yml`` controls whether
messages are logged (enabled) or silently dropped (disabled).
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
