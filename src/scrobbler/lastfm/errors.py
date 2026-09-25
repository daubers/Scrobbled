"""Last.fm API error codes (https://www.last.fm/api/errorcodes)."""

INVALID_SERVICE = 2
INVALID_METHOD = 3
AUTHENTICATION_FAILED = 4
INVALID_PARAMETERS = 6
OPERATION_FAILED = 8
INVALID_SESSION_KEY = 9
INVALID_API_KEY = 10
INVALID_SIGNATURE = 13
UNAUTHORIZED_TOKEN = 14
TOKEN_EXPIRED = 15
RATE_LIMIT_EXCEEDED = 29

MESSAGES = {
    INVALID_SERVICE: "Invalid service - This service does not exist",
    INVALID_METHOD: "Invalid Method - No method with that name in this package",
    AUTHENTICATION_FAILED: (
        "Authentication Failed - You do not have permissions to access the service"
    ),
    INVALID_PARAMETERS: "Invalid parameters - Your request is missing a required parameter",
    OPERATION_FAILED: "Operation failed - Something else went wrong",
    INVALID_SESSION_KEY: "Invalid session key - Please re-authenticate",
    INVALID_API_KEY: "Invalid API key - You must be granted a valid key by last.fm",
    INVALID_SIGNATURE: "Invalid method signature supplied",
    UNAUTHORIZED_TOKEN: "Unauthorized Token - This token has not been authorized",
    TOKEN_EXPIRED: "This token has expired",
    RATE_LIMIT_EXCEEDED: "Rate limit exceeded",
}

HTTP_STATUS = {
    INVALID_SERVICE: 400,
    INVALID_METHOD: 400,
    INVALID_PARAMETERS: 400,
    AUTHENTICATION_FAILED: 403,
    INVALID_SESSION_KEY: 403,
    INVALID_API_KEY: 403,
    INVALID_SIGNATURE: 403,
    UNAUTHORIZED_TOKEN: 403,
    TOKEN_EXPIRED: 403,
    RATE_LIMIT_EXCEEDED: 429,
    OPERATION_FAILED: 500,
}


class LastFMError(Exception):
    def __init__(self, code: int, message: str | None = None):
        self.code = code
        self.message = message or MESSAGES.get(code, "Unknown error")
        super().__init__(f"{code}: {self.message}")

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 400)
