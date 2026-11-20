/**
 * audit_log.js
 * Super-Admin cross-tenant Audit Logs viewer: fetches `/api/admin/audit_log`
 * with filter + limit/offset pagination params and renders the results table
 * client-side. Follows the same fetch/render-table convention used by
 * history_intel.js and families.js elsewhere in this app.
 */

let auditLogState = {
  limit: 50,
  offset: 0,
  total: 0
};
let auditLogDebounceTimer = null;

function auditLogDebounceFilters() {
  clearTimeout(auditLogDebounceTimer);
  auditLogDebounceTimer = setTimeout(() => {
    auditLogState.offset = 0;
    auditLogFetch();
  }, 350);
}

function auditLogApplyFilters() {
  auditLogState.offset = 0;
  auditLogFetch();
}

function auditLogClearFilters() {
  document.getElementById('auditTenantFilter').value = '';
  document.getElementById('auditUserFilter').value = '';
  document.getElementById('auditActionFilter').value = '';
  document.getElementById('auditDateFrom').value = '';
  document.getElementById('auditDateTo').value = '';
  auditLogState.offset = 0;
  auditLogFetch();
}

function auditLogReload() {
  auditLogFetch();
}

function auditLogChangePage(direction) {
  const nextOffset = auditLogState.offset + (direction * auditLogState.limit);
  if (nextOffset < 0) return;
  if (direction > 0 && nextOffset >= auditLogState.total) return;
  auditLogState.offset = nextOffset;
  auditLogFetch();
}

async function auditLogLoadTenants() {
  const select = document.getElementById('auditTenantFilter');
  if (!select) return;
  try {
    const res = await fetch('/api/admin/campaigns');
    const data = await res.json();
    const campaigns = data.campaigns || [];
    campaigns.forEach((c) => {
      const opt = document.createElement('option');
      opt.value = c.tenant_slug;
      const label = c.ward_name_en || c.ward_name_hi || c.tenant_slug;
      opt.textContent = `${label} (${c.tenant_slug})`;
      select.appendChild(opt);
    });
  } catch (e) {
    console.error('Failed to load tenant list for audit log filter', e);
  }
}

function auditLogBuildQuery() {
  const params = new URLSearchParams();
  const tenant = document.getElementById('auditTenantFilter').value.trim();
  const user = document.getElementById('auditUserFilter').value.trim();
  const action = document.getElementById('auditActionFilter').value.trim();
  const dateFrom = document.getElementById('auditDateFrom').value.trim();
  const dateTo = document.getElementById('auditDateTo').value.trim();

  if (tenant) params.set('tenant', tenant);
  if (user) params.set('user_id', user);
  if (action) params.set('action', action);
  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo) params.set('date_to', dateTo);
  params.set('limit', String(auditLogState.limit));
  params.set('offset', String(auditLogState.offset));
  return params.toString();
}

function auditLogEscapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function auditLogFormatTimestamp(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'medium' });
  } catch (e) {
    return iso;
  }
}

async function auditLogFetch() {
  const tbody = document.getElementById('auditLogTableBody');
  const badge = document.getElementById('auditCountBadge');
  if (!tbody) return;

  tbody.innerHTML = `
    <tr>
      <td colspan="7" class="py-20 text-center text-slate-400">
        <div class="text-3xl mb-2 animate-bounce">🧾</div>
        <p class="text-xs font-bold">ऑडिट रिकॉर्ड लोड हो रहा है...</p>
      </td>
    </tr>
  `;
  if (badge) badge.textContent = 'लोड हो रहा है...';

  try {
    const qs = auditLogBuildQuery();
    const res = await fetch(`/api/admin/audit_log?${qs}`);
    const data = await res.json();

    if (!data.success) {
      tbody.innerHTML = `<tr><td colspan="7" class="py-16 text-center text-rose-500 font-bold text-xs">${auditLogEscapeHtml(data.error || 'ऑडिट रिकॉर्ड लोड करने में त्रुटि हुई')}</td></tr>`;
      if (badge) badge.textContent = 'त्रुटि (Error)';
      return;
    }

    auditLogState.total = data.total || 0;
    auditLogRenderRows(data.rows || []);
    auditLogUpdatePaginationUI();

    if (badge) {
      badge.textContent = `कुल ${auditLogState.total.toLocaleString('en-IN')} प्रविष्टियाँ (Total Entries)`;
    }
  } catch (e) {
    console.error(e);
    tbody.innerHTML = '<tr><td colspan="7" class="py-16 text-center text-rose-500 font-bold text-xs">ऑडिट रिकॉर्ड लोड करने में त्रुटि हुई</td></tr>';
    if (badge) badge.textContent = 'त्रुटि (Error)';
  }
}

function auditLogRenderRows(rows) {
  const tbody = document.getElementById('auditLogTableBody');
  if (!tbody) return;

  if (!rows || rows.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" class="py-16 text-center px-4">
          <div class="text-3xl mb-2">🧾</div>
          <h3 class="text-sm font-bold text-slate-700 mb-1">कोई ऑडिट रिकॉर्ड नहीं मिला</h3>
          <p class="text-xs text-slate-400">फ़िल्टर बदलें अथवा दिनांक सीमा समायोजित करें।</p>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = rows.map((r) => `
    <tr class="hover:bg-slate-50 transition">
      <td class="py-2 px-3 font-mono text-[11px] text-slate-700 whitespace-nowrap">${auditLogEscapeHtml(auditLogFormatTimestamp(r.created_at))}</td>
      <td class="py-2 px-3 font-bold text-slate-800">${auditLogEscapeHtml(r.tenant_slug || r.tenant_id || '—')}</td>
      <td class="py-2 px-3 text-slate-700">${auditLogEscapeHtml(r.username || '—')}</td>
      <td class="py-2 px-3">
        <span class="bg-indigo-50 text-indigo-700 border border-indigo-200 font-bold px-2 py-0.5 rounded text-[11px]">${auditLogEscapeHtml(r.action || '—')}</span>
      </td>
      <td class="py-2 px-3 text-slate-600">${auditLogEscapeHtml(r.entity_type || '—')}</td>
      <td class="py-2 px-3 font-mono text-slate-600">${auditLogEscapeHtml(r.entity_id || '—')}</td>
      <td class="py-2 px-3 font-mono text-[11px] text-slate-500">${auditLogEscapeHtml(r.ip_address || '—')}</td>
    </tr>
  `).join('');
}

function auditLogUpdatePaginationUI() {
  const footerInfo = document.getElementById('auditFooterInfo');
  const pageIndicator = document.getElementById('auditPageIndicator');
  const prevBtn = document.getElementById('auditPrevPageBtn');
  const nextBtn = document.getElementById('auditNextPageBtn');

  const { limit, offset, total } = auditLogState;
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + limit, total);

  if (footerInfo) footerInfo.textContent = `Showing ${rangeStart}–${rangeEnd} of ${total.toLocaleString('en-IN')} entries`;
  if (pageIndicator) pageIndicator.textContent = `Page ${currentPage} of ${totalPages}`;
  if (prevBtn) prevBtn.disabled = offset <= 0;
  if (nextBtn) nextBtn.disabled = (offset + limit) >= total;
}

document.addEventListener('DOMContentLoaded', () => {
  auditLogLoadTenants();
  auditLogFetch();
});
