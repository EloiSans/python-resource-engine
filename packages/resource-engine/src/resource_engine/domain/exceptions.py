from __future__ import annotations


class DomainError(Exception):
    """A business rule was violated.

    Raise it (or a subclass) from domain code, hooks or actions. Adapters map it to
    a client error (HTTP 422 by default). ``code`` is optional and free-form.
    """

    code: str = "DOMAIN"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}
