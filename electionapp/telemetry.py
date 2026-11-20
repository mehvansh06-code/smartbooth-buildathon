"""
Telemetry and Production Error Tracking (Sentry Integration)
Enables automated crash reporting from live users to GitHub Issues for Google Jules triage.
"""
import os
import logging

logger = logging.getLogger(__name__)


def init_sentry(app_name: str = "smartbooth") -> None:
    """
    Initialize Sentry error tracking if SENTRY_DSN is configured.
    Guarantees:
      - Graceful no-op if SENTRY_DSN is unset (safe for dev and CI).
      - PII is strictly disabled (send_default_pii=False).
      - Filters out expected 404s and client disconnects.
    """
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        logging_integration = LoggingIntegration(
            level=logging.INFO,
            event_level=logging.ERROR,
        )

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration(), logging_integration],
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            environment=os.environ.get("FLASK_ENV", "development"),
            release=f"{app_name}@2026.10",
            send_default_pii=False,
            ignore_errors=[
                "NotFound",
                "ClientDisconnected",
            ],
        )
        logger.info("[Telemetry] Sentry error tracking initialized for %s (%s)", app_name, os.environ.get("FLASK_ENV"))
    except ImportError:
        logger.debug("[Telemetry] sentry-sdk not installed; skipping Sentry initialization.")
    except Exception as e:
        logger.error("[Telemetry] Failed to initialize Sentry: %s", e)
