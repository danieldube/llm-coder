"""Small dependency interfaces used by runtime orchestration."""

from collections.abc import Callable
from subprocess import CompletedProcess
from typing import Any, Protocol


class HTTPResponse(Protocol):
    status_code: int
    text: str

    def raise_for_status(self) -> None: ...
    def json(self) -> Any: ...


class HTTPTransport(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None,
        timeout: int,
    ) -> HTTPResponse: ...


CommandRunner = Callable[..., CompletedProcess[str]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class SystemdController(Protocol):
    def run(self, *args: str, check: bool = True) -> CompletedProcess[str]: ...


class RuntimeState(Protocol):
    def read_pod_id(self) -> str | None: ...
    def write_pod_id(self, pod_id: str) -> None: ...
    def forget_pod_id(self) -> None: ...
    def clear_activation_failure(self) -> None: ...
    def set_activation_status(self, message: str) -> None: ...
