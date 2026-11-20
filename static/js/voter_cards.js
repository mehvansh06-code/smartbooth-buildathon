/**
 * voter_cards.js - High-Performance Voter Cards, Table & Action Island
 * Part of SmartBooth Platform
 */

(function () {
  'use strict';

  let currentResults = [];
  let currentPagination = null;
  let currentViewMode = localStorage.getItem('voter_view_mode') || 'cards'; // 'cards' | 'table'

  let previousSearchState = null;
  let isInFamilyFilterMode = false;
  let currentFamilyData = null;

  // A voter whose name has been struck off the roll (विलोपन). They stay searchable —
  // people do come asking why their name vanished — but they are not electors, so they
  // must never be handed a slip telling them to go to the booth.
  function isStruckOff(v) {
    return String(v && v.status || '').trim().toLowerCase() === 'deleted';
  }
  window.isVoterStruckOff = isStruckOff;

  function struckOffBadgeHtml(v) {
    if (!isStruckOff(v)) return '';
    const reason = (v.del_reason || '').trim();
    return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-black uppercase bg-rose-100 text-rose-800 border border-rose-300"
                  title="${reason ? `कारण: ${reason}` : 'मतदाता सूची से विलोपित'}">✕ विलोपित / Struck off</span>`;
  }

  Object.defineProperty(window, 'isInFamilyFilterMode', {
    get() { return isInFamilyFilterMode; },
    set(val) { isInFamilyFilterMode = Boolean(val); },
    configurable: true
  });

  window.isPanchayatCampaign = function () {
    const cfg = window.CAMPAIGN_CONFIG || {};
    if (cfg.is_panchayat !== undefined) return Boolean(cfg.is_panchayat);
    const elec = cfg.election || {};
    if (elec.is_panchayat !== undefined) return Boolean(elec.is_panchayat);
    const etype = (elec.election_type || cfg.election_type || '').toUpperCase().trim();
    if (['ASSEMBLY', 'VIDHAN_SABHA', 'PARLIAMENTARY', 'LOK_SABHA'].includes(etype)) return false;
    const wHi = (elec.ward_name_hi || '').toLowerCase();
    const wEn = (elec.ward_name_en || '').toLowerCase();
    const consti = (elec.constituency || '').toLowerCase();
    if (wHi.includes('विधानसभा') || wHi.includes('विधान सभा') || wEn.includes('ac-') || consti.includes('ac-') || wEn.includes('vidhan') || consti.includes('assembly')) return false;
    const localTypes = [
      'PANCHAYAT', 'GRAM_PANCHAYAT', 'SARPANCH', 'WARD_PANCH', 'PARSHAD',
      'ZILA_PARISHAD', 'ZP', 'PANCHAYAT_SAMITI', 'PS', 'MUNICIPAL',
      'NAGAR_PALIKA', 'NAGAR_PARISHAD', 'NAGAR_NIGAM', 'WARD'
    ];
    return Boolean(
      elec.is_panchayat ||
      cfg.is_panchayat ||
      localTypes.includes(etype) ||
      wHi.includes('ग्राम पंचायत') ||
      wHi.includes('जिला परिषद') ||
      wHi.includes('पंचायत समिति') ||
      wHi.includes('नगर पालिका') ||
      wHi.includes('नगर निगम') ||
      wHi.includes('नगर परिषद') ||
      wHi.includes('वार्ड') ||
      wEn.includes('ward')
    );
  };

  window.setViewMode = function (mode) {
    currentViewMode = mode;
    localStorage.setItem('voter_view_mode', mode);
    const btnCards = document.getElementById('btnViewCards');
    const btnTable = document.getElementById('btnViewTable');

    if (mode === 'table') {
      if (btnTable) { btnTable.classList.add('bg-white', 'text-blue-700', 'shadow-xs'); btnTable.classList.remove('text-slate-500'); }
      if (btnCards) { btnCards.classList.remove('bg-white', 'text-blue-700', 'shadow-xs'); btnCards.classList.add('text-slate-500'); }
    } else {
      if (btnCards) { btnCards.classList.add('bg-white', 'text-blue-700', 'shadow-xs'); btnCards.classList.remove('text-slate-500'); }
      if (btnTable) { btnTable.classList.remove('bg-white', 'text-blue-700', 'shadow-xs'); btnTable.classList.add('text-slate-500'); }
    }
    renderCurrentResults();
  };

  window.renderVoterResults = function (voters, query = '', pagination = null) {
    isInFamilyFilterMode = false;
    currentFamilyData = null;
    currentResults = voters;
    if (window.APP_STATE) window.APP_STATE.currentResults = voters;
    currentPagination = pagination;
    renderCurrentResults();
  };

  function buildPaginationBarHtml(pagination) {
    if (!pagination || pagination.total === 0 || pagination.isSerialQuery) {
      return '';
    }
    const { currentPage, pageSize, total, totalPages } = pagination;
    const startIdx = total === 0 ? 0 : (currentPage - 1) * pageSize + 1;
    const endIdx = Math.min(startIdx + (currentResults ? currentResults.length : 0) - 1, total);
    const allSelected = currentResults && currentResults.length > 0 && currentResults.every(v => window.APP_STATE.selectedVoters.has(v.id || v.voter_id));

    return `
      <div class="voter-pagination-bar flex flex-wrap items-center justify-between gap-2.5 px-3.5 py-2.5 bg-white rounded-xl border border-slate-200 shadow-2xs text-xs select-none">
        
        <!-- Left: Showing summary -->
        <div class="flex items-center gap-2 font-bold text-slate-700">
          <span>प्रदर्शित:</span>
          <span class="text-blue-700 font-extrabold">${startIdx}–${endIdx}</span>
          <span class="text-slate-300">|</span>
          <span>कुल:</span>
          <span class="font-black text-slate-900 bg-slate-100 px-2 py-0.5 rounded">${total.toLocaleString('en-IN')}</span>
        </div>

        <!-- Center: Pager Navigation Controls -->
        <div class="flex items-center gap-1">
          <button 
            type="button" 
            onclick="window.goToVoterPage(1)" 
            ${currentPage <= 1 ? 'disabled' : ''} 
            class="px-2 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:pointer-events-none font-bold text-slate-700 cursor-pointer transition border border-slate-200"
            title="First Page (पहला पृष्ठ)"
          >
            ⏮
          </button>
          <button 
            type="button" 
            onclick="window.goToVoterPage(${currentPage - 1})" 
            ${currentPage <= 1 ? 'disabled' : ''} 
            class="px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:pointer-events-none font-bold text-slate-700 cursor-pointer transition border border-slate-200"
            title="Previous Page (पिछला)"
          >
            ◀ पिछला
          </button>

          <span class="px-3 py-1 font-black text-xs bg-blue-50 text-blue-900 border border-blue-200 rounded-lg whitespace-nowrap">
            पृष्ठ ${currentPage} / ${totalPages}
          </span>

          <button 
            type="button" 
            onclick="window.goToVoterPage(${currentPage + 1})" 
            ${currentPage >= totalPages ? 'disabled' : ''} 
            class="px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:pointer-events-none font-bold text-slate-700 cursor-pointer transition border border-slate-200"
            title="Next Page (अगला)"
          >
            अगला ▶
          </button>
          <button 
            type="button" 
            onclick="window.goToVoterPage(${totalPages})" 
            ${currentPage >= totalPages ? 'disabled' : ''} 
            class="px-2 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:pointer-events-none font-bold text-slate-700 cursor-pointer transition border border-slate-200"
            title="Last Page (अंतिम पृष्ठ)"
          >
            ⏭
          </button>
        </div>

        <!-- Right: Sort Order & Page Size Selectors -->
        <div class="flex flex-wrap items-center gap-2.5 ml-auto sm:ml-0">
          <div class="flex items-center gap-1.5">
            <label class="text-[11px] text-slate-500 font-bold uppercase">क्रम:</label>
            <select 
              onchange="if(window.setVoterSortOrder) window.setVoterSortOrder(this.value)" 
              class="bg-slate-50 hover:bg-white border border-slate-300 rounded-lg px-2 py-1 font-bold text-slate-900 text-xs cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500 shadow-2xs"
              title="मतदाता क्रमबद्धता (Sort Order)"
            >
              <option value="serial" ${(localStorage.getItem('voter_sort_order') || 'serial') === 'serial' ? 'selected' : ''}>🔢 क्रम संख्या (1, 2, 3...)</option>
              <option value="name" ${(localStorage.getItem('voter_sort_order') || 'serial') === 'name' ? 'selected' : ''}>🔤 नाम (A-Z)</option>
            </select>
          </div>
          <div class="flex items-center gap-1.5">
            <label class="text-[11px] text-slate-500 font-bold uppercase">प्रति पृष्ठ:</label>
            <select 
              onchange="window.setVoterPageSize(this.value)" 
              class="bg-slate-50 hover:bg-white border border-slate-300 rounded-lg px-2 py-1 font-bold text-slate-900 text-xs cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500 shadow-2xs"
            >
              <option value="25" ${pageSize === 25 ? 'selected' : ''}>25</option>
              <option value="50" ${pageSize === 50 ? 'selected' : ''}>50</option>
              <option value="100" ${pageSize === 100 ? 'selected' : ''}>100</option>
              <option value="250" ${pageSize === 250 ? 'selected' : ''}>250</option>
            </select>
          </div>
        </div>

      </div>
    `;
  }

  function renderCurrentResults() {
    const container = document.getElementById('votersResultsContainer');
    if (!container) return;

    if (isInFamilyFilterMode && currentFamilyData) {
      renderFamilyFilterView();
      return;
    }

    if (!currentResults || currentResults.length === 0) {
      container.innerHTML = `
        <div class="text-center py-16 px-4 bg-white rounded-2xl border border-slate-200/80 shadow-xs">
          <div class="text-4xl mb-3">🔍</div>
          <h3 class="text-sm font-bold text-slate-700 mb-1">कोई मतदाता नहीं मिला (No Voters Found)</h3>
          <p class="text-xs text-slate-400 max-w-sm mx-auto">कृपया नाम, पिता/पति का नाम, क्रम संख्या या भाग संख्या बदलकर दोबारा खोजें।</p>
        </div>
      `;
      return;
    }

    if (currentViewMode === 'table') {
      renderTableView(container, currentResults);
    } else {
      renderCardsView(container, currentResults);
    }
  }

  function renderVoterCardHtml(v, extraReason = '') {
    const vid = v.id || v.voter_id;
    const isSelected = window.APP_STATE.selectedVoters.has(vid);
    const nameBilingual = formatBilingual(v.name_en, v.name_hi);
    const relBilingual = formatBilingual(v.rel_name_en || v.relative_name_en, v.rel_name_hi || v.relative_name_hi);
    const relType = v.rel_type || v.relation_type || 'पिता/पति';
    const partNo = v.part_no || 1;
    const slNo = v.sl_no || v.serial_no || '-';
    const struck = isStruckOff(v);
    const phone = v.contact_phone ? v.contact_phone.split(',')[0].trim() : '';

    // Political Sentiment Badge & Left Accent Border
    let sentimentBadge = '';
    let sentimentBorderClass = '';
    const cat = String(v.voter_category !== undefined && v.voter_category !== null ? v.voter_category : '0');
    if (cat === '100') {
      sentimentBadge = `<span class="text-[10px] bg-emerald-50 text-emerald-800 border border-emerald-300 px-2 py-0.5 rounded-md font-bold flex items-center gap-1 shadow-2xs">🟢 100% पक्का</span>`;
      sentimentBorderClass = 'border-l-[3.5px] border-l-emerald-500';
    } else if (cat === '50' || cat === '50_50') {
      sentimentBadge = `<span class="text-[10px] bg-amber-50 text-amber-800 border border-amber-300 px-2 py-0.5 rounded-md font-bold flex items-center gap-1 shadow-2xs">🟡 50-50</span>`;
      sentimentBorderClass = 'border-l-[3.5px] border-l-amber-500';
    } else if (cat === '-1' || cat === 'opposition') {
      sentimentBadge = `<span class="text-[10px] bg-rose-50 text-rose-800 border border-rose-300 px-2 py-0.5 rounded-md font-bold flex items-center gap-1 shadow-2xs">🔴 विरोधी</span>`;
      sentimentBorderClass = 'border-l-[3.5px] border-l-rose-500';
    }

    return `
      <div
        class="voter-card relative rounded-2xl border transition-all p-4 cursor-pointer select-none active:scale-[0.99] ${sentimentBorderClass} ${
          isSelected
            ? 'border-blue-600 bg-blue-50/80 ring-2 ring-blue-500/40 shadow-md'
            : struck
              ? 'bg-rose-50/40 border-rose-200 hover:border-rose-300 shadow-xs'
              : 'bg-white border-slate-200/90 hover:border-slate-300 shadow-xs hover:shadow-sm'
        }"
        onclick="toggleVoterSelection(${vid}, !${isSelected})"
        data-voter-id="${vid}"
      >
        <div class="flex items-center justify-between gap-2 mb-1.5">
          <div class="flex items-center gap-2">
            <input 
              type="checkbox" 
              class="voter-card-checkbox rounded border-slate-300 text-blue-600 focus:ring-blue-500 cursor-pointer w-4 h-4 shrink-0" 
              ${isSelected ? 'checked' : ''} 
              onclick="event.stopPropagation()"
              onchange="toggleVoterSelection(${vid}, this.checked)"
              title="मतदाता चुनें"
            >
            ${(window.isPanchayatCampaign() && v.ward_no && String(v.ward_no).trim() !== '' && String(v.ward_no) !== '0') ? `
              <span class="inline-block bg-indigo-100 text-indigo-950 font-black px-2 py-0.5 rounded-lg text-xs border border-indigo-300 shadow-2xs" title="वार्ड संख्या">
                वार्ड ${v.ward_no}
              </span>
            ` : ''}
            <span class="inline-block bg-amber-100 text-amber-950 font-black px-2 py-0.5 rounded-lg text-xs border border-amber-300 shadow-2xs" title="भाग संख्या व क्रमांक">
              भाग ${partNo} · क्र. ${slNo}
            </span>
          </div>
          <div class="flex items-center gap-1.5 flex-wrap justify-end">
            ${(() => {
              const hNo = cleanIndicText(v.house_no);
              return hNo && hNo !== '0' && hNo !== '-' ? `<span class="text-xs text-slate-400 font-medium">मकान: ${window.escapeHtml ? window.escapeHtml(hNo) : hNo}</span>` : '';
            })()}
            ${v.age ? `<span class="text-xs text-slate-500 font-bold">उम्र: ${v.age} वर्ष</span>` : ''}
          </div>
        </div>

        <div class="mt-1">
          <div class="flex items-start justify-between gap-2">
            <h4 class="voter-name text-base font-black ${struck ? 'text-rose-900 line-through decoration-rose-400/70' : 'text-slate-900'} leading-snug">${nameBilingual}</h4>
            ${struckOffBadgeHtml(v)}
          </div>
          <p class="text-xs text-slate-500 mt-0.5">
            ${relType}: <span class="font-bold text-slate-800">${relBilingual}</span>
          </p>

          <!-- Badges Row: Sentiment, Caste, Phone, Outside, Assigned Karyakarta -->
          <div class="flex items-center gap-1.5 flex-wrap mt-2">
            ${sentimentBadge}
            ${v.caste_tag ? `<span class="text-[10px] bg-purple-50 text-purple-800 border border-purple-200 px-2 py-0.5 rounded-md font-bold">🏷️ ${v.caste_tag}</span>` : ''}
            ${phone ? `<span class="text-[10px] bg-emerald-50 text-emerald-800 border border-emerald-200 px-2 py-0.5 rounded-md font-mono font-bold">📞 ${phone}</span>` : ''}
            ${v.is_outside ? `<span class="text-[10px] bg-indigo-50 text-indigo-800 border border-indigo-200 px-2 py-0.5 rounded-md font-bold">✈️ बाहर</span>` : ''}
            ${v.assigned_karyakarta_name ? `<span class="text-[10px] bg-blue-50 text-blue-800 border border-blue-200 px-2 py-0.5 rounded-md font-bold flex items-center gap-1"><span>👥</span><span>${v.assigned_karyakarta_name}</span></span>` : ''}
            ${v.family_head_name ? `<button type="button" onclick="event.stopPropagation(); window.filterByFamilyHeadName('${v.family_head_name.replace(/'/g, "\\'")}', ${v.ward_no || 0});" class="text-[10px] bg-sky-50 hover:bg-sky-100 text-sky-900 border border-sky-200 px-2 py-0.5 rounded-md font-bold flex items-center gap-1 cursor-pointer transition active:scale-95" title="इस परिवार के सभी सदस्य खोजें"><span>👨‍👩‍👧</span><span>${v.family_head_name}</span></button>` : ''}
            ${v.survey_status === 'DECEASED' ? `<span class="text-[10px] bg-slate-100 text-slate-700 border border-slate-300 px-2 py-0.5 rounded-md font-bold flex items-center gap-1" title="फील्ड सर्वे: शांत / फोत"><span>🕊️</span><span>सर्वे: शांत / फोत</span></span>` : ''}
            ${(v.is_duplicate || v.survey_status === 'DUPLICATE') ? `<button type="button" onclick="event.stopPropagation(); window.filterByDuplicatePair(${v.duplicate_pair_sl || 0}, ${v.duplicate_pair_ward || 0});" class="text-[10px] bg-rose-50 hover:bg-rose-100 text-rose-900 border border-rose-300 px-2 py-0.5 rounded-md font-bold flex items-center gap-1 cursor-pointer transition active:scale-95" title="डुप्लीकेट${v.duplicate_pair_sl ? ` (मूल: ${v.duplicate_pair_ward ? 'वार्ड ' + v.duplicate_pair_ward + ' ' : ''}क्र. #${v.duplicate_pair_sl} - खोजने हेतु क्लिक करें)` : ''}"><span>⚠️</span><span>डुप्लीकेट${v.duplicate_pair_sl ? ` (${v.duplicate_pair_ward ? 'व.' + v.duplicate_pair_ward + ' ' : ''}#${v.duplicate_pair_sl})` : ''}</span></button>` : ''}
            ${(!v.is_duplicate && v.duplicate_pair_sl) ? `<button type="button" onclick="event.stopPropagation(); window.filterByDuplicatePair(${v.duplicate_pair_sl || 0}, ${v.duplicate_pair_ward || 0});" class="text-[10px] bg-blue-50 hover:bg-blue-100 text-blue-900 border border-blue-300 px-2 py-0.5 rounded-md font-bold flex items-center gap-1 cursor-pointer transition active:scale-95" title="दोहराव मिला (प्रतिलिपि: ${v.duplicate_pair_ward ? 'वार्ड ' + v.duplicate_pair_ward + ' ' : ''}क्र. #${v.duplicate_pair_sl} - खोजने हेतु क्लिक करें)"><span>ℹ️</span><span>दोहराव: ${v.duplicate_pair_ward ? 'व.' + v.duplicate_pair_ward + ' ' : ''}#${v.duplicate_pair_sl}</span></button>` : ''}
          </div>

          ${struck && v.del_reason ? `
            <p class="text-[11px] text-rose-800 bg-rose-50 rounded-md px-2 py-0.5 mt-1.5 border border-rose-200/70 font-medium">
              कारण: ${v.del_reason}
            </p>
          ` : ''}
          ${extraReason ? `
            <p class="text-[11px] text-amber-800 bg-amber-50 rounded-md px-2 py-0.5 mt-1.5 border border-amber-200/70 font-medium flex items-center gap-1">
              <span>🔗</span><span>${extraReason}</span>
            </p>
          ` : ''}
        </div>

        <!-- Action Buttons Bar: Tag/Edit, Slip, Family (matching original UI) -->
        <div class="mt-3 pt-2.5 border-t border-slate-100 flex items-center justify-between gap-1.5">
          ${(window.IS_VIEWER || (window.CURRENT_USER && (window.CURRENT_USER.role === 'viewer' || window.CURRENT_USER.isViewer))) ? '' : `
          <button 
            type="button" 
            onclick="event.stopPropagation(); if(typeof openVoterProfileModal === 'function') openVoterProfileModal(${vid});" 
            class="edit-voter-btn px-2.5 py-1.5 rounded-lg bg-indigo-50 hover:bg-indigo-100 active:bg-indigo-200 text-indigo-700 font-bold text-xs border border-indigo-200 transition flex items-center gap-1 cursor-pointer active:scale-95"
            title="मतदाता प्रोफाइल व विवरण (Edit Profile & Tag)"
          >
            <span>✏️</span>
            <span>टैग / एडिट</span>
          </button>
          `}

          <div class="flex items-center gap-1.5">
            <button 
              type="button" 
              onclick="event.stopPropagation(); openFamilyModalForVoter(${vid})" 
              class="px-2 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-xs border border-slate-200 cursor-pointer transition active:scale-95" 
              title="परिवार खोजें"
            >
              <span>👥</span>
            </button>
            <button 
              type="button" 
              onclick="event.stopPropagation(); openSlipModalForVoter(${vid})" 
              class="bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white font-black px-3 py-1.5 rounded-lg text-xs flex items-center gap-1 shadow-2xs transition active:scale-95 cursor-pointer"
              title="पर्ची भेजें"
            >
              <span>💬</span>
              <span>पर्ची</span>
            </button>
          </div>
        </div>

        ${isSelected ? `
          <div class="mt-2.5 pt-2 border-t border-blue-200/80 flex items-center justify-between text-xs text-blue-700 font-black">
            <span class="flex items-center gap-1"><span>✓</span><span>चुना गया (Selected)</span></span>
            <span class="text-[11px] font-semibold text-slate-400">टैप कर हटाएं</span>
          </div>
        ` : ''}
      </div>
    `;
  }

  function renderCardsView(container, voters) {
    const isIntel = window.APP_STATE.mode === 'intel';
    const pagerHtml = buildPaginationBarHtml(currentPagination);

    const cardsHtml = `
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        ${voters.map(v => renderVoterCardHtml(v)).join('')}
      </div>
    `;

    container.innerHTML = `
      <div class="space-y-3">
        ${cardsHtml}
        ${pagerHtml}
      </div>
    `;
  }

  function renderFamilyFilterView() {
    const container = document.getElementById('votersResultsContainer');
    if (!container || !currentFamilyData) return;

    const data = currentFamilyData;
    const directList = data.direct || [];
    const relatedList = data.related || [];
    const totalCount = (data.total_direct !== undefined ? data.total_direct : directList.length) + 
                       (data.total_related !== undefined ? data.total_related : relatedList.length);

    // Derive House & Part description
    let housePartDesc = '';
    if (data.clusters && data.clusters.length > 1) {
      const parts = Array.from(new Set(data.clusters.map(c => c.part_no).filter(Boolean)));
      const houses = Array.from(new Set(data.clusters.map(c => c.house_no).filter(h => h && h !== '0')));
      housePartDesc = `${data.clusters.length} मकान समूह · ${houses.length > 0 ? `मकान #${houses.join(', #')} ` : ''}(भाग ${parts.join(', ')})`;
    } else {
      const firstCluster = (data.clusters && data.clusters[0]) || {};
      const houseNo = firstCluster.house_no || (directList[0] && directList[0].house_no) || '-';
      const partNo = firstCluster.part_no || (directList[0] && directList[0].part_no) || '-';
      const wardNo = firstCluster.ward_no || (directList[0] && directList[0].ward_no);
      const isPanchayat = typeof window.isPanchayatCampaign === 'function' ? window.isPanchayatCampaign() : false;
      const wardPrefix = (isPanchayat && wardNo && String(wardNo).trim() !== '' && String(wardNo) !== '0') ? `वार्ड ${wardNo} · ` : '';
      housePartDesc = `${wardPrefix}मकान #${houseNo} · भाग ${partNo}`;
    }

    // Update Result Counter in header if exists
    const countEl = document.getElementById('resultsCount');
    if (countEl) {
      countEl.innerHTML = `👨‍👩‍👧 <span class="font-black text-blue-700">${directList.length}</span> परिवार सदस्य मिले${relatedList.length > 0 ? ` + <span class="font-bold text-amber-700">${relatedList.length} संभावित नजदीकी शाखाएं</span>` : ''} · परिवार फिल्टर दृश्य`;
    }

    // 1. Contextual Family Filter Banner
    const bannerHtml = `
      <div class="bg-gradient-to-r from-blue-900 via-indigo-900 to-slate-900 text-white rounded-2xl p-4 sm:p-5 shadow-lg border border-blue-700/50 mb-4 select-none">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div class="flex items-start gap-3">
            <div class="text-3xl sm:text-4xl p-2 bg-white/10 rounded-xl backdrop-blur-xs shrink-0">
              👨‍👩‍👧
            </div>
            <div>
              <div class="flex items-center gap-2 flex-wrap">
                <h3 class="text-base sm:text-lg font-black tracking-tight">परिवार फिल्टर दृश्य (Family Filter View)</h3>
                <span class="bg-blue-500/30 text-blue-200 border border-blue-400/40 text-xs font-black px-2.5 py-0.5 rounded-full">
                  ${directList.length} प्रत्यक्ष सदस्य
                </span>
                ${relatedList.length > 0 ? `
                  <span class="bg-amber-400/20 text-amber-200 border border-amber-400/30 text-xs font-bold px-2 py-0.5 rounded-full">
                    ${relatedList.length} संभावित नजदीकी शाखाएं
                  </span>
                ` : ''}
              </div>
              <p class="text-xs text-blue-200/90 font-medium mt-1 flex items-center gap-1.5 flex-wrap">
                <span>🏠 ${housePartDesc}</span>
                ${data.clusters && data.clusters.length > 1 ? `<span class="bg-amber-400/20 text-amber-200 px-2 py-0.5 rounded text-[10px] font-bold border border-amber-400/30">बहु-मकान परिवार (Multi-Cluster)</span>` : ''}
              </p>
            </div>
          </div>
          <div class="flex items-center gap-2 shrink-0 self-end sm:self-auto mt-2 sm:mt-0">
            <button 
              type="button" 
              onclick="window.selectAllFamilyMembers()" 
              class="bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white font-black text-xs px-3.5 py-2 rounded-xl shadow-md border border-blue-400/40 flex items-center gap-1.5 cursor-pointer transition active:scale-95 min-h-[38px]"
              title="परिवार के प्रत्यक्ष सदस्यों को चुनें (संभावित संबंधी बाहर रहेंगे)"
            >
              <span>✓</span>
              <span>सभी परिवार चुनें (${directList.length})</span>
            </button>
            <button 
              type="button" 
              onclick="window.exitFamilyFilterView()" 
              class="bg-white/15 hover:bg-white/25 active:bg-white/30 text-white font-black text-xs px-3.5 py-2 rounded-xl border border-white/20 shadow-xs flex items-center gap-1.5 cursor-pointer transition active:scale-95 min-h-[38px]"
              title="खोज पर वापस जाएं"
            >
              <span>✕</span>
              <span>खोज पर वापस जाएं</span>
            </button>
          </div>
        </div>
      </div>
    `;

    // 2. Cards Rendering
    let cardsContentHtml = '';

    if (data.clusters && data.clusters.length > 1) {
      cardsContentHtml = data.clusters.map((c, clusterIdx) => {
        const clusterDirect = c.direct || [];
        const houseNo = c.house_no || '-';
        const partNo = c.part_no || '-';
        const clusterCount = clusterDirect.length;

        return `
          <div class="mb-5 bg-slate-50/70 p-3 sm:p-4 rounded-2xl border border-slate-200">
            <div class="flex items-center justify-between gap-2 pb-3 mb-3 border-b border-slate-200 flex-wrap">
              <div class="flex items-center gap-2">
                <span class="text-base sm:text-lg">🏠</span>
                <span class="font-black text-xs sm:text-sm text-slate-800">
                  ${(typeof window.isPanchayatCampaign === 'function' && window.isPanchayatCampaign() && c.ward_no && String(c.ward_no).trim() !== '' && String(c.ward_no) !== '0') ? `वार्ड ${c.ward_no} · ` : ''}मकान #${houseNo} · भाग ${partNo}
                </span>
                <span class="bg-blue-100 text-blue-900 border border-blue-300 font-extrabold text-[11px] px-2 py-0.5 rounded-full">
                  ${clusterCount} सदस्य
                </span>
              </div>
              <button 
                type="button"
                onclick="window.selectClusterByIndex(${clusterIdx})"
                class="bg-white hover:bg-blue-50 text-blue-700 hover:text-blue-800 font-bold text-xs px-2.5 py-1.5 rounded-xl border border-blue-300 shadow-2xs flex items-center gap-1 cursor-pointer transition active:scale-95"
                title="इस मकान के सभी चुनें"
              >
                <span>+</span>
                <span>इस मकान के सभी चुनें</span>
              </button>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              ${clusterDirect.map(v => renderVoterCardHtml(v)).join('')}
            </div>
          </div>
        `;
      }).join('');
    } else {
      if (directList.length > 0) {
        cardsContentHtml = `
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            ${directList.map(v => renderVoterCardHtml(v)).join('')}
          </div>
        `;
      } else {
        cardsContentHtml = `
          <div class="text-center py-8 text-xs text-slate-400 bg-white rounded-xl border border-slate-200">
            परिवार के कोई सीधे सदस्य नहीं मिले
          </div>
        `;
      }
    }

    // 3. Related Members Section
    let relatedSectionHtml = '';
    if (relatedList.length > 0) {
      relatedSectionHtml = `
        <div class="mt-6 pt-5 border-t border-slate-200">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-base">🔗</span>
            <h4 class="text-xs sm:text-sm font-black text-amber-900 uppercase tracking-wide">
              संभावित नजदीकी शाखाएं / नजदीकी सदस्य (${relatedList.length})
            </h4>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            ${relatedList.map(v => renderVoterCardHtml(v, v.relation_reason || '')).join('')}
          </div>
        </div>
      `;
    }

    container.innerHTML = `
      <div class="family-filter-container space-y-4">
        ${bannerHtml}
        ${cardsContentHtml}
        ${relatedSectionHtml}
      </div>
    `;
  }

  function renderTableView(container, voters) {
    const isIntel = window.APP_STATE.mode === 'intel';
    const pagerHtml = buildPaginationBarHtml(currentPagination);
    const allSelected = voters && voters.length > 0 && voters.every(v => window.APP_STATE.selectedVoters.has(v.id || v.voter_id));

    container.innerHTML = `
      <div class="space-y-3">
        ${pagerHtml}
        <div class="bg-white rounded-xl border border-slate-200 shadow-xs overflow-x-auto max-h-[72vh] relative">
          <table class="w-full text-left text-xs border-collapse">
            <thead class="sticky top-0 bg-slate-900 text-white uppercase text-[10px] font-extrabold tracking-wider z-10 shadow-xs">
              <tr>
                <th class="p-3 w-10 text-center">
                  <input type="checkbox" id="selectAllVoters" ${allSelected ? 'checked' : ''} onchange="toggleSelectAllResults(this.checked)" class="rounded border-slate-400 cursor-pointer" title="सभी चुनें / हटाएं">
                </th>
                <th class="p-3 w-28 text-center whitespace-nowrap">भाग / क्र.</th>
                <th class="p-3 min-w-[170px]">मतदाता का नाम</th>
                <th class="p-3 min-w-[160px]">पिता / पति</th>
                <th class="p-3 w-28 text-center whitespace-nowrap">आयु / लिंग</th>
                <th class="p-3 w-20 text-center whitespace-nowrap">मकान #</th>
                <th class="p-3 w-32 font-mono whitespace-nowrap">पहचान पत्र (EPIC)</th>
                <th class="p-3 w-36 text-right pr-4 whitespace-nowrap">कार्य (Action)</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              ${voters.map(v => {
                const vid = v.id || v.voter_id;
                const isSelected = window.APP_STATE.selectedVoters.has(vid);
                const phone = v.contact_phone ? v.contact_phone.split(',')[0].trim() : '';
                const struck = isStruckOff(v);

                // Political Sentiment Badge for Table
                let sentimentBadge = '';
                const cat = String(v.voter_category !== undefined && v.voter_category !== null ? v.voter_category : '0');
                if (cat === '100') {
                  sentimentBadge = `<span class="text-[10px] bg-emerald-100 text-emerald-900 border border-emerald-300 px-1.5 py-0.5 rounded font-bold">🟢 100%</span>`;
                } else if (cat === '50' || cat === '50_50') {
                  sentimentBadge = `<span class="text-[10px] bg-amber-100 text-amber-950 border border-amber-300 px-1.5 py-0.5 rounded font-bold">🟡 50-50</span>`;
                } else if (cat === '-1' || cat === 'opposition') {
                  sentimentBadge = `<span class="text-[10px] bg-rose-100 text-rose-900 border border-rose-300 px-1.5 py-0.5 rounded font-bold">🔴 विरोधी</span>`;
                }

                return `
                  <tr class="hover:bg-slate-50 transition ${isSelected ? 'bg-blue-50/60' : struck ? 'bg-rose-50/40' : ''}">
                    <td class="p-3 text-center">
                      <input type="checkbox" ${isSelected ? 'checked' : ''} onchange="toggleVoterSelection(${vid}, this.checked)" class="cursor-pointer">
                    </td>
                    <td class="p-3 text-center whitespace-nowrap">
                      <span class="bg-amber-100 text-amber-950 font-black px-2 py-0.5 rounded text-xs border border-amber-300 shadow-2xs">
                        ${(typeof window.isPanchayatCampaign === 'function' && window.isPanchayatCampaign() && v.ward_no) ? 'W.' + v.ward_no : 'भाग ' + v.part_no} · क्र. ${v.sl_no}
                      </span>
                    </td>
                    <td class="p-3 whitespace-nowrap">
                      <div class="font-black leading-tight ${struck ? 'text-rose-900 line-through decoration-rose-400/70' : 'text-slate-900'}">${formatBilingual(v.name_en, v.name_hi)}</div>
                      <div class="flex items-center gap-1 mt-0.5">
                        ${struckOffBadgeHtml(v)}
                        ${sentimentBadge}
                        ${v.caste_tag ? `<span class="text-[10px] bg-purple-100 text-purple-900 px-1 rounded font-bold">${v.caste_tag}</span>` : ''}
                      </div>
                    </td>
                    <td class="p-3 whitespace-nowrap text-slate-600">
                      <div class="text-[11px]"><span class="font-semibold text-slate-500">${v.rel_type || 'पिता'}:</span> <span class="font-bold text-slate-800">${formatBilingual(v.rel_name_en, v.rel_name_hi)}</span></div>
                    </td>
                    <td class="p-3 text-center whitespace-nowrap font-bold text-slate-700">
                      ${v.age || '-'} Yrs · ${v.gender === 'M' ? 'पुरुष' : v.gender === 'F' ? 'महिला' : (v.gender || '-')}
                    </td>
                    <td class="p-3 text-center font-bold whitespace-nowrap text-slate-800">
                      ${v.house_no && v.house_no !== '0' ? v.house_no : '-'}
                    </td>
                    <td class="p-3 font-mono font-bold whitespace-nowrap text-slate-900">
                      <span class="bg-slate-100 px-1.5 py-0.5 rounded text-xs">${v.epic_no || '-'}</span>
                    </td>
                    <td class="p-3 text-right pr-4 whitespace-nowrap">
                      <div class="flex items-center justify-end gap-1.5">
                        ${(window.IS_VIEWER || (window.CURRENT_USER && (window.CURRENT_USER.role === 'viewer' || window.CURRENT_USER.isViewer))) ? '' : `
                        <button type="button" onclick="if(typeof openVoterProfileModal === 'function') openVoterProfileModal(${vid})" class="edit-voter-btn bg-indigo-50 hover:bg-indigo-100 text-indigo-700 font-bold px-2 py-1.5 rounded-lg text-xs border border-indigo-200 transition cursor-pointer flex items-center gap-1 active:scale-95" title="मतदाता विवरण संपादित करें (Edit Profile & Tag)">
                          <span>✏️</span><span>टैग / एडिट</span>
                        </button>
                        `}
                        <button onclick="openSlipModalForVoter(${vid})" class="bg-emerald-600 hover:bg-emerald-500 text-white font-black px-2.5 py-1.5 rounded-lg text-xs flex items-center gap-1 shadow-2xs transition active:scale-95 cursor-pointer">
                          <span>💬</span><span>पर्ची</span>
                        </button>
                        <button onclick="openFamilyModalForVoter(${vid})" class="bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold px-2 py-1.5 rounded-lg text-xs border border-slate-200 cursor-pointer" title="परिवार खोजें">
                          <span>👨‍👩‍👧</span>
                        </button>
                        ${isIntel ? `
                          <button type="button" data-action="quick-phone" data-vid="${vid}" data-vname="${window.escapeHtml ? window.escapeHtml(v.name_en || '') : (v.name_en || '')}" data-phone="${window.escapeHtml ? window.escapeHtml(phone) : phone}" class="bg-blue-50 text-blue-700 hover:bg-blue-100 px-2 py-1.5 rounded-lg text-xs border border-blue-200 cursor-pointer" title="Phone">
                            <span>📞</span>
                          </button>
                        ` : ''}
                      </div>
                    </td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        </div>
        ${pagerHtml}
      </div>
    `;

    container.querySelectorAll('[data-action="quick-phone"]').forEach(btn => {
      btn.addEventListener('click', function () {
        const vid = this.getAttribute('data-vid');
        const vname = this.getAttribute('data-vname');
        const phone = this.getAttribute('data-phone');
        if (typeof openQuickPhoneModal === 'function') {
          openQuickPhoneModal(vid, vname, phone);
        }
      });
    });
  }

  // Voter Selection Manager
  window.toggleVoterSelection = function (voterId, isSelected) {
    let voter = currentResults.find(v => (v.id || v.voter_id) === voterId);
    if (!voter && isInFamilyFilterMode && currentFamilyData) {
      const allMembers = [...(currentFamilyData.direct || []), ...(currentFamilyData.related || [])];
      voter = allMembers.find(v => (v.id || v.voter_id) === voterId);
    }
    if (!voter) return;

    if (isSelected) {
      window.APP_STATE.selectedVoters.set(voterId, voter);
      if (window.playAudioTone) window.playAudioTone(950, 0.08);
    } else {
      window.APP_STATE.selectedVoters.delete(voterId);
      if (window.playAudioTone) window.playAudioTone(440, 0.08);
    }
    renderCurrentResults();
    updateFloatingActionIsland();
  };

  window.toggleSelectAllResults = function (isChecked) {
    if (isInFamilyFilterMode && currentFamilyData) {
      if (isChecked) {
        if (Array.isArray(currentFamilyData.related)) {
          currentFamilyData.related.forEach(v => {
            const vid = v.id || v.voter_id;
            window.APP_STATE.selectedVoters.delete(vid);
          });
        }
        const directList = (currentFamilyData.direct && currentFamilyData.direct.length > 0)
          ? currentFamilyData.direct
          : (currentFamilyData.clusters || []).flatMap(c => c.direct || []);
        directList.forEach(v => {
          const vid = v.id || v.voter_id;
          window.APP_STATE.selectedVoters.set(vid, v);
        });
      } else {
        currentResults.forEach(v => {
          const vid = v.id || v.voter_id;
          window.APP_STATE.selectedVoters.delete(vid);
        });
      }
    } else {
      currentResults.forEach(v => {
        const vid = v.id || v.voter_id;
        if (isChecked) {
          window.APP_STATE.selectedVoters.set(vid, v);
        } else {
          window.APP_STATE.selectedVoters.delete(vid);
        }
      });
    }
    renderCurrentResults();
    updateFloatingActionIsland();
  };

  window.clearVoterSelection = function () {
    window.APP_STATE.selectedVoters.clear();
    renderCurrentResults();
    updateFloatingActionIsland();
  };

  // Expose renderCurrentResults so external updates (category/assignment) can re-render cards cleanly
  window.renderCurrentResults = renderCurrentResults;

  // Reactively update categories in current results
  window.updateVoterCategoryInResults = function (ids, newCat) {
    const idSet = new Set(ids.map(Number));
    currentResults.forEach(v => {
      const vid = Number(v.id || v.voter_id);
      if (idSet.has(vid)) {
        v.voter_category = newCat;
      }
    });
    renderCurrentResults();
  };

  // Reactively update assigned karyakarta in current results
  window.updateVoterKaryakartaInResults = function (ids, kId, kName) {
    const idSet = new Set(ids.map(Number));
    currentResults.forEach(v => {
      const vid = Number(v.id || v.voter_id);
      if (idSet.has(vid)) {
        v.assigned_karyakarta_id = kId;
        v.assigned_karyakarta_name = kName;
      }
    });
    renderCurrentResults();
  };

  // Reactively update caste in current results
  window.updateVoterCasteInResults = function (ids, newCaste) {
    const idSet = new Set(ids.map(Number));
    currentResults.forEach(v => {
      const vid = Number(v.id || v.voter_id);
      if (idSet.has(vid)) {
        v.caste_tag = newCaste || '';
      }
    });
    renderCurrentResults();
  };

  // Reactively update phone in current results
  window.updateVoterPhoneInResults = function (ids, newPhone) {
    const idSet = new Set(ids.map(Number));
    currentResults.forEach(v => {
      const vid = Number(v.id || v.voter_id);
      if (idSet.has(vid)) {
        v.contact_phone = newPhone || '';
      }
    });
    renderCurrentResults();
  };

  // Reactively update full profile patch in current results
  window.updateVoterProfileInResults = function (ids, patch) {
    const idSet = new Set(ids.map(Number));
    currentResults.forEach(v => {
      const vid = Number(v.id || v.voter_id);
      if (idSet.has(vid)) {
        Object.assign(v, patch);
      }
    });
    renderCurrentResults();
  };

  // Reactively update outside voter status in current results
  window.updateVoterOutsideInResults = function (ids, isOutside) {
    const idSet = new Set(ids.map(Number));
    currentResults.forEach(v => {
      const vid = Number(v.id || v.voter_id);
      if (idSet.has(vid)) {
        v.is_outside = isOutside ? 1 : 0;
      }
    });
    renderCurrentResults();
  };

  // Export Selected Voters directly to UTF-8 BOM CSV
  window.exportSelectedVotersCsv = function () {
    const voters = Array.from(window.APP_STATE.selectedVoters.values());
    if (voters.length === 0) {
      window.showToast('कृपया पहले मतदाता चुनें (Please select voters first)', 'warning');
      return;
    }
    function getSentimentText(cat) {
      const c = String(cat !== undefined && cat !== null ? cat : '0');
      if (c === '100') return '100% पक्का';
      if (c === '50' || c === '50_50') return '50-50';
      if (c === '-1' || c === 'opposition') return 'विरोधी';
      return 'सामान्य';
    }

    const isPanchayat = typeof window.isPanchayatCampaign === 'function' ? window.isPanchayatCampaign() : false;
    const headers = [];
    if (isPanchayat) headers.push('वार्ड नं (Ward No)');
    headers.push(
      'भाग क्र. (Part No)',
      'क्रम संख्या (Serial No)',
      'नाम (हिन्दी)',
      'Name (English)',
      'रिश्तेदार का नाम',
      'संबंध (Relation)',
      'उम्र (Age)',
      'लिंग (Gender)',
      'पहचान पत्र (EPIC No)',
      'मोबाइल (Phone)',
      'मकान नं (House No)',
      'राजनैतिक रुझान (Sentiment)',
      'कार्यकर्ता (Assigned Karyakarta)'
    );
    const rows = voters.map(v => {
      const r = [];
      if (isPanchayat) r.push(v.ward_no || '');
      r.push(
        v.part_no || '',
        v.serial_no || v.sl_no || '',
        `"${(v.name_hi || '').replace(/"/g, '""')}"`,
        `"${(v.name_en || '').replace(/"/g, '""')}"`,
        `"${(v.relative_name_hi || v.rel_name_hi || v.rel_name_en || '').replace(/"/g, '""')}"`,
        `"${(v.relation_type || v.rel_type || '').replace(/"/g, '""')}"`,
        v.age || '',
        v.gender || '',
        `"${(v.epic_no || '').replace(/"/g, '""')}"`,
        `"${(v.contact_phone || '').replace(/"/g, '""')}"`,
        `"${(v.house_no || '').replace(/"/g, '""')}"`,
        `"${getSentimentText(v.voter_category)}"`,
        `"${(v.assigned_karyakarta_name || '').replace(/"/g, '""')}"`
      );
      return r;
    });

    const csvContent = '\uFEFF' + [headers.join(','), ...rows.map(r => r.join(','))].join('\r\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `voters_export_${voters.length}_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    if (window.showToast) window.showToast(`${voters.length} मतदाताओं की CSV सूची डाउनलोड हो गई!`, 'success');
  };

  // Floating Action Island (Bottom of Screen)
  function updateFloatingActionIsland() {
    let island = document.getElementById('floatingActionIsland');
    const bar = document.getElementById('floatingActionBar');
    const count = window.APP_STATE.selectedVoters.size;

    if (bar) {
      if (count === 0) {
        bar.classList.add('hidden');
        bar.classList.remove('flex');
      } else {
        bar.classList.remove('hidden');
        bar.classList.add('flex');
        const countText = document.getElementById('selectedCountText');
        if (countText) countText.textContent = `${count} चुना गया`;

        // Floating Edit / Bulk Edit Button: Always visible whenever voter(s) are selected!
        const editBtn = document.getElementById('floatingEditBtn');
        const editBtnText = document.getElementById('floatingEditBtnText');
        if (editBtn) {
          editBtn.classList.remove('hidden');
          editBtn.classList.add('flex');
          if (editBtnText) {
            editBtnText.textContent = count > 1 ? 'बल्क एडिट' : 'एडिट';
          }
          editBtn.title = count > 1 
            ? `${count} मतदाताओं का सामूहिक संपादन (Bulk Edit)` 
            : 'मतदाता विवरण संपादित करें (Edit Profile)';
        }
      }
    }

    if (count === 0) {
      if (island) island.classList.add('translate-y-28', 'opacity-0', 'pointer-events-none');
      return;
    }

    if (bar) {
      if (island) island.classList.add('hidden');
      return;
    }

    if (!island) {
      island = document.createElement('div');
      island.id = 'floatingActionIsland';
      island.className = 'fixed z-40 bg-slate-900/98 backdrop-blur-md text-white border border-slate-700/90 rounded-2xl shadow-2xl px-3 py-2 sm:px-4 sm:py-2.5 flex items-center gap-2.5 transition-all duration-300 max-w-2xl w-[95%] sm:w-auto';
      document.body.appendChild(island);
    }

    // Dock above mobile bottom nav on small screens, bottom-5 on desktop (centered within main content area)
    if (window.innerWidth < 768) {
      island.style.bottom = 'calc(env(safe-area-inset-bottom, 0px) + 72px)';
      island.style.left = '50%';
    } else {
      island.style.bottom = '20px';
      const sidebar = document.getElementById('appSidebar');
      const sidebarWidth = sidebar && sidebar.offsetParent !== null ? sidebar.offsetWidth : 0;
      island.style.left = `calc(50% + ${Math.round(sidebarWidth / 2)}px)`;
    }
    island.style.transform = 'translateX(-50%)';

    island.innerHTML = `
      <div class="flex items-center gap-2 shrink-0">
        <span class="bg-blue-600 text-white font-black px-2.5 py-1 rounded-xl text-xs flex items-center gap-1 shadow-xs">
          ✓ <span id="islandCount">${count}</span> चुना गया
        </span>
        <button onclick="clearVoterSelection()" class="text-xs text-slate-400 hover:text-white font-bold p-1 cursor-pointer transition hover:scale-110" title="चयन हटाएं (Clear Selection)">✕</button>
      </div>

      <div class="flex items-center gap-1.5 flex-1 justify-end">
        <button 
          type="button" 
          onclick="if(window.triggerHaptic) window.triggerHaptic('light'); openProfileForSelected()" 
          class="bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 text-white font-black text-xs px-3 py-2 rounded-xl shadow-xs flex items-center gap-1.5 cursor-pointer transition active:scale-95 min-h-[38px]"
          title="${count > 1 ? `${count} मतदाताओं का सामूहिक संपादन (Bulk Edit)` : 'मतदाता विवरण संपादित करें (Edit Profile)'}"
        >
          <span>✏️</span>
          <span>${count > 1 ? 'बल्क एडिट' : 'एडिट'}</span>
        </button>

        <button 
          type="button" 
          onclick="if(window.triggerHaptic) window.triggerHaptic('light'); openFamilyForSelected()" 
          class="bg-slate-800 hover:bg-slate-700 active:bg-slate-700 text-slate-200 font-black text-xs px-3 py-2 rounded-xl border border-slate-600 shadow-xs flex items-center gap-1.5 cursor-pointer transition active:scale-95 min-h-[38px]"
          title="परिवार के अन्य सदस्य देखें (View Family Members)"
        >
          <span>👨‍👩‍👧</span>
          <span class="hidden sm:inline">परिवार</span>
        </button>

        <button 
          type="button" 
          onclick="if(window.triggerHaptic) window.triggerHaptic('medium'); openSlipForSelected()" 
          class="bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white font-black text-xs px-3.5 py-2 rounded-xl shadow-md flex items-center gap-1.5 cursor-pointer transition active:scale-95 min-h-[38px]"
          title="व्हाट्सएप पर्ची भेजें (Send WhatsApp Slip)"
        >
          <span>💬</span>
          <span>पर्ची भेजें</span>
        </button>
      </div>
    `;

    island.classList.remove('translate-y-28', 'opacity-0', 'pointer-events-none', 'hidden');
    island.classList.add('translate-y-0', 'opacity-100');
  }

  window.updateFloatingActionIsland = updateFloatingActionIsland;

  // Inline Family Filter Implementation
  window.filterByFamilyForSelected = async function (customVoterIds = null) {
    let ids = null;
    if (customVoterIds) {
      ids = Array.isArray(customVoterIds) ? customVoterIds : [customVoterIds];
    } else {
      ids = Array.from(window.APP_STATE.selectedVoters.keys());
    }

    if (!ids || ids.length === 0) {
      if (window.showToast) {
        window.showToast('कृपया पहले किसी मतदाता को चुनें', 'warning');
      }
      return;
    }

    if (!isInFamilyFilterMode) {
      previousSearchState = {
        results: currentResults ? [...currentResults] : [],
        pagination: currentPagination ? { ...currentPagination } : null
      };
    }

    isInFamilyFilterMode = true;

    const container = document.getElementById('votersResultsContainer');
    if (container) {
      container.innerHTML = `
        <div class="py-16 text-center bg-white rounded-2xl border border-slate-200/90 shadow-xs">
          <div class="inline-block text-3xl mb-2 animate-bounce">👨‍👩‍👧</div>
          <h3 class="text-sm font-black text-slate-800">परिवार फिल्टर दृश्य लोड हो रहा है...</h3>
          <p class="text-xs text-slate-400 mt-1 max-w-sm mx-auto">मकान संख्या व परिवार संबंधों का विश्लेषण किया जा रहा है...</p>
        </div>
      `;
    }

    try {
      const tenant = (window.APP_STATE && window.APP_STATE.tenant) || new URLSearchParams(window.location.search).get('tenant') || '';
      let url = `/api/family_smart?voter_ids=${encodeURIComponent(ids.join(','))}`;
      if (tenant) {
        url += `&tenant=${encodeURIComponent(tenant)}`;
      }

      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`Server returned ${res.status}: ${res.statusText}`);
      }
      const data = await res.json();
      currentFamilyData = data;

      // Populate currentResults with all family members for selection lookup
      // Invert insertion order so direct records overwrite related records, ensuring direct records always win
      const membersMap = new Map();
      (data.related || []).forEach(v => membersMap.set(v.id || v.voter_id, v));
      (data.direct || []).forEach(v => membersMap.set(v.id || v.voter_id, v));
      currentResults = Array.from(membersMap.values());
      if (window.APP_STATE) {
        window.APP_STATE.currentResults = currentResults;
      }
      currentPagination = null;

      renderFamilyFilterView();
      updateFloatingActionIsland();
    } catch (err) {
      console.error('Filter by family error:', err);
      if (window.showToast) {
        window.showToast('परिवार जानकारी लोड करने में समस्या आई', 'error');
      }
      if (container) {
        container.innerHTML = `
          <div class="py-12 text-center bg-white rounded-2xl border border-rose-200 shadow-xs px-4">
            <div class="text-3xl mb-2 text-rose-500">⚠️</div>
            <h3 class="text-sm font-bold text-rose-900">परिवार लोड करने में त्रुटि हुई</h3>
            <p class="text-xs text-slate-500 mt-1 mb-4">सर्वर से परिवार की जानकारी प्राप्त नहीं हो सकी।</p>
            <button onclick="window.exitFamilyFilterView()" class="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-xs rounded-xl border border-slate-300 cursor-pointer">
              ✕ खोज पर वापस जाएं
            </button>
          </div>
        `;
      }
    }
  };

  window.exitFamilyFilterView = function (skipRender = false) {
    isInFamilyFilterMode = false;
    currentFamilyData = null;
    if (previousSearchState) {
      currentResults = previousSearchState.results || [];
      currentPagination = previousSearchState.pagination || null;
      previousSearchState = null;
      if (window.APP_STATE) {
        window.APP_STATE.currentResults = currentResults;
      }
    }
    if (!skipRender) {
      renderCurrentResults();
      updateFloatingActionIsland();
      const countEl = document.getElementById('resultsCount');
      if (countEl && currentPagination) {
        const p = currentPagination;
        countEl.textContent = `${p.total.toLocaleString('en-IN')} मतदाता मिले · पृष्ठ ${p.currentPage}/${p.totalPages}`;
      }
    }
  };

  window.selectAllFamilyMembers = function () {
    if (!currentFamilyData) return;

    // Deselect any voters currently selected that are in currentFamilyData.related
    if (Array.isArray(currentFamilyData.related)) {
      currentFamilyData.related.forEach(v => {
        const vid = v.id || v.voter_id;
        window.APP_STATE.selectedVoters.delete(vid);
      });
    }

    // Select ONLY direct family members
    const directMembers = (currentFamilyData.direct && currentFamilyData.direct.length > 0)
      ? currentFamilyData.direct
      : (currentFamilyData.clusters || []).flatMap(c => c.direct || []);

    directMembers.forEach(v => {
      const vid = v.id || v.voter_id;
      window.APP_STATE.selectedVoters.set(vid, v);
    });

    renderCurrentResults();
    updateFloatingActionIsland();
    if (window.showToast) {
      window.showToast(`परिवार के सभी ${directMembers.length} सदस्य चयनित किए गए`, 'success');
    }
  };

  window.selectClusterMembers = function (members) {
    if (!Array.isArray(members)) return;
    members.forEach(v => {
      const vid = v.id || v.voter_id;
      window.APP_STATE.selectedVoters.set(vid, v);
    });
    renderCurrentResults();
    updateFloatingActionIsland();
    if (window.showToast) {
      window.showToast(`${members.length} सदस्य चयनित किए गए`, 'success');
    }
  };

  window.selectClusterByIndex = function (idx) {
    if (!currentFamilyData || !currentFamilyData.clusters || !currentFamilyData.clusters[idx]) return;
    window.selectClusterMembers(currentFamilyData.clusters[idx].direct || []);
  };

  window.openFamilyForSelected = function () {
    return window.filterByFamilyForSelected();
  };

  window.openProfileForSelected = function (voterId) {
    if (window.CURRENT_USER && window.CURRENT_USER.role === 'viewer') {
      if (window.showToast) window.showToast('दर्शक (Viewer) केवल देखने के लिए अधिकृत है (Viewer role is read-only)', 'info');
      return;
    }
    if (voterId) {
      if (typeof window.openVoterProfileModal === 'function') {
        return window.openVoterProfileModal(voterId);
      }
    }
    const selected = window.APP_STATE && window.APP_STATE.selectedVoters;
    if (!selected || selected.size === 0) {
      if (window.showToast) window.showToast('कृपया पहले किसी मतदाता को चुनें (Please select a voter)', 'warning');
      return;
    }
    const ids = Array.from(selected.keys());
    if (ids.length > 1 && typeof window.openBulkProfileModal === 'function') {
      return window.openBulkProfileModal(ids);
    }
    if (typeof window.openVoterProfileModal === 'function') {
      return window.openVoterProfileModal(ids[0]);
    }
  };

  window.openFamilyModalForVoter = function (voterId) {
    if (voterId) {
      if (!window.APP_STATE.selectedVoters.has(voterId)) {
        const voter = currentResults.find(v => (v.id || v.voter_id) === voterId);
        if (voter) {
          window.APP_STATE.selectedVoters.set(voterId, voter);
        }
      }
      return window.filterByFamilyForSelected([voterId]);
    } else {
      return window.filterByFamilyForSelected();
    }
  };

  window.filterByFamilyHeadName = function (headName, wardNo) {
    if (typeof window.triggerHaptic === 'function') window.triggerHaptic('light');
    const nameInput = document.getElementById('nameSearchInput');
    if (nameInput) {
      nameInput.value = headName;
      if (typeof window.triggerOmniSearch === 'function') {
        window.triggerOmniSearch();
      }
    }
  };

  window.filterByDuplicatePair = function (serialNo, wardNo) {
    if (typeof window.triggerHaptic === 'function') window.triggerHaptic('light');
    if (wardNo) {
      const wardSelect = document.getElementById('wardFilterSelect') || document.getElementById('boothFilterSelect');
      if (wardSelect) {
        wardSelect.value = String(wardNo);
        wardSelect.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }
    const nameInput = document.getElementById('nameSearchInput') || document.getElementById('serialSearchInput');
    if (nameInput) {
      nameInput.value = String(serialNo);
      if (typeof window.triggerOmniSearch === 'function') {
        window.triggerOmniSearch();
      }
    }
  };

  function cleanIndicText(text) {
    if (!text) return '';
    return String(text)
      .replace(/\ufffd/g, '')
      .replace(/\s*(Photo\s*Available|Photo\s*Not\s*Available|PhotoAvailable|Available|Photo)+$/gi, '')
      // Canonical Devanagari vowel normalization (replace invalid 'अ' + vowel signs)
      .replace(/\u0905\u093e\u0947/g, '\u0913')
      .replace(/\u0905\u093e\u0948/g, '\u0914')
      .replace(/\u0905\u093e\u0945/g, '\u0911')
      .replace(/\u0905\u093e/g, '\u0906')
      .replace(/\u0905\u094b/g, '\u0913')
      .replace(/\u0905\u094c/g, '\u0914')
      .replace(/\u0905[\u0949\u0945]/g, '\u0911')
      .replace(/\u0905\u0948/g, '\u0910')
      .replace(/\u0905\u0947/g, '\u090f')
      .replace(/\u0905\u093f/g, '\u0907')
      .replace(/\u0905\u0940/g, '\u0908')
      .replace(/\u0905\u0941/g, '\u0909')
      .replace(/\u0905\u0942/g, '\u090a')
      .replace(/(^|\s+)[\u0901-\u0903\u093a-\u094f\u0962\u0963]+/g, '$1')
      .replace(/\u0947\u093e/g, '\u094b')
      .replace(/([\u093e-\u094c])\1+/g, '$1')
      .replace(/([\u093e-\u094c])[\u093e-\u094c]+/g, '$1')
      .replace(/([^\u0915-\u0939])\u093c/g, '$1')
      .replace(/\u094d+$/g, '')
      .replace(/\u094d\s+/g, '\u094d')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function formatBilingual(en, hi) {
    const esc = window.escapeHtml || function(s){ return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); };
    let e = (en || '').replace(/\ufffd/g, '').trim();
    let h = (hi || '').replace(/\ufffd/g, '').trim();

    // Client-side Indic cleanup for OCR dropout & orphan combining marks / matras
    if (h) {
      h = h.replace(/(?:^|\s)ानी(?=\s|राम|दास|लाल|चंद|चन्द|सिंह|देवी|$)/g, ' ज्ञानी')
           .replace(/(?:^|\s)ानीराम/g, ' ज्ञानीराम')
           .replace(/(?:^|\s)ारका\s*प्रसाद/g, ' द्वारका प्रसाद')
           .replace(/(?:^|\s)ूल(?=\s*सिंह)/g, ' मूल')
           .replace(/(?:^|\s)ूलचन्द/g, ' मूलचन्द')
           .replace(/(?:^|\s)ेम(?=\s*(?:देवी|घुमरिया|लता|सिंह))/g, ' प्रेम')
           .replace(/(?:^|\s)ान(?=\s+(?:प्रकाश|कंवर|सिंह|चंद|ललित))/g, ' ज्ञान')
           .replace(/(?:^|\s)ानचंद/g, ' ज्ञानचंद')
           .replace(/(?:^|\s)ानमती/g, ' भानमती')
           .replace(/(?:^|\s)अा\s*राम(?=\s|$)/g, ' अम्रा राम')
           .replace(/(?:^|\s)ूसा(?:\s*राम|राम)/g, ' फूसा राम')
           .replace(/(?:^|\s)ुसाराम/g, ' फूसा राम')
           .replace(/(?:^|\s)ुसी\s*देवी/g, ' फुसी देवी')
           .replace(/(?:^|\s)ुसी(?=\s|$)/g, ' फुसी')
           .replace(/(?:^|\s)सुा\s*राम/g, ' सुग्रा राम');
      h = cleanIndicText(h);
    }

    if (e) {
      e = e.replace(/\s*(Photo\s*Available|Photo\s*Not\s*Available|PhotoAvailable|Available|Photo)+$/gi, '')
           .replace(/\bA Ram\b/gi, 'Amra Ram')
           .replace(/\bSa Ram\b/gi, 'Fusa Ram')
           .replace(/\bSaram\b/gi, 'Fusa Ram')
           .replace(/\bSi Devi\b/gi, 'Fusi Devi')
           .replace(/\bSi\b/gi, 'Fusi')
           .replace(/\bSu Ram\b/gi, 'Sugra Ram')
           .replace(/\bRathaur\b/gi, 'Rathore')
           .replace(/\bRathau\b/gi, 'Rathore')
           .replace(/\bRathod\b/gi, 'Rathore')
           .replace(/\bNi Ram\b/gi, 'Gyani Ram')
           .replace(/\bNiram\b/gi, 'Gyaniram')
           .replace(/\bJnani\b/gi, 'Gyani')
           .replace(/\bJnan\b/gi, 'Gyani')
           .replace(/\bChaudhri\b/gi, 'Chaudhary')
           .replace(/\s+/g, ' ').trim();
    }

    if (e && h && e.toLowerCase() !== h.toLowerCase()) return `${esc(e)} / ${esc(h)}`;
    return esc(e || h);
  }

})();
