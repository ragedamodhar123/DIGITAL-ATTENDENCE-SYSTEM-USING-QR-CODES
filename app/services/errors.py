"""Errors raised by the services layer.

Services never return HTTP responses. They raise one of these, and the routes
(Module 1, part 3 onward) turn them into a consistent JSON or HTML error.
`details` maps a field name to a list of messages, for showing next to inputs.
"""


class ServiceError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self):
        body = {"error": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


class ValidationFailed(ServiceError):
    status_code = 422
    code = "validation_failed"


class AuthenticationFailed(ServiceError):
    status_code = 401
    code = "authentication_failed"


class PermissionDenied(ServiceError):
    status_code = 403
    code = "permission_denied"


class NotFound(ServiceError):
    status_code = 404
    code = "not_found"


class Conflict(ServiceError):
    status_code = 409
    code = "conflict"