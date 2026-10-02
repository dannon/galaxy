import uuid

from galaxy.web.framework.request_scope import current_request_scope


class RequestIDMiddleware:
    """
    A WSGI middleware that creates a unique ID for the request and
    puts it in the environment
    """

    def __init__(self, app, global_conf=None):
        self.app = app

    def __call__(self, environ, start_response):
        # Mounted under the ASGI app, reuse its request id so this request's log lines,
        # access line and audit events can be matched with one id.
        scope = current_request_scope()
        environ["request_id"] = (scope.request_id if scope is not None else None) or uuid.uuid1().hex
        return self.app(environ, start_response)
