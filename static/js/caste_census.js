/**
 * caste_census.js - Radically Simple PWA Community Demographics Engine
 * Touch-First Mobile Cards, Instant Ward/Caste Sheets, 1-Tap WhatsApp Slips
 * Part of SmartBooth Mobile Platform
 */

(function () {
  'use strict';

  let currentCasteVoters = [];
  let selectedCasteVoters = new Map();
  let cachedTaggedCastes = [];
  let cachedCastePresets = [];
  let activeSelectedCaste = '';
  let activeSelectedCasteLabel = '🏷️ सभी जातियां';
  let currentViewMode = localStorage.getItem('caste_view_mode') || 'cards';
  let activeCasteVoiceSession = null;

  // View Mode Switcher: Cards vs Table
  window.setCasteViewMode = function (mode) {
    currentViewMode = mode;
    localStorage.setItem('caste_view_mode', mode);
    const cardsCont = document.getElementById('casteCardsContainer');
    const tableCont = document.getElementById('casteTableContainer');
    const btnCards = document.getElementById('btnViewCards');
    const btnTable = document.getElementById('btnViewTable');

    if (mode === 'table') {
      if (cardsCont) cardsCont.classList.add('hidden');
      if (tableCont) tableCont.classList.remove('hidden');
      if (btnTable) { btnTable.classList.add('bg-white', 'text-indigo-700', 'font-black', 'shadow-2xs'); btnTable.classList.remove('text-slate-600'); }
      if (btnCards) { btnCards.classList.remove('bg-white', 'text-indigo-700', 'font-black', 'shadow-2xs'); btnCards.classList.add('text-slate-600'); }
    } else {
      if (cardsCont) cardsCont.classList.remove('hidden');
      if (tableCont) tableCont.classList.add('hidden');
      if (btnCards) { btnCards.classList.add('bg-white', 'text-indigo-700', 'font-black', 'shadow-2xs'); btnCards.classList.remove('text-slate-600'); }
      if (btnTable) { btnTable.classList.remove('bg-white', 'text-indigo-700', 'font-black', 'shadow-2xs'); btnTable.classList.add('text-slate-600'); }
    }
    renderCurrentResults();
  };

  // Caste Bottom Sheet Modal Handlers
  window.openCasteSelectSheet = function () {
    const sheet = document.getElementById('casteSelectSheet');
    if (sheet) {
      sheet.classList.remove('hidden');
      const inp = document.getElementById('casteSheetFilterInput');
      if (inp) {
        inp.value = '';
        setTimeout(() => inp.focus(), 150);
      }
      populateCasteSheetList();
      if (window.triggerHaptic) window.triggerHaptic('selection');
    }
  };

  window.closeCasteSelectSheet = function () {
    const sheet = document.getElementById('casteSelectSheet');
    if (sheet) sheet.classList.add('hidden');
  };

  window.filterCasteSheetItems = function (q) {
    const query = (q || '').trim().toLowerCase();
    populateCasteSheetList(query);
  };

  window.selectCasteFromSheet = function (val, label) {
    activeSelectedCaste = (val || '').trim();
    const isUntagged = (
      activeSelectedCaste === 'अन्य / गैर-टैग' ||
      activeSelectedCaste === 'अन्य/गैर-टैग' ||
      activeSelectedCaste === 'untagged' ||
      activeSelectedCaste.toLowerCase() === 'other'
    );
    if (isUntagged) {
      activeSelectedCaste = 'अन्य / गैर-टैग';
      activeSelectedCasteLabel = '⚪ अनिर्धारित / गैर-टैग';
    } else {
      activeSelectedCasteLabel = label || (activeSelectedCaste ? `🏷️ ${activeSelectedCaste}` : '🏷️ सभी जातियां');
    }
    const labelEl = document.getElementById('selectedCasteLabel');
    if (labelEl) labelEl.textContent = activeSelectedCasteLabel;
    closeCasteSelectSheet();
    if (window.triggerHaptic) window.triggerHaptic('success');
    window.triggerCasteSearch();
  };

  function populateCasteSheetList(filterQuery = '') {
    const cont = document.getElementById('casteSheetList');
    if (!cont) return;

    let items = cachedTaggedCastes || [];
    if (filterQuery) {
      items = items.filter(c => (c.tag || '').toLowerCase().includes(filterQuery));
    }

    let html = `
      <button 
        type="button" 
        onclick="selectCasteFromSheet('', '🏷️ सभी जातियां')" 
        class="w-full text-left p-2.5 rounded-xl hover:bg-indigo-50 font-bold text-slate-800 flex items-center justify-between transition cursor-pointer ${!activeSelectedCaste ? 'bg-indigo-50 border border-indigo-200 text-indigo-900 font-black' : ''}"
      >
        <span>🏷️ सभी जातियां</span>
        <span class="text-slate-400 font-semibold">सभी देखें</span>
      </button>
    `;

    if (items.length === 0) {
      html += `<div class="p-4 text-center text-slate-400 font-bold text-xs">कोई जाति नहीं मिली</div>`;
    } else {
      html += items.map(c => {
        const isSel = activeSelectedCaste && activeSelectedCaste.toLowerCase() === (c.tag || '').toLowerCase();
        const cnt = Number(c.count || 0).toLocaleString('en-IN');
        return `
          <button 
            type="button" 
            onclick="selectCasteFromSheet('${escapeHtml(c.tag)}', '🏷️ ${escapeHtml(c.tag)} (${cnt})')" 
            class="w-full text-left p-2.5 rounded-xl hover:bg-indigo-50 font-bold text-slate-800 flex items-center justify-between transition cursor-pointer ${isSel ? 'bg-indigo-50 border border-indigo-200 text-indigo-900 font-black' : ''}"
          >
            <span>🏷️ ${escapeHtml(c.tag)}</span>
            <span class="bg-slate-100 text-slate-700 font-extrabold px-2 py-0.5 rounded-md text-[11px]">${cnt}</span>
          </button>
        `;
      }).join('');
    }

    cont.innerHTML = html;
  }

  // Voice Search Handler
  window.toggleCasteVoiceSearch = function (evt = null) {
    if (evt) {
      if (typeof evt.preventDefault === 'function') evt.preventDefault();
      if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
    }

    const SpeechAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechAPI) {
      if (window.showToast) window.showToast('वॉइस सर्च इस ब्राउज़र में समर्थित नहीं है', 'error', 3000);
      return;
    }

    if (activeCasteVoiceSession) {
      try { activeCasteVoiceSession.stop(); } catch (e) {}
      stopCasteVoiceSearch();
      return;
    }

    const input = document.getElementById('casteSearchInput');
    const micBtn = document.getElementById('casteVoiceSearchBtn');
    if (input) {
      input.blur();
      input.setAttribute('inputmode', 'none');
      input.readOnly = true;
    }

    try {
      const recognition = new SpeechAPI();
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.lang = 'hi-IN';

      recognition.onstart = function () {
        if (micBtn) {
          micBtn.classList.remove('text-slate-400');
          micBtn.classList.add('bg-rose-500', 'text-white', 'animate-pulse');
        }
        if (input) {
          input.placeholder = '🔴 बोलिए... (उदा. शर्मा, बुनकर, मेघवाल)';
          input.classList.add('ring-2', 'ring-rose-500');
        }
        if (window.triggerHaptic) window.triggerHaptic('medium');
        if (window.showToast) window.showToast('🔴 बोलिए... (Listening)', 'info', 2000);
      };

      recognition.onresult = function (event) {
        const transcript = (event.results && event.results[0] && event.results[0][0])
          ? event.results[0][0].transcript.trim()
          : '';
        if (transcript) {
          const inp = document.getElementById('casteSearchInput');
          if (inp) {
            inp.value = transcript;
            inp.removeAttribute('inputmode');
            inp.readOnly = false;
            if (window.triggerHaptic) window.triggerHaptic('success');
            window.triggerCasteSearch();
          }
        }
        stopCasteVoiceSearch();
      };

      recognition.onerror = function () {
        stopCasteVoiceSearch();
      };

      recognition.onend = function () {
        stopCasteVoiceSearch();
      };

      activeCasteVoiceSession = recognition;
      recognition.start();
    } catch (err) {
      stopCasteVoiceSearch();
    }
  };

  function stopCasteVoiceSearch() {
    if (activeCasteVoiceSession) {
      try { activeCasteVoiceSession.abort(); } catch (e) {}
      activeCasteVoiceSession = null;
    }
    const micBtn = document.getElementById('casteVoiceSearchBtn');
    if (micBtn) {
      micBtn.classList.remove('bg-rose-500', 'text-white', 'animate-pulse');
      micBtn.classList.add('text-slate-400');
    }
    const input = document.getElementById('casteSearchInput');
    if (input) {
      input.removeAttribute('inputmode');
      input.readOnly = false;
      input.placeholder = 'उपनाम या जाति लिखें (उदा. शर्मा, बुनकर, मेघवाल)...';
      input.classList.remove('ring-2', 'ring-rose-500');
    }
  }

  window.clearCasteSearch = function () {
    const input = document.getElementById('casteSearchInput');
    if (input) {
      input.value = '';
      input.blur();
    }
    activeSelectedCaste = '';
    activeSelectedCasteLabel = '🏷️ सभी जातियां';
    const labelEl = document.getElementById('selectedCasteLabel');
    if (labelEl) labelEl.textContent = activeSelectedCasteLabel;

    window.triggerCasteSearch();
  };

  function getActiveTenant() {
    try {
      const urlParams = new URLSearchParams(window.location.search);
      return urlParams.get('tenant') || window.CURRENT_TENANT || (window.CAMPAIGN_CONFIG && window.CAMPAIGN_CONFIG.slug) || '';
    } catch (e) {
      return window.CURRENT_TENANT || '';
    }
  }

  async function loadCastePresetsAndTags() {
    try {
      const tenant = getActiveTenant();
      const url = tenant ? `/api/caste/presets?tenant=${encodeURIComponent(tenant)}` : '/api/caste/presets';
      const res = await fetch(url);
      const data = await res.json();
      if (data && data.success) {
        cachedCastePresets = data.presets || [];
        cachedTaggedCastes = data.tagged_castes || [];
        populateCasteSheetList();
      }
    } catch (e) {
      console.warn('Could not load caste presets:', e);
    }
  }

  // Main Elector Search & Ingestion Trigger
  window.triggerCasteSearch = async function () {
    const input = document.getElementById('casteSearchInput');
    const rawInputVal = (input?.value || '').trim();
    const casteFilter = (activeSelectedCaste || '').trim();

    const filterEl = document.getElementById('castePartFilter');
    const rawFilterVal = (filterEl?.value || '').trim();
    let filterKey = 'part_no';
    let partFilter = '';
    if (rawFilterVal) {
      if (/^ward:/i.test(rawFilterVal)) {
        filterKey = 'ward_no';
        partFilter = rawFilterVal.replace(/^ward:/i, '').trim();
      } else if (/^(part|booth):/i.test(rawFilterVal)) {
        filterKey = 'part_no';
        partFilter = rawFilterVal.replace(/^(part|booth):/i, '').trim();
      } else {
        filterKey = filterEl?.dataset?.filterKey || 'part_no';
        partFilter = rawFilterVal;
      }
    }

    const cardsCont = document.getElementById('casteCardsContainer');
    const tableBody = document.getElementById('casteTableBody');
    const summaryEl = document.getElementById('casteResultsSummary');

    if (cardsCont) {
      cardsCont.innerHTML = `
        <div class="text-center py-12 px-4 bg-white rounded-2xl border border-slate-200 shadow-xs">
          <div class="inline-block animate-spin text-2xl mb-2">⏳</div>
          <h3 class="text-sm font-bold text-slate-800">मतदाता खोज जारी है...</h3>
          <p class="text-xs text-slate-400 mt-0.5">समुदाय व पारिवारिक क्लस्टर का मिलान किया जा रहा है</p>
        </div>
      `;
    }

    try {
      const tenant = getActiveTenant();
      let url = `/api/caste/census?include_family=1`;
      if (casteFilter) url += `&caste=${encodeURIComponent(casteFilter)}`;
      if (rawInputVal) url += `&query=${encodeURIComponent(rawInputVal)}`;
      if (tenant) url += `&tenant=${encodeURIComponent(tenant)}`;
      if (partFilter) url += `&${filterKey}=${encodeURIComponent(partFilter)}`;

      const res = await fetch(url);
      const data = await res.json();

      if (!data.success) {
        if (cardsCont) cardsCont.innerHTML = `<div class="p-8 text-center bg-white rounded-2xl border border-rose-200 text-rose-500 font-bold text-xs">${data.error || 'खोज में त्रुटि आई'}</div>`;
        return;
      }

      currentCasteVoters = data.voters || [];
      selectedCasteVoters.clear();
      updateCasteFloatingIsland();

      const tot = data.total_voters ?? currentCasteVoters.length;
      const phoneCount = data.phone_count ?? currentCasteVoters.filter(v => Boolean(v.contact_phone || v.phone)).length;
      const phonePct = data.phone_pct ?? (tot > 0 ? Math.round((phoneCount / tot) * 100) : 0);

      // Update 1-line clean summary
      if (summaryEl) {
        const queryLabel = rawInputVal ? `"${rawInputVal}"` : activeSelectedCasteLabel;
        summaryEl.innerHTML = `<strong class="text-slate-900">${tot.toLocaleString('en-IN')}</strong> मतदाता मिले <span class="text-slate-400 font-normal">(${phoneCount.toLocaleString('en-IN')} फोन • ${phonePct}% कवरेज)</span>`;
      }

      renderCurrentResults();
    } catch (err) {
      console.error('triggerCasteSearch error:', err);
      if (cardsCont) cardsCont.innerHTML = `<div class="p-8 text-center bg-white rounded-2xl border border-rose-200 text-rose-500 font-bold text-xs">नेटवर्क त्रुटि आई। कृपया पुनः प्रयास करें।</div>`;
    }
  };

  // Render Current Results (Cards or Table)
  function renderCurrentResults() {
    const cardsCont = document.getElementById('casteCardsContainer');
    const tableBody = document.getElementById('casteTableBody');

    if (!currentCasteVoters || currentCasteVoters.length === 0) {
      if (cardsCont) {
        cardsCont.innerHTML = `
          <div class="text-center py-12 px-4 bg-white rounded-2xl border border-slate-200 shadow-xs">
            <div class="text-3xl mb-2 text-slate-300">🔍</div>
            <h3 class="text-sm font-bold text-slate-700">कोई मतदाता नहीं मिला</h3>
            <p class="text-xs text-slate-400 mt-1 max-w-sm mx-auto">कृपया वर्तनी (spelling) जांचें या दूसरा उपनाम/जाति चुनकर खोजें।</p>
          </div>
        `;
      }
      if (tableBody) {
        tableBody.innerHTML = `<tr><td colspan="10" class="p-8 text-center text-slate-400 font-bold">कोई मतदाता नहीं मिला</td></tr>`;
      }
      return;
    }

    // Render Cards View
    if (cardsCont) {
      cardsCont.innerHTML = currentCasteVoters.map(v => renderCasteVoterCardHtml(v)).join('');
    }

    // Render Table View (for desktop toggle)
    if (tableBody) {
      tableBody.innerHTML = currentCasteVoters.map((v, idx) => {
        const vid = v.id || v.voter_id;
        const slNo = v.sl_no ?? v.serial_no ?? '-';
        const relName = v.rel_name_hi || v.relative_name_hi || v.rel_name_en || '-';
        const phone = v.contact_phone || v.phone || '';
        const displayUnit = v.ward_no || v.part_no || '-';
        const isVoted = Boolean(v.is_voted);

        return `
          <tr class="hover:bg-slate-50 border-b border-slate-100 transition text-xs">
            <td class="py-2.5 px-3 text-center text-slate-400 font-mono">${idx + 1}</td>
            <td class="py-2.5 px-3 text-center font-mono font-bold text-slate-700">${displayUnit}</td>
            <td class="py-2.5 px-3 text-center font-mono font-black text-slate-900 bg-slate-50">${slNo}</td>
            <td class="py-2.5 px-3 font-bold text-slate-900">${escapeHtml(v.name_hi || v.name_en || '-')}</td>
            <td class="py-2.5 px-3 text-slate-700">${escapeHtml(relName)}</td>
            <td class="py-2.5 px-3 font-mono font-bold text-indigo-700">${escapeHtml(v.epic_no || '-')}</td>
            <td class="py-2.5 px-3 text-center text-slate-700">${v.age || '-'} / ${v.gender || '-'}</td>
            <td class="py-2.5 px-3">
              ${(v.caste_tag || v.caste) ? `<span class="bg-purple-50 text-purple-800 text-[10px] font-bold px-2 py-0.5 rounded border border-purple-200">🏷️ ${escapeHtml(v.caste_tag || v.caste)}</span>` : `<span class="bg-slate-100 text-slate-500 text-[10px] font-bold px-2 py-0.5 rounded border border-slate-200">⚪ अनिर्धारित</span>`}
            </td>
            <td class="py-2.5 px-3">${phone ? `<a href="tel:${phone}" class="font-mono font-bold text-blue-700 hover:underline">📞 ${phone}</a>` : '-'}</td>
            <td class="py-2.5 px-3 text-center">
              <button onclick="openSlipModalForVoter(${vid})" class="bg-emerald-600 hover:bg-emerald-500 text-white font-black px-2 py-1 rounded text-[11px] shadow-2xs">पर्ची</button>
            </td>
          </tr>
        `;
      }).join('');
    }
  }

  // PWA Voter Card Generator
  function renderCasteVoterCardHtml(v) {
    const vid = v.id || v.voter_id;
    const isSel = selectedCasteVoters.has(vid);
    const nameHi = v.name_hi || v.name_en || '-';
    const nameEn = v.name_en || '';
    const relName = v.rel_name_hi || v.relative_name_hi || v.rel_name_en || '-';
    const relType = v.rel_type || v.relation_type || 'संबंधी';
    const slNo = v.sl_no ?? v.serial_no ?? '-';
    const wardNo = v.ward_no || v.part_no || '-';
    const houseNo = v.house_no && v.house_no !== '0' && v.house_no !== '-' ? v.house_no : '';
    const phone = (v.contact_phone || v.phone || '').split(',')[0].trim();
    const casteTag = v.caste_tag || v.caste || '';

    // Sentiment Tag
    let sentimentTag = '';
    const cat = String(v.voter_category !== undefined && v.voter_category !== null ? v.voter_category : '0');
    if (cat === '100') {
      sentimentTag = `<span class="text-[10px] bg-emerald-50 text-emerald-800 border border-emerald-300 px-1.5 py-0.2 rounded font-bold">🟢 पक्का</span>`;
    } else if (cat === '50' || cat === '50_50') {
      sentimentTag = `<span class="text-[10px] bg-amber-50 text-amber-800 border border-amber-300 px-1.5 py-0.2 rounded font-bold">🟡 50-50</span>`;
    }

    return `
      <div 
        onclick="toggleCasteVoterSelection(${vid})"
        class="bg-white rounded-2xl border p-3 shadow-2xs transition active:scale-[0.99] cursor-pointer select-none ${isSel ? 'border-indigo-600 bg-indigo-50/40 ring-2 ring-indigo-400/40' : 'border-slate-200 hover:border-slate-300'}"
        data-voter-id="${vid}"
      >
        <!-- Line 1: Ward & Serial + Caste Tag + Age/Gender -->
        <div class="flex items-center justify-between text-xs mb-1">
          <div class="flex items-center gap-1.5">
            <input 
              type="checkbox" 
              ${isSel ? 'checked' : ''} 
              onclick="event.stopPropagation(); toggleCasteVoterSelection(${vid})" 
              class="rounded text-indigo-600 w-4 h-4 cursor-pointer"
            >
            <span class="bg-slate-100 text-slate-800 font-extrabold px-1.5 py-0.5 rounded text-[11px]">
              वार्ड ${wardNo} • क्र. ${slNo}
            </span>
            ${casteTag ? `<span class="bg-purple-50 text-purple-800 border border-purple-200 font-bold text-[10px] px-1.5 py-0.2 rounded">🏷️ ${escapeHtml(casteTag)}</span>` : `<span class="bg-slate-100 text-slate-500 border border-slate-200 font-bold text-[10px] px-1.5 py-0.2 rounded">⚪ अनिर्धारित</span>`}
          </div>
          <span class="text-[11px] text-slate-500 font-bold">
            ${v.age ? `${v.age} वर्ष • ` : ''}${v.gender === 'F' ? 'म.' : (v.gender === 'M' ? 'पु.' : (v.gender || ''))}
          </span>
        </div>

        <!-- Line 2: Voter Name & Relative -->
        <div class="text-sm font-black text-slate-900 leading-snug">
          ${escapeHtml(nameHi)} ${nameEn && nameEn.toLowerCase() !== nameHi.toLowerCase() ? `<span class="text-xs font-normal text-slate-500">(${escapeHtml(nameEn)})</span>` : ''}
        </div>
        <div class="text-xs text-slate-600 mt-0.5">
          ${escapeHtml(relType)}: <strong class="text-slate-800">${escapeHtml(relName)}</strong>
          ${houseNo ? `<span class="text-slate-400 ml-1.5">• मकान #${escapeHtml(houseNo)}</span>` : ''}
        </div>

        <!-- Line 3: Actions + Phone & Sentiment -->
        <div class="mt-2.5 pt-2 border-t border-slate-100 flex items-center justify-between gap-1" onclick="event.stopPropagation()">
          <div class="flex items-center gap-1.5 flex-wrap">
            ${sentimentTag}
            ${phone ? `<a href="tel:${phone}" class="text-[11px] font-mono font-bold text-blue-700 bg-blue-50 border border-blue-200 px-2 py-0.5 rounded-lg flex items-center gap-1">📞 ${phone}</a>` : ''}
          </div>
          <div class="flex items-center gap-1">
            <button 
              type="button" 
              onclick="if(typeof openVoterProfileModal === 'function') openVoterProfileModal(${vid})" 
              class="px-2 py-1 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-xs rounded-lg transition active:scale-95"
              title="एडिट / टैग"
            >
              ✏️
            </button>
            <button 
              type="button" 
              onclick="if(typeof openFamilySmartModal === 'function') openFamilySmartModal(${vid})" 
              class="px-2 py-1 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-xs rounded-lg transition active:scale-95"
              title="परिवार देखें"
            >
              👨‍👩‍👧
            </button>
            <button 
              type="button" 
              onclick="if(typeof openSlipModalForVoter === 'function') openSlipModalForVoter(${vid})" 
              class="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white font-black text-xs rounded-lg shadow-2xs flex items-center gap-1 transition active:scale-95"
              title="WhatsApp पर्ची"
            >
              <span>💬</span><span>पर्ची</span>
            </button>
          </div>
        </div>
      </div>
    `;
  }

  // Multi-Selection Manager
  window.toggleCasteVoterSelection = function (vid) {
    if (selectedCasteVoters.has(vid)) {
      selectedCasteVoters.delete(vid);
    } else {
      const v = currentCasteVoters.find(x => (x.id || x.voter_id) === vid);
      if (v) selectedCasteVoters.set(vid, v);
    }
    updateCasteFloatingIsland();
    renderCurrentResults();
  };

  window.clearCasteSelection = function () {
    selectedCasteVoters.clear();
    updateCasteFloatingIsland();
    renderCurrentResults();
  };

  function updateCasteFloatingIsland() {
    const island = document.getElementById('casteFloatingIsland');
    const txt = document.getElementById('casteSelectedCountText');
    const count = selectedCasteVoters.size;

    if (!island) return;
    if (count > 0) {
      if (txt) txt.textContent = `${count} चुना गया`;
      island.classList.remove('hidden');
      island.classList.add('flex');
    } else {
      island.classList.add('hidden');
      island.classList.remove('flex');
    }
  }

  // Batch Slip Trigger for Selected
  window.openBatchCasteSlipModal = function () {
    const ids = Array.from(selectedCasteVoters.keys());
    if (ids.length === 0) return;
    if (typeof window.openBulkSlipModal === 'function') {
      window.openBulkSlipModal(ids);
    } else if (typeof window.openSlipModalForVoter === 'function') {
      window.openSlipModalForVoter(ids[0]);
    }
  };

  // CSV Export
  window.exportCurrentCasteList = function () {
    if (currentCasteVoters.length === 0) {
      alert('डाउनलोड करने के लिए पहले कोई समुदाय खोजें');
      return;
    }
    const headers = ['Ward', 'Serial', 'Name_HI', 'Name_EN', 'Relative', 'EPIC', 'Age', 'Gender', 'Phone', 'Caste'];
    const rows = currentCasteVoters.map(v => [
      v.ward_no || v.part_no || '',
      v.sl_no ?? v.serial_no ?? '',
      `"${(v.name_hi || '').replace(/"/g, '""')}"`,
      `"${(v.name_en || '').replace(/"/g, '""')}"`,
      `"${(v.rel_name_hi || v.relative_name_hi || '').replace(/"/g, '""')}"`,
      v.epic_no || '',
      v.age || '',
      v.gender || '',
      v.contact_phone || v.phone || '',
      `"${(v.caste_tag || v.caste || '').replace(/"/g, '""')}"`
    ]);

    const csvContent = 'data:text/csv;charset=utf-8,\uFEFF' + [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `community_voters_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ===========================================================================
  // COMMUNITY DEMOGRAPHIC ANALYSIS & WARD-WISE BIFURCATION ENGINE
  // ===========================================================================
  let activeCommunityTab = 'voters';
  let cachedAnalyticsData = null;

  window.switchCommunityTab = function (tab) {
    activeCommunityTab = tab || 'voters';
    const votersView = document.getElementById('votersTabView');
    const analyticsView = document.getElementById('analyticsTabView');
    const tabBtnVoters = document.getElementById('tabBtnVoters');
    const tabBtnAnalytics = document.getElementById('tabBtnAnalytics');

    if (activeCommunityTab === 'analytics') {
      if (votersView) votersView.classList.add('hidden');
      if (analyticsView) analyticsView.classList.remove('hidden');

      if (tabBtnAnalytics) {
        tabBtnAnalytics.className = 'flex-1 py-1.5 rounded-xl bg-white text-indigo-700 shadow-xs text-center transition cursor-pointer font-black';
      }
      if (tabBtnVoters) {
        tabBtnVoters.className = 'flex-1 py-1.5 rounded-xl text-slate-600 hover:text-slate-900 text-center transition cursor-pointer font-bold';
      }

      loadCasteAnalytics();
      if (window.triggerHaptic) window.triggerHaptic('selection');
    } else {
      if (votersView) votersView.classList.remove('hidden');
      if (analyticsView) analyticsView.classList.add('hidden');

      if (tabBtnVoters) {
        tabBtnVoters.className = 'flex-1 py-1.5 rounded-xl bg-white text-indigo-700 shadow-xs text-center transition cursor-pointer font-black';
      }
      if (tabBtnAnalytics) {
        tabBtnAnalytics.className = 'flex-1 py-1.5 rounded-xl text-slate-600 hover:text-slate-900 text-center transition cursor-pointer font-bold';
      }

      if (window.triggerHaptic) window.triggerHaptic('selection');
    }
  };

  window.openAnalyticsWardSheet = function () {
    const trigger = document.querySelector('[data-modal-target="castePartFilterModal"]') || 
                    document.getElementById('casteSelectedWardLabel') ||
                    document.querySelector('#votersTabView button');
    if (trigger) {
      trigger.click();
    } else if (typeof window.openWardSheetModal === 'function') {
      window.openWardSheetModal('castePartFilterModal');
    }
  };

  window.loadCasteAnalytics = async function () {
    const filterEl = document.getElementById('castePartFilter');
    const rawFilterVal = (filterEl?.value || '').trim();
    let wardNo = '';
    let partNo = '';

    if (rawFilterVal) {
      if (/^ward:/i.test(rawFilterVal)) {
        wardNo = rawFilterVal.replace(/^ward:/i, '').trim();
      } else if (/^(part|booth):/i.test(rawFilterVal)) {
        partNo = rawFilterVal.replace(/^(part|booth):/i, '').trim();
      } else {
        const key = filterEl?.dataset?.filterKey || 'ward_no';
        if (key === 'ward_no') wardNo = rawFilterVal;
        else partNo = rawFilterVal;
      }
    }

    const scopeLabel = document.getElementById('analyticsScopeLabel');
    const wardLabelEl = document.getElementById('casteSelectedWardLabel');
    if (scopeLabel) {
      if (wardNo) {
        scopeLabel.textContent = `दायरा: वार्ड ${wardNo} का विश्लेषण`;
      } else if (partNo) {
        scopeLabel.textContent = `दायरा: भाग ${partNo} का विश्लेषण`;
      } else if (wardLabelEl && wardLabelEl.textContent.trim()) {
        scopeLabel.textContent = `दायरा: ${wardLabelEl.textContent.trim()}`;
      } else {
        scopeLabel.textContent = 'दायरा: पूरे अभियान का विश्लेषण';
      }
    }

    const container = document.getElementById('casteAnalyticsCardsContainer');
    if (container) {
      container.innerHTML = `
        <div class="text-center py-10 bg-white rounded-2xl border border-slate-200 text-slate-400 text-xs">
          <div class="inline-block animate-spin text-xl mb-2">⏳</div>
          <p class="font-bold">जाति व समुदाय डेटा लोड हो रहा है...</p>
        </div>
      `;
    }

    try {
      const tenant = getActiveTenant();
      let url = `/api/caste/analytics?`;
      if (tenant) url += `tenant=${encodeURIComponent(tenant)}&`;
      if (wardNo) url += `ward_no=${encodeURIComponent(wardNo)}&`;
      if (partNo) url += `part_no=${encodeURIComponent(partNo)}&`;

      const res = await fetch(url);
      const data = await res.json();

      if (!data.success) {
        if (container) {
          container.innerHTML = `<div class="p-6 text-center text-rose-500 font-bold text-xs bg-rose-50 rounded-2xl border border-rose-100">डेटा लोड करने में त्रुटि: ${escapeHtml(data.error || 'अज्ञात त्रुटि')}</div>`;
        }
        return;
      }

      cachedAnalyticsData = data;

      // Update Quick Metrics
      const totalEl = document.getElementById('analyticsStatTotal');
      const taggedEl = document.getElementById('analyticsStatTagged');
      const phonesEl = document.getElementById('analyticsStatPhones');

      if (totalEl) totalEl.textContent = Number(data.total_voters || 0).toLocaleString('en-IN');
      if (taggedEl) taggedEl.textContent = `${Number(data.tagged_voters || 0).toLocaleString('en-IN')} (${data.tagged_pct || 0}%)`;
      if (phonesEl) phonesEl.textContent = `${Number(data.phone_count || 0).toLocaleString('en-IN')} (${data.phone_pct || 0}%)`;

      renderAnalyticsCards(data.communities || [], wardNo || partNo);

    } catch (err) {
      console.error('Failed to load caste analytics:', err);
      if (container) {
        container.innerHTML = `<div class="p-6 text-center text-rose-500 font-bold text-xs bg-rose-50 rounded-2xl border border-rose-100">डेटा लोड करने में विफल। कृपया पुनः प्रयास करें।</div>`;
      }
    }
  };

  function renderAnalyticsCards(communities, activeFilter = '') {
    const container = document.getElementById('casteAnalyticsCardsContainer');
    if (!container) return;

    if (!communities || communities.length === 0) {
      container.innerHTML = `
        <div class="text-center py-10 bg-white rounded-2xl border border-slate-200 text-slate-500 text-xs">
          <p class="font-bold">कोई जाति या समुदाय रिकॉर्ड नहीं मिला</p>
        </div>
      `;
      return;
    }

    const html = communities.map((c) => {
      const isUntagged = c.is_untagged || c.caste === 'अन्य / गैर-टैग';
      const badgeIcon = isUntagged ? '⚪' : '🏷️';
      const barColor = isUntagged ? 'bg-slate-300' : 'bg-indigo-600';
      const phoneBadgeColor = (c.phone_pct >= 70) ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
                              (c.phone_pct >= 40) ? 'bg-amber-50 text-amber-700 border-amber-200' :
                              'bg-slate-50 text-slate-600 border-slate-200';

      // Ward distribution chips
      let wardChipsHtml = '';
      if (c.wards && c.wards.length > 0) {
        const topWards = c.wards.slice(0, 6);
        wardChipsHtml = `
          <div class="pt-2 border-t border-slate-100 flex items-center gap-1.5 flex-wrap text-[11px]">
            <span class="text-slate-400 font-semibold shrink-0">वार्डवार:</span>
            ${topWards.map(w => `
              <button 
                type="button" 
                onclick="jumpToCasteVoters('${escapeHtml(c.caste)}', '${w.ward}')" 
                class="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-lg bg-slate-100 hover:bg-indigo-50 hover:text-indigo-700 text-slate-700 font-bold transition cursor-pointer"
                title="वार्ड ${w.ward} में देखें"
              >
                <span>वार्ड ${w.ward}:</span>
                <span class="text-indigo-600 font-black">${w.count}</span>
              </button>
            `).join('')}
            ${c.wards.length > 6 ? `<span class="text-slate-400 text-[10px]">+${c.wards.length - 6} अन्य</span>` : ''}
          </div>
        `;
      }

      return `
        <div class="bg-white rounded-2xl border border-slate-200 p-3.5 shadow-2xs space-y-2.5 hover:border-slate-300 transition">
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <div class="flex items-center gap-1.5 flex-wrap">
                <span class="text-xs">${badgeIcon}</span>
                <h4 class="font-black text-sm text-slate-900 truncate">${escapeHtml(c.caste)}</h4>
                <span class="px-2 py-0.5 rounded-full text-[10px] font-black ${phoneBadgeColor} border">
                  📞 ${c.phone_pct}% फोन (${c.phone_count})
                </span>
              </div>
              <div class="text-xs text-slate-500 font-bold mt-0.5">
                <span class="text-slate-900 font-black">${Number(c.count).toLocaleString('en-IN')}</span> मतदाता 
                <span class="text-slate-300 mx-1">•</span> 
                <span class="text-indigo-600 font-black">${c.pct}%</span> कुल वोट शेयर
              </div>
            </div>

            <!-- 1-Tap Jump to Voters -->
            <button 
              type="button"
              onclick="jumpToCasteVoters('${escapeHtml(c.caste)}', '${activeFilter || ''}')"
              class="shrink-0 bg-indigo-50 hover:bg-indigo-100 active:scale-95 text-indigo-700 font-black text-xs px-3 py-1.5 rounded-xl border border-indigo-200 transition flex items-center gap-1 cursor-pointer"
            >
              <span>मतदाता</span>
              <span>→</span>
            </button>
          </div>

          <!-- Progress Bar -->
          <div class="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
            <div class="${barColor} h-2 rounded-full transition-all duration-500" style="width: ${Math.min(c.pct * 2, 100)}%;"></div>
          </div>

          ${wardChipsHtml}
        </div>
      `;
    }).join('');

    container.innerHTML = html;
  }

  window.filterAnalyticsCards = function (q) {
    if (!cachedAnalyticsData || !cachedAnalyticsData.communities) return;
    const query = (q || '').trim().toLowerCase();
    if (!query) {
      renderAnalyticsCards(cachedAnalyticsData.communities, cachedAnalyticsData.filter_ward);
      return;
    }
    const filtered = cachedAnalyticsData.communities.filter(c => 
      (c.caste || '').toLowerCase().includes(query)
    );
    renderAnalyticsCards(filtered, cachedAnalyticsData.filter_ward);
  };

  window.jumpToCasteVoters = function (casteName, wardNo) {
    // 1. Set activeSelectedCaste to the chosen caste
    activeSelectedCaste = (casteName || '').trim();
    const isUntagged = (
      activeSelectedCaste === 'अन्य / गैर-टैग' ||
      activeSelectedCaste === 'अन्य/गैर-टैग' ||
      activeSelectedCaste === 'untagged' ||
      activeSelectedCaste.toLowerCase() === 'other'
    );
    if (isUntagged) {
      activeSelectedCaste = 'अन्य / गैर-टैग';
      activeSelectedCasteLabel = '⚪ अनिर्धारित / गैर-टैग';
    } else {
      activeSelectedCasteLabel = activeSelectedCaste ? `🏷️ ${activeSelectedCaste}` : '🏷️ सभी जातियां';
    }
    const selectedLabelEl = document.getElementById('selectedCasteLabel');
    if (selectedLabelEl) {
      selectedLabelEl.textContent = activeSelectedCasteLabel;
    }

    // 2. Clear text search input so user sees all voters of this caste
    const searchInp = document.getElementById('casteSearchInput');
    if (searchInp) {
      searchInp.value = '';
    }

    // 3. Set ward filter if specified
    if (wardNo && String(wardNo).trim() !== '' && String(wardNo) !== 'ALL') {
      const filterEl = document.getElementById('castePartFilter');
      if (filterEl) {
        for (let opt of filterEl.options) {
          if (opt.value === String(wardNo) || opt.value === `ward:${wardNo}` || opt.value === `part:${wardNo}`) {
            filterEl.value = opt.value;
            break;
          }
        }
      }
      const labelEl = document.getElementById('casteSelectedWardLabel');
      if (labelEl) labelEl.textContent = `वार्ड ${wardNo}`;
    }

    // 4. Switch to voters tab and trigger filtered search
    switchCommunityTab('voters');
    window.triggerCasteSearch();
  };

  // Initialization
  window.casteInit = async function () {
    const filterEl = document.getElementById('castePartFilter');
    if (filterEl) {
      filterEl.addEventListener('change', () => {
        if (activeCommunityTab === 'analytics') {
          loadCasteAnalytics();
        }
      });
    }

    await loadCastePresetsAndTags();
    // Trigger initial search so voters load immediately
    window.triggerCasteSearch();
  };

})();

