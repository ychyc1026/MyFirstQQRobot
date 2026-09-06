"""Domain-level errors that do not depend on NapCat or FastAPI."""


class IgnoredEvent(ValueError):
    """Raised when an event is valid but outside the configured message boundary."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NapCatRequestError(RuntimeError):
    """Raised when NapCat returns a confirmed rejection for an outbound action."""


class NapCatDeliveryUnknownError(RuntimeError):
    """Raised when a send may have reached NapCat but no outcome was confirmed."""
