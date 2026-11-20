"""
search.py
Search Blueprint for Unified Multi-Tenant Voter Platform:
- Bilingual voter search API (/api/search)
Supports ?q=...&part_no=...&limit=...&offset=...
Consumes repository search with Indic transliteration, Devanagari normalization,
and rapidfuzz similarity ranking.
"""

from flask import Blueprint, jsonify, request, current_app, g, session

search_bp = Blueprint('search', __name__)


@search_bp.route('/api/search')
def api_search():
    """
    Search voters by query string (name in Hindi/English, EPIC, serial, house)
    and optional part_no filter across tenants.
    """
    from electionapp.tenancy import resolve_tenant
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500
    q = request.args.get('q', '').strip()
    name = request.args.get('name', '').strip()
    first_name = request.args.get('first_name', '').strip()
    if not name and first_name:
        name = first_name
    relative = request.args.get('relative', '').strip()
    house = request.args.get('house', '').strip()

    if not q and (name or relative or house):
        parts = []
        if name:
            parts.append(name)
        if relative:
            parts.append(relative)
        if house:
            parts.append(house)
        q = " ".join(parts)

    part_param = request.args.get('part_no') or request.args.get('part')
    parts_param = request.args.get('parts') or request.args.get('part_nos')
    booth_param = request.args.get('booth') or request.args.get('booth_no')
    ward_param = request.args.get('ward_no') or request.args.get('ward')
    gp_param = request.args.get('gp') or request.args.get('gram_panchayat')

    if parts_param:
        part_param = f"parts:{parts_param}"
    elif not part_param and booth_param:
        part_param = f"booth:{booth_param}"

    wards_param = request.args.get('wards') or request.args.get('ward_nos')
    if wards_param:
        ward_param = f"wards:{wards_param}"

    from electionapp.config import parse_location_filter_extended
    ward_no, part_no, part_nos, extracted_gp, ward_nos = parse_location_filter_extended(ward_param, part_param)
    if not gp_param and extracted_gp:
        gp_param = extracted_gp

    limit = request.args.get('limit', default=50, type=int)
    offset = request.args.get('offset', default=0, type=int)

    filters = {}
    if name:
        filters['name'] = name
    if relative:
        filters['relative'] = relative
    if house:
        filters['house'] = house
    if part_no is not None:
        filters['part_no'] = part_no
    if part_nos:
        filters['part_nos'] = part_nos
    if ward_no is not None:
        filters['ward_no'] = ward_no
    if ward_nos:
        filters['ward_nos'] = ward_nos
    if gp_param:
        filters['gp'] = gp_param
        filters['gram_panchayat'] = gp_param

    gender = request.args.get('gender', '').strip()
    if gender:
        filters['gender'] = gender

    age_min_param = request.args.get('age_min')
    if age_min_param is not None and str(age_min_param).isdigit():
        filters['age_min'] = int(age_min_param)

    age_max_param = request.args.get('age_max')
    if age_max_param is not None and str(age_max_param).isdigit():
        filters['age_max'] = int(age_max_param)

    caste = request.args.get('caste', '').strip()
    if caste:
        filters['caste'] = caste

    category_param = request.args.get('category')
    if category_param is not None and str(category_param).isdigit():
        filters['category'] = int(category_param)

    sort_param = request.args.get('sort') or request.args.get('order_by')
    if sort_param:
        filters['sort'] = sort_param.strip()

    # Check if user has scoped sub-access (booth, ward, or village)
    curr_user = getattr(g, 'current_user', {}) or {}
    user_role = curr_user.get('role') or session.get('role', '')
    is_super = (user_role == 'super_admin' or session.get('role') == 'super_admin' or curr_user.get('tenant_slug') == '*')

    if not is_super:
        assigned_booths = list(curr_user.get('assigned_booths') or session.get('assigned_booths') or [])
        assigned_booth = curr_user.get('assigned_booth') if curr_user.get('assigned_booth') is not None else session.get('assigned_booth')
        if assigned_booth is not None and assigned_booth not in assigned_booths:
            assigned_booths.append(assigned_booth)

        if assigned_booths:
            if part_no is not None:
                if part_no not in assigned_booths:
                    return jsonify({'success': True, 'total': 0, 'count': 0, 'voters': [], 'page': 1, 'limit': limit, 'offset': offset}), 200
            else:
                filters['part_nos'] = assigned_booths

        assigned_wards = list(curr_user.get('assigned_wards') or session.get('assigned_wards') or [])
        if assigned_wards:
            if ward_no is not None:
                if ward_no not in assigned_wards:
                    return jsonify({'success': True, 'total': 0, 'count': 0, 'voters': [], 'page': 1, 'limit': limit, 'offset': offset}), 200
            else:
                filters['ward_nos'] = assigned_wards

        assigned_villages = list(curr_user.get('assigned_villages') or session.get('assigned_villages') or [])
        if assigned_villages:
            filters['villages'] = assigned_villages

        assigned_gps = list(curr_user.get('assigned_gps') or session.get('assigned_gps') or [])
        if assigned_gps and not assigned_booths:
            filters['gp'] = assigned_gps[0] if len(assigned_gps) == 1 else None

    # Route search with all filter predicates
    tenant_target = tenant or getattr(repo, 'tenant_slug', '') or ''
    if hasattr(repo, 'search_voters'):
        results = repo.search_voters(tenant_target, query=q, limit=limit, offset=offset, **filters)
    elif hasattr(repo, 'search'):
        results = repo.search(query=q, limit=limit, offset=offset, **filters)
    else:
        results = []

    # Count total matching voters for accurate pagination
    # Smart count bypass: if first page returns fewer than limit, count is exact
    if offset == 0 and len(results) < limit:
        total_count = len(results)
    elif hasattr(repo, 'count_voters'):
        try:
            total_count = repo.count_voters(tenant_target, query=q, **filters)
        except Exception:
            total_count = len(results)
    else:
        total_count = len(results)

    return jsonify({
        'success': True,
        'results': results,
        'voters': results,
        'total': total_count,
        'total_count': total_count,
        'shown': len(results),
        'limit': limit,
        'offset': offset,
        'query': q,
        'tenant': tenant_target,
        'part_no': part_no,
        'ward_no': ward_no,
        'filters': filters
    }), 200
