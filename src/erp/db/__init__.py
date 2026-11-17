"""Database package. Importing models registers them on ``Base.metadata``."""

from erp.db.base import Base
from erp.db import models as models

__all__ = ["Base", "models"]
