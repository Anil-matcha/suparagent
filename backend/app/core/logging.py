import logging

import structlog


def _redact_event(_logger, _method, event_dict):
    from app.services.agent_memory import redact_json

    return redact_json(event_dict)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        from app.services.agent_memory import redact_secrets

        try:
            record.msg = redact_secrets(record.getMessage())[0]
            record.args = None
        except Exception:
            pass
        return True


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(_RedactFilter())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_event,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)