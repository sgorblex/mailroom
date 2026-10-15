"""ORM models: import every model module here so ``Base.metadata`` sees it (autogenerate)."""

from mailroom.models.base import Base

__all__ = ["Base"]
