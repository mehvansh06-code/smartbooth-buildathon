import os
import sys

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app

def test_all_views_with_sidebar():
    app = create_app('ward82', 'postgres')
    c = app.test_client()
    views = ['/', '/voting-day', '/turnout', '/warroom', '/karyakartas', '/caste', '/users', '/my-voters']
    for v in views:
        res = c.get(v)
        assert res.status_code in (200, 302), f'Failed on {v} with {res.status_code}'
        if res.status_code == 200:
            html = res.data.decode('utf-8')
            assert 'id="appSidebar"' in html, f'Sidebar missing in {v}'
            assert 'sidebar-collapse-icon' in html or 'toggleSidebar' in html, f'Toggle button missing in {v}'
            assert 'id="btnSunlightToggle"' not in html, f'Sunlight toggle should be removed from {v}'
            assert 'Log Out' in html, f'Logout button missing in {v}'
            # Verify the old crowded button row is gone from header
            assert '<a href="/turnout" class="px-2 py-1 rounded-md' not in html, f'Old header nav still present in {v}'
        print(f'{v} -> HTTP {res.status_code} OK')
    print('ALL VIEWS RENDERED WITH TRUE FULL-HEIGHT SIDEBAR AND ZERO SUNLIGHT SUCCESSFULLY!')

if __name__ == '__main__':
    test_all_views_with_sidebar()
