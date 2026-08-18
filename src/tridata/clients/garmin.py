"""Re-export GarminClient from its original module.

The actual implementation lives in tridata.garmin_client so that the
existing tests (which patch tridata.garmin_client.Garmin) continue to
work without modification.  This shim makes it importable from the new
clients package path as well.
"""
from ..garmin_client import GarminAuthError, GarminClient

__all__ = ["GarminClient", "GarminAuthError"]
