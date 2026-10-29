"""Typed exceptions shared across RootPilot."""

from __future__ import annotations


class RootPilotError(Exception):
    """Base class for all RootPilot errors."""


class ConfigError(RootPilotError):
    """Configuration is missing, malformed or unsafe (e.g. a world-readable key file)."""


class BackendError(RootPilotError):
    """The device backend failed to perform an operation."""


class BackendTimeout(BackendError):
    """The device backend did not answer in time."""


class BackendUnsupported(BackendError):
    """The backend cannot perform this operation at all (reported to the model, not fatal)."""


class ProviderError(RootPilotError):
    """The model provider returned an error or an unusable response."""


class ToolInputError(RootPilotError):
    """A tool was called with invalid arguments (reported back to the model)."""


class StaleRefError(ToolInputError):
    """A tool referenced an element ref that is not in the latest UI tree."""
