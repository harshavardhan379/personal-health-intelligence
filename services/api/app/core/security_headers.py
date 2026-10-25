"""Response headers that make a browser treat the API's answers carefully (YGH-115).

Every response carries them (an existing header of the same name is kept, so the original-file
endpoint's own `Cache-Control` and `Content-Type` handling win):

  X-Content-Type-Options: nosniff     never guess a type: an uploaded file is not run as a page
  Cache-Control: no-store             health data is not left in a browser or proxy cache
  X-Frame-Options: DENY               the API is never shown inside someone else's frame
  Referrer-Policy: no-referrer
  Content-Security-Policy             the API serves data, never a page: nothing may load or run
  Strict-Transport-Security           production only (it is a promise that HTTPS is in front)

The interactive docs (development only) are a page, so the policy skips them.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def security_headers(production: bool) -> dict:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    }
    if production:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, production: bool = False):
        self.app = app
        self.headers = security_headers(production)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope.get("path", "") in DOC_PATHS

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    if is_docs and name == "Content-Security-Policy":
                        continue
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
