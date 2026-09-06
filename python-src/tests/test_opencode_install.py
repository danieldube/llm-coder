import hashlib
import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import requests
from llm_coding import opencode
from llm_coding.config import Settings


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status_error: requests.RequestException | None = None,
        content_length: int | None = None,
    ) -> None:
        self.body = body
        self.status_error = status_error
        self.headers = {
            'Content-Length': str(
                len(body) if content_length is None else content_length
            )
        }

    def __enter__(self) -> 'FakeResponse':
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_error:
            raise self.status_error

    def iter_content(self, _size: int) -> list[bytes]:
        return [self.body]


def archive_with(name: str = 'opencode', body: bytes = b'executable') -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as bundle:
        info = tarfile.TarInfo(name)
        info.size = len(body)
        bundle.addfile(info, io.BytesIO(body))
    return output.getvalue()


class OpenCodeInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.binary = self.home / '.opencode/bin/opencode'
        self.config = cast(
            Settings, SimpleNamespace(opencode_version='test-version')
        )
        self.body = archive_with()
        artifact = opencode._ReleaseArtifact(
            'opencode-linux-x64.tar.gz', hashlib.sha256(self.body).hexdigest()
        )
        self.patches = (
            patch.object(Path, 'home', return_value=self.home),
            patch('llm_coding.opencode.platform.system', return_value='Linux'),
            patch(
                'llm_coding.opencode.platform.machine', return_value='x86_64'
            ),
            patch.dict(
                opencode._RELEASE_ARTIFACTS,
                {'test-version': {('Linux', 'x86_64'): artifact}},
            ),
        )
        for item in cast(tuple[Any, ...], self.patches):
            item.start()
            self.addCleanup(item.stop)

    def runner(self, version: str = 'test-version') -> Mock:
        return Mock(
            return_value=subprocess.CompletedProcess([], 0, version + '\n', '')
        )

    def test_successfully_installs_verified_release(self) -> None:
        run = self.runner()
        with patch(
            'llm_coding.opencode.requests.get',
            return_value=FakeResponse(self.body),
        ) as get:
            opencode.ensure_installed(self.config, run)

        assert self.binary.read_bytes() == b'executable'
        assert self.binary.stat().st_mode & 0o777 == 0o755
        assert get.call_args.kwargs['allow_redirects']
        assert get.call_args.kwargs['timeout'] == (10, 120)

    def test_rejects_http_error_without_replacing_binary(self) -> None:
        self._existing_binary()
        response = FakeResponse(
            b'', status_error=requests.HTTPError('secret response body')
        )
        with patch('llm_coding.opencode.requests.get', return_value=response):
            self._assert_error(
                RuntimeError,
                'Unable to download',
                lambda: opencode.ensure_installed(
                    self.config, self.runner('old')
                ),
            )
        assert self.binary.read_bytes() == b'working'

    def test_rejects_truncated_download(self) -> None:
        with patch(
            'llm_coding.opencode.requests.get',
            return_value=FakeResponse(
                self.body, content_length=len(self.body) + 1
            ),
        ):
            self._assert_error(
                RuntimeError,
                'truncated',
                lambda: opencode.ensure_installed(self.config, self.runner()),
            )
        assert not self.binary.exists()

    def test_rejects_checksum_mismatch(self) -> None:
        with patch(
            'llm_coding.opencode.requests.get',
            return_value=FakeResponse(self.body + b'x'),
        ):
            self._assert_error(
                RuntimeError,
                'checksum',
                lambda: opencode.ensure_installed(self.config, self.runner()),
            )

    def test_rejects_malformed_archive(self) -> None:
        malformed = b'not an archive'
        artifact = opencode._ReleaseArtifact(
            'opencode-linux-x64.tar.gz', hashlib.sha256(malformed).hexdigest()
        )
        with (
            patch.dict(
                opencode._RELEASE_ARTIFACTS,
                {'test-version': {('Linux', 'x86_64'): artifact}},
            ),
            patch(
                'llm_coding.opencode.requests.get',
                return_value=FakeResponse(malformed),
            ),
        ):
            self._assert_error(
                RuntimeError,
                'malformed',
                lambda: opencode.ensure_installed(self.config, self.runner()),
            )

    def test_rejects_malicious_archive_path(self) -> None:
        malicious = archive_with('../opencode')
        artifact = opencode._ReleaseArtifact(
            'opencode-linux-x64.tar.gz', hashlib.sha256(malicious).hexdigest()
        )
        with (
            patch.dict(
                opencode._RELEASE_ARTIFACTS,
                {'test-version': {('Linux', 'x86_64'): artifact}},
            ),
            patch(
                'llm_coding.opencode.requests.get',
                return_value=FakeResponse(malicious),
            ),
        ):
            self._assert_error(
                RuntimeError,
                'unsafe path',
                lambda: opencode.ensure_installed(self.config, self.runner()),
            )

    def test_failed_atomic_replace_preserves_working_binary(self) -> None:
        self._existing_binary()
        original_replace = Path.replace

        def fail_install(path: Path, target: Path) -> Path:
            if target == self.binary:
                raise OSError('disk failure')
            return original_replace(path, target)

        run = self.runner('old')
        run.side_effect = [
            subprocess.CompletedProcess([], 0, 'old\n', ''),
            subprocess.CompletedProcess([], 0, 'test-version\n', ''),
        ]
        with (
            patch(
                'llm_coding.opencode.requests.get',
                return_value=FakeResponse(self.body),
            ),
            patch.object(Path, 'replace', fail_install),
        ):
            self._assert_error(
                OSError,
                'disk failure',
                lambda: opencode.ensure_installed(self.config, run),
            )
        assert self.binary.read_bytes() == b'working'

    def test_reuses_requested_version_without_downloading(self) -> None:
        self._existing_binary()
        with patch('llm_coding.opencode.requests.get') as get:
            opencode.ensure_installed(self.config, self.runner())
        get.assert_not_called()
        assert self.binary.read_bytes() == b'working'

    def test_rejects_staged_executable_version_mismatch(self) -> None:
        with patch(
            'llm_coding.opencode.requests.get',
            return_value=FakeResponse(self.body),
        ):
            self._assert_error(
                RuntimeError,
                'unexpected version',
                lambda: opencode.ensure_installed(
                    self.config, self.runner('wrong')
                ),
            )
        assert not self.binary.exists()

    def _assert_error(
        self,
        error_type: type[BaseException],
        message: str,
        action: Any,
    ) -> None:
        received = ''
        try:
            action()
        except error_type as exc:
            received = str(exc)
        else:
            raise AssertionError(f'{error_type.__name__} was not raised')
        assert message in received

    def _existing_binary(self) -> None:
        self.binary.parent.mkdir(parents=True, exist_ok=True)
        self.binary.write_bytes(b'working')


if __name__ == '__main__':
    unittest.main()
