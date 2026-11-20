"""
electionapp.repo package.
Exports Repository Seam interfaces and implementations for Unified Multi-Tenant Voter Platform.
SmartBooth runs exclusively on PostgreSQL.
"""

from electionapp.repo.base import BaseVoterRepository
from electionapp.repo.postgres_repo import PostgresVoterRepository


def get_repository(storage_backend: str = "postgres", **kwargs) -> BaseVoterRepository:
    """
    Factory to obtain repository instance based on storage backend.
    SmartBooth runs exclusively on PostgreSQL.
    """
    backend = (storage_backend or "postgres").lower().strip()
    if backend in ("postgres", "postgresql", "pg"):
        return PostgresVoterRepository(**kwargs)
    elif backend in ("sqlite", "sqlite3"):
        raise ValueError("SQLite storage backend has been retired. SmartBooth runs exclusively on PostgreSQL.")
    else:
        raise ValueError(f"Unknown storage_backend: '{storage_backend}'. Supported: 'postgres'.")


__all__ = [
    "BaseVoterRepository",
    "PostgresVoterRepository",
    "get_repository",
]
