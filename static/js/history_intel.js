/**
 * history_intel.js - Dispatch History & Outside Voters Tab Controllers
 * Part of SmartBooth Platform
 */

(function () {
  'use strict';

  let cachedHistoryBatches = [];
  let cachedTotalVoters = 0;

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function renderHistoryBatches(batches) {
    const listContainer = document.getElementById('dispatchHistoryModalList');
    if (!listContainer) return;

    if (!batches || batches.length === 0) {
      listContainer.innerHTML = `
        <div class="text-center py-16 px-4">
          <div class="text-3xl mb-2">📜</div>
          <p class="text-xs text-slate-500 font-semibold">कोई पर्ची रिकॉर्ड नहीं मिला (No dispatches found)</p>
        </div>
      `;
      return;
    }

    listContainer.innerHTML = batches.map(b => {
      const isSingle = (b.slip_type || '').toLowerCase() === 'single';
      const slipBadgeText = isSingle ? 'व्यक्तिगत' : 'परिवार / संयुक्त';
      const st = (b.delivery_status || 'sent').toLowerCase();

      let statusBadge = '';
      if (st === 'delivered') {
        statusBadge = '<span class="px-2.5 py-1 rounded-full text-[10px] font-black uppercase bg-emerald-100 text-emerald-800">✓ पहुँचा</span>';
      } else if (st === 'read') {
        statusBadge = '<span class="px-2.5 py-1 rounded-full text-[10px] font-black uppercase bg-blue-100 text-blue-800">✓✓ पढ़ा</span>';
      } else if (st === 'failed') {
        statusBadge = '<span class="px-2.5 py-1 rounded-full text-[10px] font-black uppercase bg-rose-100 text-rose-800">✕ विफल</span>';
      } else {
        statusBadge = `<span class="px-2.5 py-1 rounded-full text-[10px] font-black uppercase bg-slate-100 text-slate-700">${escapeHtml(b.delivery_status || 'Sent')}</span>`;
      }

      const phoneText = escapeHtml(b.phone || 'उपलब्ध नहीं');
      const channelText = escapeHtml(b.channel || 'WhatsApp');
      const sentAtText = escapeHtml(b.sent_at || '');
      const voterCount = b.voter_count || 1;
      const partsText = b.parts_display ? `(${escapeHtml(b.parts_display)})` : '';
      const previewText = b.message_preview ? `<p class="text-slate-400 text-[11px] truncate mt-0.5 max-w-md">${escapeHtml(b.message_preview)}</p>` : '';

      return `
        <div class="p-3 hover:bg-slate-50 transition flex items-center justify-between gap-3 text-xs">
          <div class="flex-1 min-w-0">
            <div class="flex flex-wrap items-center gap-2 mb-1">
              <span class="font-mono font-bold text-slate-900 text-sm">${phoneText}</span>
              <span class="bg-blue-50 text-blue-800 border border-blue-200 font-semibold px-2 py-0.5 rounded text-[11px]">${slipBadgeText}</span>
              <span class="bg-slate-100 text-slate-600 font-medium px-1.5 py-0.5 rounded text-[10px]">${channelText}</span>
              <span class="text-slate-400 font-medium text-[11px]">${sentAtText}</span>
            </div>
            <p class="text-slate-600">${voterCount} मतदाता ${partsText}</p>
            ${previewText}
          </div>
          <div class="shrink-0 text-right">
            ${statusBadge}
          </div>
        </div>
      `;
    }).join('');
  }

  // Open Dispatch History Audit Modal
  window.openHistoryModal = async function () {
    const modal = document.getElementById('dispatchHistoryModal');
    if (!modal) return;
    modal.classList.remove('hidden');

    const listContainer = document.getElementById('dispatchHistoryModalList');
    if (listContainer) {
      listContainer.innerHTML = '<p class="text-xs text-slate-400 py-12 text-center">डिस्पैच हिस्ट्री लोड हो रही है...</p>';
    }

    const searchInput = document.getElementById('historySearchInput');
    if (searchInput) {
      searchInput.value = '';
    }

    try {
      const res = await fetch('/api/history');
      const data = await res.json();
      const batches = data.batches || [];
      cachedHistoryBatches = batches;
      cachedTotalVoters = data.total || 0;

      const totalBadge = document.getElementById('historyTotalBadge');
      if (totalBadge) {
        totalBadge.textContent = `कुल: ${batches.length} बैच (${data.total || 0} मतदाता)`;
      }

      renderHistoryBatches(batches);
    } catch (err) {
      console.error('History modal load error:', err);
      if (listContainer) {
        listContainer.innerHTML = '<p class="text-xs text-rose-500 py-12 text-center">हिस्ट्री लोड करने में त्रुटि</p>';
      }
    }
  };

  // Close Dispatch History Audit Modal
  window.closeHistoryModal = function () {
    const modal = document.getElementById('dispatchHistoryModal');
    if (modal) modal.classList.add('hidden');
    const legacyModal = document.getElementById('historyModal');
    if (legacyModal) legacyModal.classList.add('hidden');
  };

  // Filter Cached History Batches
  window.filterHistoryList = function (query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) {
      const totalBadge = document.getElementById('historyTotalBadge');
      if (totalBadge) {
        totalBadge.textContent = `कुल: ${cachedHistoryBatches.length} बैच (${cachedTotalVoters} मतदाता)`;
      }
      renderHistoryBatches(cachedHistoryBatches);
      return;
    }

    const filtered = cachedHistoryBatches.filter(b => {
      const phone = String(b.phone || '').toLowerCase();
      const slipType = String(b.slip_type || '').toLowerCase();
      const slipHi = slipType === 'single' ? 'व्यक्तिगत' : 'परिवार संयुक्त';
      const status = String(b.delivery_status || '').toLowerCase();
      const channel = String(b.channel || '').toLowerCase();
      const parts = String(b.parts_display || '').toLowerCase();
      const preview = String(b.message_preview || '').toLowerCase();

      return phone.includes(q) ||
        slipType.includes(q) ||
        slipHi.includes(q) ||
        status.includes(q) ||
        channel.includes(q) ||
        parts.includes(q) ||
        preview.includes(q);
    });

    const totalBadge = document.getElementById('historyTotalBadge');
    if (totalBadge) {
      const vCount = filtered.reduce((acc, cur) => acc + (cur.voter_count || 1), 0);
      totalBadge.textContent = `दिखाए गए: ${filtered.length} बैच (${vCount} मतदाता)`;
    }

    renderHistoryBatches(filtered);
  };

  // Keyboard Escape listener
  if (!window._historyModalEscBound) {
    window._historyModalEscBound = true;
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        const modal = document.getElementById('dispatchHistoryModal');
        if (modal && !modal.classList.contains('hidden')) {
          window.closeHistoryModal();
        }
      }
    });
  }

  // Load Dispatch History Tab
  window.loadHistoryData = async function () {
    const container = document.getElementById('historyContentArea');
    if (!container) return;

    container.innerHTML = '<p class="text-xs text-slate-400 py-12 text-center">डिस्पैच हिस्ट्री लोड हो रही है...</p>';

    try {
      const res = await fetch('/api/history');
      const data = await res.json();
      const batches = data.batches || [];

      if (batches.length === 0) {
        container.innerHTML = `
          <div class="text-center py-16 px-4 bg-white rounded-xl border border-slate-200">
            <div class="text-3xl mb-2">📜</div>
            <p class="text-xs text-slate-500 font-semibold">अभी तक कोई पर्ची डिस्पैच नहीं की गई है</p>
          </div>
        `;
        return;
      }

      container.innerHTML = `
        <div class="bg-white rounded-xl border border-slate-200/80 shadow-xs overflow-hidden">
          <div class="p-3 bg-slate-900 text-white flex items-center justify-between">
            <span class="text-xs font-bold">कुल बैच: ${batches.length} (कुल मतदाता: ${data.total || 0})</span>
            <a href="/api/history/export" class="bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs px-3 py-1.5 rounded-lg flex items-center gap-1 shadow-xs">
              📊 Excel Export
            </a>
          </div>
          <div class="divide-y divide-slate-100 max-h-[70vh] overflow-y-auto">
            ${batches.map(b => `
              <div class="p-3 hover:bg-slate-50 transition flex items-center justify-between gap-3 text-xs">
                <div>
                  <div class="flex items-center gap-2 mb-1">
                    <span class="font-mono font-bold text-slate-900 text-sm">${b.phone}</span>
                    <span class="bg-slate-100 font-semibold px-2 py-0.5 rounded text-[11px]">${b.slip_type} · ${b.channel}</span>
                    <span class="font-semibold text-slate-400 text-[11px]">${b.sent_at}</span>
                  </div>
                  <p class="text-slate-600">${b.voter_count} मतदाता (${b.parts_display})</p>
                </div>
                <div class="shrink-0 text-right">
                  <span class="px-2 py-1 rounded-full text-[10px] font-black uppercase ${
                    b.delivery_status === 'read' ? 'bg-blue-100 text-blue-800' :
                    b.delivery_status === 'delivered' ? 'bg-emerald-100 text-emerald-800' :
                    b.delivery_status === 'failed' ? 'bg-rose-100 text-rose-800' :
                    'bg-slate-100 text-slate-700'
                  }">
                    ${b.delivery_status}
                  </span>
                </div>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    } catch (err) {
      console.error('History load error:', err);
      container.innerHTML = '<p class="text-xs text-rose-500 py-12 text-center">हिस्ट्री लोड करने में त्रुटि</p>';
    }
  };

  // Load Outside Voters Tab
  window.loadOutsideVotersData = async function () {
    const container = document.getElementById('outsideVotersContentArea');
    if (!container) return;

    container.innerHTML = '<p class="text-xs text-slate-400 py-12 text-center">प्रवासी मतदाता सूची लोड हो रही है...</p>';

    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const params = new URLSearchParams();
      if (curTenant) params.set('tenant', curTenant);

      const searchInput = document.getElementById('outsideSearchInput');
      const qVal = (searchInput ? searchInput.value : '').trim();
      if (qVal) params.set('q', qVal);

      const filterEl = document.getElementById('outsidePartFilter');
      const rawFilter = filterEl ? filterEl.value : '';
      if (rawFilter) {
        if (rawFilter.startsWith('ward:')) {
          params.set('ward_no', rawFilter.substring(5));
        } else if (rawFilter.startsWith('part:') || rawFilter.startsWith('booth:')) {
          params.set('part_no', rawFilter.replace(/^(part|booth):/, ''));
        } else {
          const key = filterEl.dataset.filterKey || 'part_no';
          params.set(key, rawFilter);
        }
      }

      const queryStr = params.toString() ? `?${params.toString()}` : '';
      const res = await fetch(`/api/outside_voters${queryStr}`);
      const data = await res.json();
      const voters = data.outside_voters || [];

      if (voters.length === 0) {
        container.innerHTML = `
          <div class="text-center py-16 px-4 bg-white rounded-xl border border-slate-200">
            <div class="text-3xl mb-2">✈️</div>
            <p class="text-xs text-slate-500 font-semibold">कोई प्रवासी मतदाता चिह्नित नहीं है</p>
          </div>
        `;
        return;
      }

      container.innerHTML = `
        <div class="bg-white rounded-2xl border border-slate-200/80 shadow-xs overflow-hidden">
          <div class="p-3.5 bg-slate-900 text-white flex items-center justify-between flex-wrap gap-2">
            <div>
              <span class="text-xs font-black">कुल प्रवासी: ${voters.length} मतदाता (${data.total_families || 0} परिवार)</span>
              <span class="text-[11px] text-slate-400 block font-medium">संपर्क फोन उपलब्ध: ${data.families_with_phone || 0} परिवार</span>
            </div>
            <a href="/api/outside_voters/export${queryStr ? queryStr : (curTenant ? '?tenant=' + encodeURIComponent(curTenant) : '')}" class="bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs px-3.5 py-2 rounded-xl flex items-center gap-1.5 shadow-xs transition active:scale-95">
              <span>📊</span><span>Export Calling List (Excel)</span>
            </a>
          </div>
          <div class="divide-y divide-slate-100 max-h-[75vh] overflow-y-auto">
            ${voters.map((v, idx) => {
              const ph = (v.contact_phone || '').trim();
              const phClean = ph.replace(/\D/g, '');
              const needsTravel = (v.notes || '').includes('वाहन') || (v.notes || '').includes('Travel');

              return `
                <div id="outside-voter-${v.voter_id}" class="p-3.5 hover:bg-slate-50 transition flex items-center justify-between gap-3 text-xs flex-wrap sm:flex-nowrap">
                  <div class="space-y-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="font-mono text-slate-400 text-[11px]">#${idx + 1}</span>
                      <strong class="font-black text-slate-900 text-sm">${v.name_hi || v.name_en}</strong>
                      ${v.name_en && v.name_hi ? `<span class="text-slate-500 text-xs">(${v.name_en})</span>` : ''}
                      ${v.ward_no ? `<span class="bg-indigo-100 text-indigo-950 font-bold px-1.5 py-0.5 rounded-md text-[10px]">वार्ड ${v.ward_no}</span>` : ''}
                      <span class="bg-amber-100 text-amber-950 font-bold px-1.5 py-0.5 rounded-md text-[10px]">भाग ${v.part_no} · क्र. ${v.sl_no}</span>
                      <span class="text-slate-500 text-[11px]">${v.age ? v.age + ' वर्ष' : ''} · ${v.gender || ''}</span>
                      ${needsTravel ? '<span class="bg-indigo-100 text-indigo-800 font-bold px-2 py-0.5 rounded-md text-[10px]">🚗 वाहन सुविधा</span>' : ''}
                    </div>
                    <div class="text-[11px] text-slate-500 flex items-center gap-2 flex-wrap">
                      ${v.rel_name_hi || v.rel_name_en ? `<span>सम्बन्ध: <strong class="text-slate-700 font-bold">${v.rel_name_hi || v.rel_name_en}</strong></span>` : ''}
                      ${v.house_no && v.house_no !== '0' ? `<span>· म.नं. <strong class="text-slate-700 font-bold">${v.house_no}</strong></span>` : ''}
                      ${v.epic_no ? `<span>· पहचान पत्र: <span class="font-mono">${v.epic_no}</span></span>` : ''}
                    </div>
                    ${v.notes ? `<div class="text-[11px] bg-slate-100 text-slate-700 rounded-lg px-2 py-1 inline-block font-medium">📍 ${v.notes}</div>` : ''}
                  </div>
                  <div class="flex items-center gap-2 shrink-0 ml-auto">
                    ${ph ? `
                      <a href="tel:${ph}" class="min-h-[38px] px-3 py-1.5 bg-blue-50 hover:bg-blue-100 active:bg-blue-200 text-blue-800 border border-blue-200 rounded-xl text-xs font-bold transition flex items-center gap-1.5" title="कॉल करें">
                        <span>📞</span><span class="font-mono">${ph}</span>
                      </a>
                      <a href="https://wa.me/91${phClean}" target="_blank" class="min-h-[38px] px-3 py-1.5 bg-emerald-50 hover:bg-emerald-100 active:bg-emerald-200 text-emerald-800 border border-emerald-200 rounded-xl text-xs font-bold transition flex items-center gap-1.5" title="WhatsApp भेजें">
                        <span>💬</span><span class="hidden sm:inline">WhatsApp</span>
                      </a>
                    ` : '<span class="text-[11px] text-slate-400 font-medium px-2 py-1 bg-slate-100 rounded-lg">फोन नंबर नहीं</span>'}
                    <button onclick="unmarkOutsideVoter(${v.voter_id})" class="min-h-[38px] px-2.5 py-1.5 text-xs text-rose-600 hover:bg-rose-50 border border-rose-200 font-bold rounded-xl transition cursor-pointer" title="प्रवासी सूची से हटाएं">
                      ✕ हटाएं
                    </button>
                  </div>
                </div>
              `;
            }).join('')}
          </div>
        </div>
      `;
    } catch (err) {
      console.error('Outside voters load error:', err);
      container.innerHTML = '<p class="text-xs text-rose-500 py-12 text-center">प्रवासी सूची लोड करने में त्रुटि</p>';
    }
  };

  window.unmarkOutsideVoter = async function (voterId) {
    const rowEl = document.getElementById(`outside-voter-${voterId}`);
    if (rowEl) {
      rowEl.style.opacity = '0.3';
      rowEl.style.pointerEvents = 'none';
    }
    try {
      const res = await fetch('/api/outside_voters/unmark', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voter_ids: [voterId] })
      });
      const data = await res.json();
      if (data.success) {
        if (rowEl) rowEl.remove();
        window.showToast('प्रवासी मतदाता सूची से हटा दिया गया', 'info');
      } else {
        if (rowEl) {
          rowEl.style.opacity = '1';
          rowEl.style.pointerEvents = '';
        }
        window.showToast(data.message || 'त्रुटि हुई', 'error');
      }
    } catch (e) {
      console.error(e);
      if (rowEl) {
        rowEl.style.opacity = '1';
        rowEl.style.pointerEvents = '';
      }
      window.showToast('नेटवर्क त्रुटि', 'error');
    }
  };

  window.switchMainTab = function (tabName) {
    const tabs = ['voters', 'history', 'outside'];
    tabs.forEach(t => {
      const content = document.getElementById(`tabContent_${t}`);
      const btn = document.getElementById(`tabBtn_${t}`);
      if (content) {
        if (t === tabName) {
          content.classList.remove('hidden');
        } else {
          content.classList.add('hidden');
        }
      }
      if (btn) {
        if (t === tabName) {
          btn.classList.add('bg-blue-600', 'text-white', 'shadow-xs');
          btn.classList.remove('text-slate-600', 'hover:bg-slate-100');
        } else {
          btn.classList.remove('bg-blue-600', 'text-white', 'shadow-xs');
          btn.classList.add('text-slate-600', 'hover:bg-slate-100');
        }
      }
    });

    if (tabName === 'history') {
      window.loadHistoryData();
    } else if (tabName === 'outside') {
      window.loadOutsideVotersData();
    }
  };

})();
