"""
blueprints package: Modular route splits for Unified Multi-Tenant Voter Platform.
"""

from .auth import auth_bp
from .core import core_bp
from .voters import voters_bp
from .search import search_bp
from .warroom import warroom_bp
from .dispatch import dispatch_bp
from .admin import admin_bp
from .history import history_bp
from .slip import slip_bp
from .campaign import campaign_bp
from .turnout import turnout_bp
from .karyakartas import karyakartas_bp
from .caste import caste_bp
from .family import family_bp
from .reports import reports_bp
from .free_slips import free_slips_bp
from .voice import voice_bp
from .voice_agent import voice_agent_bp
from .agent_api import agent_bp

__all__ = [
    'auth_bp',
    'core_bp',
    'voters_bp',
    'search_bp',
    'warroom_bp',
    'dispatch_bp',
    'admin_bp',
    'history_bp',
    'slip_bp',
    'campaign_bp',
    'turnout_bp',
    'karyakartas_bp',
    'caste_bp',
    'family_bp',
    'reports_bp',
    'free_slips_bp',
    'voice_bp',
    'voice_agent_bp',
    'agent_bp'
]
