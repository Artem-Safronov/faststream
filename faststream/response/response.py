from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from faststream._internal.basic_types import AnyDict


class Response:
    def __init__(
        self,
        body: Any,
        *,
        headers: Optional["AnyDict"] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Initialize a handler."""
        self.body = body
        self.headers = headers or {}
        self.correlation_id = correlation_id

    def add_headers(
        self,
        extra_headers: "AnyDict",
        *,
        override: bool = True,
    ) -> None:
        if override:
            self.headers = {**self.headers, **extra_headers}
        else:
            self.headers = {**extra_headers, **self.headers}

    def as_publish_kwargs(self) -> "AnyDict":
        return {
            "headers": self.headers,
            "correlation_id": self.correlation_id,
        }