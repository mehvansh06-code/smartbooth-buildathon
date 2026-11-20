"""
run_local.py
Local Development & Testing Server Runner for Chunav Saathi Unified Platform.
Pre-provisions test accounts and launches the Flask server.
"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Ensure UTF-8 stdout
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PLATFORM_ROOT = os.path.abspath(os.path.dirname(__file__))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

import user_manager
from electionapp import create_app

def setup_demo_users():
    """Ensure standard test users exist with easy-to-use credentials."""
    # Fast boot: accounts (admin/admin2026, ward82/ward82, etc.) are already persisted
    pass

def main():
    setup_demo_users()
    
    # Enable core mode by default (Voter search & WhatsApp slip sending)
    os.environ['VOTER_CORE_MODE'] = '1'
    os.environ['FORCE_AUTH_CHECK'] = '1'
    
    storage_backend = os.environ.get('STORAGE_BACKEND', 'postgres')
    port = int(os.environ.get('PORT') or os.environ.get('APP_PORT') or 5030)
    app = create_app(tenant_slug='ward82', storage_backend=storage_backend)
    
    print("\n" + "=" * 72)
    print("🚀 CHUNAV SAATHI — LOCAL SERVER RUNNING")
    print(f"🗄️ Database Backend: {storage_backend.upper()} (133k+ Partitioned Voters)")
    print("=" * 72)
    print(f"🔗 Local Web URL: http://localhost:{port}")
    print("------------------------------------------------------------------------")
    print("🔑 Test User Credentials (Multi-Tenant Demo):")
    print("   • Super Admin (All Campaigns):  username: admin    | password: admin2026")
    print("   • Ward 82 (Ward 82 Only):       username: ward82   | password: ward82")
    print("   • Jelatra (Jelatra Only):       username: jelatra  | password: jelatra")
    print("   • Rajpura (Rajpura Only):       username: rajpura  | password: rajpura")
    print("   • Ward 9 (Ward 9 Only):         username: ward9    | password: ward9")
    print("------------------------------------------------------------------------")
    print("✨ Core Product Focused: Two-Box Indic Search & WhatsApp Slip Sending")
    print("=" * 72 + "\n", flush=True)
    
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)

if __name__ == '__main__':
    main()
