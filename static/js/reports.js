/**
 * reports.js
 * Frontend controller for SmartBooth Voter Reports, Print & Export Center.
 */

(function () {
  let activePreset = 'parchi';
  let activeTab = 'parchi';
  let previewDebounceTimer = null;
  let cachedPreviewVoters = [];

  const presetLabels = {
    parchi: '12-Up कटिंग मतदाता पर्ची',
    az: 'A-Z वर्णमाला अनुसार सूची',
    mobile_sheet: 'मोबाइल प्रविष्टि सर्वे पत्रक',
    roll: 'वार्ड / बूथवार मतदाता सूची',
    caste: 'जाति व समुदाय सूची',
    samarthak: 'पक्के समर्थक सूची (100)',
    outside: 'प्रवासी मतदाता कॉलिंग सूची',
    unvoted: 'शेष मतदाता सूची (GOTV)'
  };

  function getFilterParams() {
    const params = new URLSearchParams();
    if (window.CURRENT_TENANT || window.CAMPAIGN_INFO?.slug) {
      params.set('tenant', window.CURRENT_TENANT || window.CAMPAIGN_INFO.slug);
    }

    params.set('preset', activePreset);

    const wardFilter = document.getElementById('repWardFilter');
    const rawWardVal = wardFilter?.value || '';
    if (rawWardVal) {
      if (rawWardVal.startsWith('ward:')) {
        params.set('ward_no', rawWardVal.substring(5));
      } else if (rawWardVal.startsWith('part:') || rawWardVal.startsWith('booth:') || rawWardVal.startsWith('parts:')) {
        params.set('part_no', rawWardVal);
      } else {
        const key = wardFilter?.dataset?.filterKey || 'ward_no';
        params.set(key, rawWardVal);
      }
    }

    const caste = document.getElementById('repCasteFilter')?.value || 'all';
    if (caste && caste !== 'all') params.set('caste', caste);

    const cat = document.getElementById('repCategoryFilter')?.value || 'all';
    if (cat && cat !== 'all') params.set('category', cat);

    const mob = document.getElementById('repMobileFilter')?.value || 'all';
    if (mob && mob !== 'all') params.set('mobile_status', mob);

    const res = document.getElementById('repResidenceFilter')?.value || 'all';
    if (res && res !== 'all') params.set('residence', res);

    const vote = document.getElementById('repVotingFilter')?.value || 'all';
    if (vote && vote !== 'all') params.set('voting_status', vote);

    const gen = document.getElementById('repGenderFilter')?.value || 'all';
    if (gen && gen !== 'all') params.set('gender', gen);

    const sort = document.getElementById('repSortFilter')?.value || 'serial';
    params.set('sort_by', sort);

    const density = document.querySelector('input[name="parchiDensity"]:checked')?.value || '12';
    params.set('density', density);

    return params;
  }

  window.selectReportPreset = function (presetKey) {
    activePreset = presetKey;
    if (window.triggerHaptic) window.triggerHaptic('selection');

    // Update chip styling
    document.querySelectorAll('.preset-chip').forEach(btn => {
      btn.className = 'preset-chip px-3.5 py-2 rounded-xl transition whitespace-nowrap cursor-pointer flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold';
    });

    const activeBtnMap = {
      parchi: 'btnPresetParchi',
      az: 'btnPresetAz',
      mobile_sheet: 'btnPresetMobile',
      roll: 'btnPresetRoll',
      caste: 'btnPresetCaste',
      samarthak: 'btnPresetSamarthak',
      outside: 'btnPresetOutside',
      unvoted: 'btnPresetUnvoted',
      counting_sheet: 'btnPresetCounting'
    };

    const activeEl = document.getElementById(activeBtnMap[presetKey]);
    if (activeEl) {
      activeEl.className = 'preset-chip px-3.5 py-2 rounded-xl transition whitespace-nowrap cursor-pointer flex items-center gap-1.5 bg-indigo-600 text-white font-black shadow-xs ring-2 ring-indigo-600/30';
    }

    const statLabel = document.getElementById('statActivePreset');
    if (statLabel) statLabel.textContent = presetLabels[presetKey] || presetKey;

    const densityWrapper = document.getElementById('parchiDensityWrapper');
    if (densityWrapper) {
      densityWrapper.style.display = (presetKey === 'parchi') ? 'block' : 'none';
    }

    // Apply preset smart filters
    const sortEl = document.getElementById('repSortFilter');
    const catEl = document.getElementById('repCategoryFilter');
    const resEl = document.getElementById('repResidenceFilter');
    const voteEl = document.getElementById('repVotingFilter');

    if (presetKey === 'az') {
      if (sortEl) sortEl.value = 'az_en';
      setPreviewTab('table');
    } else if (presetKey === 'samarthak') {
      if (catEl) catEl.value = '100';
      setPreviewTab('table');
    } else if (presetKey === 'outside') {
      if (resEl) resEl.value = 'outside_only';
      setPreviewTab('table');
    } else if (presetKey === 'unvoted') {
      if (voteEl) voteEl.value = 'unvoted';
      setPreviewTab('table');
    } else if (presetKey === 'mobile_sheet') {
      setPreviewTab('table');
    } else if (presetKey === 'parchi') {
      setPreviewTab('parchi');
    } else if (presetKey === 'counting_sheet') {
      setPreviewTab('table');
      window.open('/reports/counting?tenant=' + encodeURIComponent(window.CAMPAIGN_INFO?.slug || ''), '_blank');
    }

    triggerReportFilterChange();
  };

  window.setPreviewTab = function (tabName) {
    activeTab = tabName;
    const tabP = document.getElementById('tabPreviewParchi');
    const tabT = document.getElementById('tabPreviewTable');
    const boxP = document.getElementById('parchiPreviewContainer');
    const boxT = document.getElementById('tablePreviewContainer');

    if (tabName === 'parchi') {
      if (tabP) tabP.className = 'px-3.5 py-1.5 rounded-xl text-xs font-black transition cursor-pointer bg-indigo-600 text-white shadow-xs';
      if (tabT) tabT.className = 'px-3.5 py-1.5 rounded-xl text-xs font-bold transition cursor-pointer bg-slate-200 text-slate-700 hover:bg-slate-300';
      if (boxP) boxP.classList.remove('hidden');
      if (boxT) boxT.classList.add('hidden');
    } else {
      if (tabP) tabP.className = 'px-3.5 py-1.5 rounded-xl text-xs font-bold transition cursor-pointer bg-slate-200 text-slate-700 hover:bg-slate-300';
      if (tabT) tabT.className = 'px-3.5 py-1.5 rounded-xl text-xs font-black transition cursor-pointer bg-indigo-600 text-white shadow-xs';
      if (boxP) boxP.classList.add('hidden');
      if (boxT) boxT.classList.remove('hidden');
    }
  };

  window.triggerReportFilterChange = function () {
    const statusEl = document.getElementById('statStatusText');
    if (statusEl) statusEl.textContent = 'अपडेट हो रहा है...';

    if (previewDebounceTimer) clearTimeout(previewDebounceTimer);
    previewDebounceTimer = setTimeout(fetchReportPreview, 250);
  };

  window.refreshReportPreview = function () {
    fetchReportPreview();
  };

  async function fetchReportPreview() {
    const params = getFilterParams();
    params.set('limit', '50');

    try {
      const res = await fetch('/api/reports/preview?' + params.toString());
      const data = await res.json();

      if (!data.success) {
        document.getElementById('statStatusText').textContent = 'डेटा त्रुटि';
        return;
      }

      const total = data.total_matching || 0;
      const pages = data.page_estimate || 0;
      cachedPreviewVoters = data.voters || [];

      document.getElementById('statTotalVoters').textContent = Number(total).toLocaleString('hi-IN');
      document.getElementById('statTotalPages').textContent = `${pages} A4 पेज`;
      document.getElementById('statStatusText').textContent = '✅ तैयार';

      document.getElementById('previewTotalMatchCount').textContent = Number(total).toLocaleString('hi-IN');
      document.getElementById('previewDisplayCount').textContent = `प्रथम ${cachedPreviewVoters.length} रिकॉर्ड`;

      renderParchiPreview(cachedPreviewVoters);
      renderTablePreview(cachedPreviewVoters);

    } catch (e) {
      console.error('Preview error:', e);
      const statusEl = document.getElementById('statStatusText');
      if (statusEl) statusEl.textContent = 'लोड विफल';
    }
  }

  function renderParchiPreview(voters) {
    const container = document.getElementById('parchiGrid');
    if (!container) return;

    if (!voters || voters.length === 0) {
      container.innerHTML = `
        <div class="col-span-full py-16 text-center text-slate-400 font-bold text-sm bg-white rounded-xl border border-dashed border-slate-300">
          चयनित फिल्टर के अनुसार कोई मतदाता रिकॉर्ड नहीं मिले।
        </div>
      `;
      return;
    }

    const wardTitle = window.CAMPAIGN_INFO?.wardTitle || 'ग्राम पंचायत';
    const candChip = window.CAMPAIGN_INFO?.symbol || window.CAMPAIGN_INFO?.candidateName || '🚜 प्रत्याशी';

    const cardsHtml = voters.slice(0, 12).map(v => {
      const n_hi = v.name_hi || v.name_en || '';
      const n_en = (v.name_en && v.name_en !== n_hi) ? `(${v.name_en})` : '';
      const r_hi = v.rel_name_hi || v.rel_name_en || '-';
      const r_type = v.rel_type || 'पिता/पति';
      const gen = String(v.gender || '').toUpperCase().startsWith('M') || v.gender === 'पु' ? 'पु' : 'म';
      const booth = v.booth_name || 'राजकीय विद्यालय';

      return `
        <div class="bg-white border-1.5 border-dashed border-slate-400 rounded-lg p-3 shadow-2xs relative text-[11px] leading-snug flex flex-col justify-between min-h-[145px]">
          <span class="absolute -top-2 right-2 text-[9px] bg-white px-1 font-mono text-slate-500 font-bold border border-slate-200 rounded">✂ कटिंग लाइन</span>
          
          <div class="flex items-center justify-between pb-1.5 mb-1.5 border-b border-slate-900">
            <span class="font-extrabold text-slate-900 text-xs truncate max-w-[70%]">🗳️ ${wardTitle}</span>
            <span class="bg-blue-50 text-blue-900 border border-blue-200 text-[10px] font-bold px-1.5 py-0.5 rounded">${candChip}</span>
          </div>

          <div class="bg-slate-50 border border-slate-200 rounded p-1 mb-1.5 flex items-center justify-between text-xs">
            <span class="bg-slate-900 text-white font-black px-2 py-0.5 rounded text-[11px]">क्र.सं. ${v.sl_no || '-'}</span>
            <span class="font-bold text-slate-700 text-[11px]">वार्ड/भाग: <strong>${v.ward_no || v.part_no || '-'}</strong></span>
            <span class="font-mono font-bold text-slate-900 text-[11px]">${v.epic_no || '-'}</span>
          </div>

          <div class="space-y-0.5 text-slate-800">
            <div class="flex items-baseline gap-1">
              <span class="text-slate-500 font-bold w-12 shrink-0">मतदाता:</span>
              <strong class="text-slate-900">${n_hi}</strong>
              <span class="text-slate-500 text-[10px]">${n_en}</span>
            </div>
            <div class="flex items-baseline gap-1">
              <span class="text-slate-500 font-bold w-12 shrink-0">${r_type}:</span>
              <span>${r_hi}</span>
            </div>
            <div class="grid grid-cols-2 gap-1 text-[10px] text-slate-600">
              <div>मकान नं.: <strong class="text-slate-900">${v.house_no || '-'}</strong></div>
              <div>आयु/लिंग: <strong class="text-slate-900">${v.age || ''} वर्ष / ${gen}</strong></div>
            </div>
          </div>

          <div class="bg-slate-50 border-l-2 border-slate-800 p-1 my-1 text-[10px] text-slate-700 truncate">
            <strong>मतदान केंद्र:</strong> ${booth}
          </div>

          <div class="border-t border-slate-200 pt-1 text-[9px] text-slate-500 flex items-center justify-between font-bold">
            <span>⏰ प्रातः 7 से सायं 5 बजे</span>
            <span class="text-emerald-700 font-extrabold">★ पहचान पत्र साथ लाएं ★</span>
          </div>
        </div>
      `;
    }).join('');

    container.innerHTML = cardsHtml;
  }

  function renderTablePreview(voters) {
    const tbody = document.getElementById('tablePreviewBody');
    if (!tbody) return;

    if (!voters || voters.length === 0) {
      tbody.innerHTML = `
        <tr><td colspan="10" class="p-8 text-center text-slate-400 font-bold">कोई रिकॉर्ड नहीं मिले।</td></tr>
      `;
      return;
    }

    const rowsHtml = voters.map((v, i) => {
      const catMap = { 100: '⭐ समर्थक', 50: '⚖️ 50-50', 0: 'सामान्य', '-1': '🚫 विरोधी' };
      const catLabel = catMap[v.voter_category] || 'सामान्य';
      const gen = String(v.gender || '').toUpperCase().startsWith('M') || v.gender === 'पु' ? 'पु' : 'म';

      return `
        <tr class="hover:bg-slate-50 transition">
          <td class="p-2.5 text-center font-bold text-slate-900">${v.sl_no || (i + 1)}</td>
          <td class="p-2.5 text-center font-bold text-slate-700">${v.ward_no || v.part_no || '-'}</td>
          <td class="p-2.5">
            <div class="font-extrabold text-slate-900">${v.name_hi || v.name_en || ''}</div>
            ${v.name_en ? `<div class="text-[10px] text-slate-400">${v.name_en}</div>` : ''}
          </td>
          <td class="p-2.5 text-slate-700">${v.rel_name_hi || v.rel_name_en || '-'}</td>
          <td class="p-2.5 text-center font-mono text-xs font-bold text-slate-800">${v.epic_no || '-'}</td>
          <td class="p-2.5 text-center text-slate-700">${v.house_no || '-'}</td>
          <td class="p-2.5 text-center font-bold">${v.age || ''} / ${gen}</td>
          <td class="p-2.5 text-center font-mono font-bold ${v.contact_phone ? 'text-emerald-700' : 'text-slate-300'}">
            ${v.contact_phone || '—'}
          </td>
          <td class="p-2.5 text-slate-700 font-bold">${v.caste_tag || '—'}</td>
          <td class="p-2.5 text-center">
            <span class="text-[10px] px-2 py-0.5 rounded-full font-bold bg-slate-100 text-slate-700">${catLabel}</span>
          </td>
        </tr>
      `;
    }).join('');

    tbody.innerHTML = rowsHtml;
  }

  window.printCurrentReport = function () {
    if (window.triggerHaptic) window.triggerHaptic('medium');
    const params = getFilterParams();
    params.set('type', activePreset);

    const printUrl = '/api/reports/print_html?' + params.toString();

    let iframe = document.getElementById('reportPrintFrame');
    if (!iframe) {
      iframe = document.createElement('iframe');
      iframe.id = 'reportPrintFrame';
      iframe.setAttribute('style', 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden;');
      document.body.appendChild(iframe);
    }

    const statusEl = document.getElementById('statStatusText');
    if (statusEl) statusEl.textContent = 'प्रिंट तैयार हो रहा है...';

    iframe.onload = function () {
      if (statusEl) statusEl.textContent = '✅ तैयार';
      try {
        iframe.contentWindow.focus();
        iframe.contentWindow.print();
      } catch (e) {
        window.open(printUrl, '_blank');
      }
    };

    iframe.src = printUrl;
  };

  window.downloadCurrentPdf = function () {
    if (window.triggerHaptic) window.triggerHaptic('light');
    const params = getFilterParams();
    params.set('type', activePreset);
    window.location.href = '/api/reports/export/pdf?' + params.toString();
  };

  window.downloadCurrentExcel = function () {
    if (window.triggerHaptic) window.triggerHaptic('light');
    const params = getFilterParams();
    window.location.href = '/api/reports/export/excel?' + params.toString();
  };

  window.resetReportFilters = function () {
    ['repWardFilter', 'repCasteFilter', 'repCategoryFilter', 'repMobileFilter', 'repResidenceFilter', 'repVotingFilter', 'repGenderFilter'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = (id === 'repWardFilter' ? '' : 'all');
    });

    const sortEl = document.getElementById('repSortFilter');
    if (sortEl) sortEl.value = 'serial';

    triggerReportFilterChange();
  };

  document.addEventListener('DOMContentLoaded', function () {
    fetchReportPreview();
  });
})();
