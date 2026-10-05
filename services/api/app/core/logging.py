import logging

from pythonjsonlogger import jsonlogger


def configure_logging(level: str) -> None:
    """Route all logs through a single JSON-formatted handler.

    Rule: never log request bodies, file contents, or extracted health data here.
    Only metadata (ids, status, duration) belongs in a log line.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
