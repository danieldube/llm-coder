"""Shared, injectable text-mode subprocess execution."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def run_command(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    input: str | None = None,  # noqa: A002 - match subprocess.run
    diagnostics: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run ``args`` without exposing command arguments or command output.

    Diagnostics are deliberately opt-in.  Even when enabled, they contain
    only the exit status and argument count because arguments, standard input,
    stdout, and stderr can all contain credentials or source content.
    """
    try:
        result = subprocess.run(
            args,
            check=check,
            capture_output=capture_output,
            input=input,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        if diagnostics:
            logger.error(
                'Subprocess failed with exit code %d (%d arguments)',
                exc.returncode,
                len(args),
            )
        raise
    if diagnostics:
        logger.debug(
            'Subprocess exited with code %d (%d arguments)',
            result.returncode,
            len(args),
        )
    return result
