"""tridata.clients — brand-agnostic fitness client factory."""
from .base import FitnessClient
from .coros import CorosClient
from .garmin import GarminAuthError, GarminClient
from .suunto import SuuntoClient

__all__ = [
    "FitnessClient",
    "GarminClient",
    "GarminAuthError",
    "SuuntoClient",
    "CorosClient",
]
