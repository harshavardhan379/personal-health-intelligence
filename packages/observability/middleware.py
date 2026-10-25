"""One wrapper around every HTTP request (YGH-90, 91, 93).

It adopts the caller's X-Request-ID when it is a plain token (the web app sends one, so a reference
shown to a user is the same id found in the server's logs) or makes one, puts it on the response,
times the request, records the time under the route template, and writes one `request_finished`
log line: method, route template, status, duration. Never the path's ids, query string, body or
headers.

A pure ASGI middleware, not BaseHTTPMiddleware: the latter runs the endpoint in a separate task, and
the request id would not follow into it.
"""

import logging
import time

from starlette.datastructures import MutableHeaders

from packages.observability.context import HEADER, request_id_from, set_request_id
from packages.observability.metrics import registry, request_metric, route_template

logger = logging.getLogger("app.request")

SLOW_REQUEST_MS = 5000.0


class RequestContextMiddleware:
    def __init__(self, app, slow_ms: float = SLOW_REQUEST_MS):
        self.app = app
        self.slow_ms = slow_ms

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(HEADER.lower().encode(), b"")
        request_id = request_id_from(incoming.decode("latin-1"))
        set_request_id(request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        started = time.perf_counter()
        outcome = {"status": 500}

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                outcome["status"] = message["status"]
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            milliseconds = (time.perf_counter() - started) * 1000
            template = route_template(scope.get("path", ""), scope.get("path_params"))
            status = outcome["status"]
            registry.observe(request_metric(scope["method"], template), milliseconds,
                             ok=status < 500)
            slow = milliseconds >= self.slow_ms
            logger.log(
                logging.WARNING if status >= 500 or slow else logging.INFO,
                "request_finished",
                extra={"method": scope["method"], "route": template, "status": status,
                       "duration_ms": round(milliseconds, 1), "slow": slow},
            )
