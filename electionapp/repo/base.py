"""
base.py
Abstract Base Repository for Unified Multi-Tenant Voter Platform.
Contract enforcement for Stage 1 (SQLite) and Stage 2 (PostgreSQL 17).

Non-negotiable contract:
- EVERY repository method MUST take tenant_id (int or str slug resolved to int) as its first positional parameter.
- Return plain dict / list[dict] matching existing API schemas (no ORM objects).
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union


class BaseVoterRepository(ABC):
    """
    Abstract interface for voter data persistence.
    Ensures absolute driver-agnostic parity across SQLite and PostgreSQL 17.
    """

    def __init__(self, tenant_slug: str = "jelatra", storage_backend: str = "postgres"):
        self.tenant_slug = tenant_slug
        self.storage_backend = storage_backend

    @abstractmethod
    def get_voter(self, tenant_id: Union[int, str], voter_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single voter by identifier strictly scoped to the specified tenant.
        Returns a plain dictionary matching canonical schema, or None if not found.
        """
        pass

    @abstractmethod
    def get_voters_by_ids(self, tenant_id: Union[int, str], voter_ids: List[int]) -> List[Dict[str, Any]]:
        """
        Retrieves multiple voters matching the given voter_ids list strictly scoped to tenant_id.
        """
        pass

    @abstractmethod
    def search_voters(
        self, tenant_id: Union[int, str], query: str, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Executes typo-tolerant, Indic token-aware search across voter records.
        PostgreSQL backend leverages GIN pg_trgm index + SIMD rapidfuzz.
        SQLite backend leverages candidate filtering + RapidFuzz / fuzzy ranking.
        """
        pass

    @abstractmethod
    def get_voters_by_part(
        self, tenant_id: Union[int, str], part_no: int, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Retrieves voters within a given polling part / booth, ordered by serial number (sl_no).
        """
        pass

    @abstractmethod
    def get_family_voters(self, tenant_id: Union[int, str], family_id: str) -> List[Dict[str, Any]]:
        """
        Retrieves all voters belonging to a family cluster, ordered by head of family and age.
        """
        pass

    @abstractmethod
    def mark_voted(
        self,
        tenant_id: Union[int, str],
        voter_id: int,
        is_voted: bool,
        user_id: int,
        voter_name: str = "",
        device_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> bool:
        """
        Updates voter turnout status and appends an immutable audit entry to voting_audit_log
        atomically in one transaction.
        Returns True on success, False otherwise.
        """
        pass

    @abstractmethod
    def get_turnout_stats(self, tenant_id: Union[int, str]) -> Dict[str, Any]:
        """
        Computes overall and booth/part-wise turnout metrics.
        Returns:
            - total_voters: int
            - voted_count: int
            - unvoted_count: int
            - turnout_percent: float
            - booth_stats: List[Dict[str, Any]]
        """
        pass

    @abstractmethod
    def get_gotv_voters(
        self, tenant_id: Union[int, str], min_category: int = 50, part_no: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Returns favorable unvoted voters (is_voted = False/0 and voter_category >= min_category).
        Optionally filtered by part_no.
        """
        pass

    @abstractmethod
    def log_dispatch(
        self,
        tenant_id: Union[int, str],
        voter_id: int,
        phone: str,
        channel: str,
        message_id: Optional[str] = None,
        delivery_status: str = "SENT",
        payload: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """
        Records a slip dispatch entry in the dispatch audit ledger and marks the voter
        is_dispatched = True with dispatched_at timestamp atomically.
        Returns the generated dispatch record ID (int).
        """
        pass

    @abstractmethod
    def log_audit_event(
        self,
        tenant_id: Union[int, str],
        user_id: Optional[int],
        username: Optional[str],
        action: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """
        Records a generic "who did what" entry in the audit_log ledger. Distinct
        from the narrow voting_audit_log (vote-marking only) and dispatch_history
        (WhatsApp sends, no actor column). Intended for use via the
        electionapp.audit.audit_action decorator on blueprint write routes.
        Never raises on failure to the caller's callers; implementations may log
        and swallow errors since audit logging must not block the underlying request.
        """
        pass

    # Convenience aliases for tenant-bound application usage
    def search(self, query: str = "", limit: int = 50, offset: int = 0, **kwargs) -> List[Dict[str, Any]]:
        """Tenant-bound alias for search_voters."""
        return self.search_voters(self.tenant_slug, query, limit=limit, offset=offset, **kwargs)

    def get_family(self, family_id: str) -> List[Dict[str, Any]]:
        """Tenant-bound alias for get_family_voters."""
        return self.get_family_voters(self.tenant_slug, family_id)

    def get_parts(self, tenant_id: Optional[Union[int, str]] = None) -> List[Dict[str, Any]]:
        """Retrieves list of distinct polling parts/booths with voter counts."""
        return []

    def get_booths_dict(self, tenant_id: Optional[Union[int, str]] = None) -> Dict[str, Dict[str, Any]]:
        """Returns a dictionary mapping part_no string to booth details."""
        parts = self.get_parts(tenant_id)
        return {str(p["part_no"]): p for p in parts}

    def get_wards(self, tenant_id: Optional[Union[int, str]] = None) -> List[Dict[str, Any]]:
        """Retrieves distinct wards with their polling booth and voter counts."""
        return []

    def get_wards_dict(self, tenant_id: Optional[Union[int, str]] = None) -> Dict[str, Dict[str, Any]]:
        """Returns a dictionary mapping ward_no string to ward details."""
        return {str(w["ward_no"]): w for w in self.get_wards(tenant_id)}

    def get_booths_with_wards(self, tenant_id: Optional[Union[int, str]] = None) -> Dict[str, Dict[str, Any]]:
        """Returns mapping of physical booths with their constituent wards."""
        return {}

    def get_panchayat_hierarchy(self, tenant_id: Optional[Union[int, str]] = None) -> Dict[str, Any]:
        """Returns structured hierarchy of Gram Panchayats and their constituent wards/booths."""
        return {"is_multi_gp": False, "panchayats": [], "total_voters": 0}

