"""Typed, user-safe errors and stable command exit codes."""

from enum import IntEnum


class ExitCode(IntEnum):
    """Stable non-zero exit codes from the public command contract."""

    CONFIG = 2
    POLICY = 3
    SECRET_DETECTED = 4
    COLLECTION = 5
    CRYPTO = 6
    DESTINATION = 7
    VERIFY = 8
    RESTORE = 9


class ToolkitError(Exception):
    """Base class for errors whose messages are safe to show to an operator."""

    exit_code = ExitCode.CONFIG


class ConfigError(ToolkitError):
    """Configuration or command arguments are invalid."""

    exit_code = ExitCode.CONFIG


class PolicyError(ToolkitError):
    """A source violates the backup safety policy."""

    exit_code = ExitCode.POLICY


class SecretDetectedError(ToolkitError):
    """A staged source contains a likely secret."""

    exit_code = ExitCode.SECRET_DETECTED


class CollectionError(ToolkitError):
    """A source could not be collected consistently."""

    exit_code = ExitCode.COLLECTION


class CryptoError(ToolkitError):
    """Archive creation, encryption, or decryption failed."""

    exit_code = ExitCode.CRYPTO


class DestinationError(ToolkitError):
    """A destination operation or read-back failed."""

    exit_code = ExitCode.DESTINATION


class DestinationIntegrityError(DestinationError):
    """Destination returned bytes that contradict the expected immutable object."""


class VerifyError(ToolkitError):
    """Backup verification failed."""

    exit_code = ExitCode.VERIFY


class RestoreError(ToolkitError):
    """Restore preview, apply, or rollback failed."""

    exit_code = ExitCode.RESTORE
