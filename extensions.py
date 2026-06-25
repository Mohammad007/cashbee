"""Shared Flask extensions, defined separately to avoid circular imports."""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import Config

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=Config.RATELIMIT_STORAGE_URI,
)
