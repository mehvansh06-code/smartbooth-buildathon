"""
wsgi.py
CLI Runner and WSGI Entrypoint for Unified Multi-Tenant Voter Platform.
Supports per-tenant process isolation as defined in Section 3.3.

Usage:
  python -m electionapp.wsgi --tenant sanchore --port 5000
  python -m electionapp.wsgi --tenant ward82 --port 5000
"""

import os
import sys
import argparse

# Ensure PLATFORM root is in sys.path
PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app

# Default application for WSGI servers (e.g. gunicorn/uwsgi)
default_tenant = os.environ.get('VOTER_TENANT', 'ward82')
default_storage = os.environ.get('VOTER_STORAGE', 'postgres')
try:
    application = create_app(default_tenant, storage_backend=default_storage)
except Exception as _e:
    # Allow importing parse_args or wsgi utilities even if local PostgreSQL is unreachable
    application = None
app = application


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run Unified Multi-Tenant Voter Platform for a specific campaign."
    )
    parser.add_argument(
        '--tenant', '-t',
        type=str,
        default=os.environ.get('VOTER_TENANT', 'ward82'),
        help="Tenant slug to boot (default: 'ward82' or $VOTER_TENANT)"
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=int(os.environ.get('PORT', '5000')),
        help="Port number to bind (default: 5000 or $PORT)"
    )
    parser.add_argument(
        '--storage', '-s',
        type=str,
        choices=['postgres'],
        default=os.environ.get('VOTER_STORAGE', 'postgres'),
        help="Storage backend: 'postgres' (primary; default: 'postgres' or $VOTER_STORAGE)"
    )
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        default=os.environ.get('FLASK_DEBUG', '0') == '1',
        help="Enable Flask debug mode and autoreload"
    )
    return parser.parse_args()


if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def main():
    args = parse_args()
    server_app = create_app(tenant_slug=args.tenant, storage_backend=args.storage)
    campaign = server_app.config.get('CAMPAIGN', {})
    candidate_name = campaign.get('candidate', {}).get('name_hi') or campaign.get('candidate', {}).get('name_en') or ''
    election_title = campaign.get('election', {}).get('app_title', '')

    print("=" * 60)
    print(f"[SMART BOOTH] Unified Multi-Tenant Voter Platform")
    print(f"   Tenant:   {args.tenant}")
    print(f"   Election: {election_title}")
    print(f"   Candidate: {candidate_name}")
    print(f"   Storage:  {args.storage}")
    print(f"   Port:     {args.port}")
    print(f"   Debug:    {args.debug}")
    print("=" * 60)

    server_app.run(host='0.0.0.0', port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
