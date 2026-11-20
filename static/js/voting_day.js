/**
 * voting_day.js - High-Speed Booth-Wise Serial Grid & Polling Day Engine
 * Part of SmartBooth PWA
 *
 * Features:
 * - 0ms physical tap-to-turn-green marking with tactile haptics
 * - Strict Single Ward / Single Booth enforcement (zero multi-ward conflation, zero W1/W2 badges)
 * - Live Booth Marking Sheet Generator (Ward 82 Style 20x30 matrix, 600 per page, 1-2 pages max)
 * - Filterable Unvoted Voter List Print Engine (Caste, Sentiment, Residence, Gender filters)
 * - Fast Century Jumpers & Rapid Numeric Keypad entry
 */

(function () {
  'use strict';

  if (window.__VD_LOADED__) {
    return;
  }
  window.__VD_LOADED__ = true;

  let currentPart = '';
  let currentParts = '';
  let currentWard = null; // numeric ward_no or null (never 'all')
  let statusFilter = 'all'; // 'all' | 'pending' | 'voted'
  let votersList = []; // All voters in current booth

  function formatBoothHeaderTitle(boothName, ward) {
    const activeTab = document.getElementById(`vdTab${currentPart}`);
    const gpName = activeTab ? (activeTab.dataset.gp || '').trim() : '';
    let label = (boothName || `भाग ${currentPart}`).trim();
    if (ward !== null && ward !== undefined) {
      if (/\(वार्ड.*?\)/.test(label)) {
        label = label.replace(/\(वार्ड.*?\)/, `(वार्ड ${ward})`);
      } else {
        label = `${label} (वार्ड ${ward})`;
      }
    }
    if (gpName && !label.includes(gpName)) {
      label = `${gpName} · ${label}`;
    }
    return `${label} · क्रम संख्या ग्रिड`;
  }

  window.vdInit = function (defaultPart, defaultParts) {
    const urlParams = new URLSearchParams(window.location.search);
    const urlPart = urlParams.get('part') || urlParams.get('part_no') || urlParams.get('booth') || urlParams.get('booth_no');
    if (urlPart) {
      defaultPart = urlPart;
    }
    const urlWard = urlParams.get('ward') || urlParams.get('ward_no');
    if (urlWard) {
      currentWard = parseInt(urlWard, 10);
      const allTabs = document.querySelectorAll('.vd-booth-tab');
      for (const tab of allTabs) {
        const wards = (tab.dataset.wards || '').split(',').map(s => s.trim());
        if (wards.includes(String(urlWard)) && tab.dataset.part) {
          defaultPart = tab.dataset.part;
          defaultParts = tab.dataset.parts || defaultPart;
          break;
        }
      }
    }
    currentPart = String(defaultPart || '1');
    const tabEl = document.getElementById(`vdTab${currentPart}`);
    currentParts = defaultParts || (tabEl ? tabEl.dataset.parts : '') || currentPart;
    window.vdSelectBooth(currentPart, currentParts);
  };

  // Switch Booth Tab
  window.vdSelectBooth = function (part, parts) {
    if (!part) return;
    currentPart = String(part);
    const activeTab = document.getElementById(`vdTab${currentPart}`);
    currentParts = (parts || (activeTab ? activeTab.dataset.parts : '') || currentPart).trim();

    // Update active tab visual
    document.querySelectorAll('.vd-booth-tab').forEach(tab => {
      if (tab.dataset.part === currentPart) {
        tab.className = 'vd-booth-tab px-3.5 py-2 rounded-xl text-xs font-black transition whitespace-nowrap cursor-pointer flex items-center gap-1.5 active:scale-95 bg-emerald-600 text-white shadow-xs';
      } else {
        tab.className = 'vd-booth-tab px-3.5 py-2 rounded-xl text-xs font-bold transition whitespace-nowrap cursor-pointer flex items-center gap-1.5 active:scale-95 bg-slate-100 hover:bg-slate-200 text-slate-700';
      }
    });

    function updateHeaderTitle() {
      const headerTitle = document.getElementById('vdBoothHeaderTitle');
      if (headerTitle) {
        const boothName = activeTab ? activeTab.textContent.trim() : `भाग ${currentPart}`;
        headerTitle.textContent = formatBoothHeaderTitle(boothName, currentWard);
      }
    }
    updateHeaderTitle();

    if (window.triggerHaptic) window.triggerHaptic('selection');
    window.vdFetchBoothVoters();
  };

  // Switch Ward Tab (Strictly one ward, never 'all')
  window.vdSelectWard = function (ward) {
    currentWard = parseInt(ward, 10);
    const headerTitle = document.getElementById('vdBoothHeaderTitle');
    if (headerTitle) {
      const activeTab = document.getElementById(`vdTab${currentPart}`);
      const boothName = activeTab ? activeTab.textContent.trim() : `भाग ${currentPart}`;
      headerTitle.textContent = formatBoothHeaderTitle(boothName, currentWard);
    }
    renderWardTabs();
    if (window.triggerHaptic) window.triggerHaptic('selection');
    vdRenderStats();
    vdRenderCenturyJumpers();
    vdRenderGrid();
  };

  // Fetch voters for the selected booth
  window.vdFetchBoothVoters = async function () {
    const grid = document.getElementById('vdSerialGrid');
    if (grid) {
      grid.innerHTML = `
        <div class="col-span-full py-16 text-center text-slate-400 font-bold">
          <div class="text-2xl mb-2 animate-spin">⏳</div>
          <div>बूथ ${currentPart} के मतदाता लोड हो रहे हैं...</div>
        </div>
      `;
    }

    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      let fetchUrl = '';
      if (currentParts && currentParts.includes(',')) {
        fetchUrl = `/api/search?parts=${encodeURIComponent(currentParts)}&limit=5000${curTenant ? '&tenant=' + encodeURIComponent(curTenant) : ''}`;
      } else if (currentParts && currentParts !== currentPart) {
        fetchUrl = `/api/search?part=${encodeURIComponent(currentParts)}&limit=5000${curTenant ? '&tenant=' + encodeURIComponent(curTenant) : ''}`;
      } else {
        fetchUrl = `/api/search?part=${encodeURIComponent(currentPart)}&limit=5000${curTenant ? '&tenant=' + encodeURIComponent(curTenant) : ''}`;
      }
      const res = await fetch(fetchUrl);
      const data = await res.json();
      const rawList = Array.isArray(data) ? data : (data.results || []);

      votersList = rawList.map(v => {
        const sl = parseInt(v.sl_no ?? v.serial_no, 10);
        const wNo = v.ward_no != null && !isNaN(parseInt(v.ward_no, 10)) ? parseInt(v.ward_no, 10) : null;
        const isSurveyDup = (v.survey_status === 'DUPLICATE') || Boolean(v.is_duplicate);

        return {
          id: v.id || v.voter_id,
          serial_no: isNaN(sl) ? 0 : sl,
          name: v.name_hi || v.name_en || v.name || 'मतदाता',
          rel_name: v.rel_name_hi || v.rel_name_en || v.relative_name || '',
          house_no: v.house_no || '-',
          ward_no: wNo,
          part_no: v.part_no || currentPart,
          age: v.age || '',
          gender: v.gender || '',
          contact_phone: v.contact_phone || v.phone || '',
          caste_tag: v.caste_tag || '',
          voter_category: v.voter_category,
          is_outside: Boolean(v.is_outside),
          is_duplicate: isSurveyDup,
          duplicate_pair_sl: v.duplicate_pair_sl || null,
          duplicate_pair_ward: v.duplicate_pair_ward || null,
          duplicate_pair_name: v.duplicate_pair_name || '',
          duplicate_info: v.duplicate_info || '',
          duplicate_group_id: v.duplicate_group_id || null,
          survey_status: v.survey_status || null,
          status: v.status || 'ACTIVE',
          del_reason: v.del_reason || '',
          is_voted: Boolean(v.is_voted)
        };
      }).filter(v => v.serial_no > 0);

      // Sort by ward number, then serial number ascending
      votersList.sort((a, b) => ((a.ward_no || 0) - (b.ward_no || 0)) || (a.serial_no - b.serial_no));

      // Detect distinct wards in this booth
      const distinctWards = [...new Set(votersList.map(v => v.ward_no).filter(w => w !== null && w > 0))].sort((a, b) => a - b);
      const wardContainer = document.getElementById('vdWardSelectorContainer');

      if (distinctWards.length > 1) {
        if (wardContainer) wardContainer.classList.remove('hidden');
        const urlParams = new URLSearchParams(window.location.search);
        const urlWard = urlParams.get('ward') ? parseInt(urlParams.get('ward'), 10) : null;
        if (urlWard && distinctWards.includes(urlWard)) {
          currentWard = urlWard;
        } else if (currentWard === null || !distinctWards.includes(currentWard)) {
          currentWard = distinctWards[0];
        }
      } else if (distinctWards.length === 1) {
        if (wardContainer) wardContainer.classList.add('hidden');
        currentWard = distinctWards[0];
      } else {
        if (wardContainer) wardContainer.classList.add('hidden');
        currentWard = null;
      }

      renderWardTabs();
      const headerTitle = document.getElementById('vdBoothHeaderTitle');
      if (headerTitle) {
        const activeTab = document.getElementById(`vdTab${currentPart}`);
        const boothName = activeTab ? activeTab.textContent.trim() : `भाग ${currentPart}`;
        headerTitle.textContent = formatBoothHeaderTitle(boothName, currentWard);
      }
      vdRenderStats();
      vdRenderCenturyJumpers();
      vdRenderGrid();
    } catch (err) {
      console.error('Error fetching booth voters:', err);
      if (grid) {
        grid.innerHTML = `
          <div class="col-span-full py-12 text-center text-rose-500 font-bold">
            ⚠️ डेटा लोड करने में त्रुटि आई। कृपया दोबारा प्रयास करें।
          </div>
        `;
      }
    }
  };

  // Render Dynamic Ward Sub-Tabs (Strictly one ward, zero "All Wards" toggle)
  function renderWardTabs() {
    const listEl = document.getElementById('vdWardTabsList');
    if (!listEl) return;

    const distinctWards = [...new Set(votersList.map(v => v.ward_no).filter(w => w !== null && w > 0))].sort((a, b) => a - b);
    if (distinctWards.length <= 1) {
      listEl.innerHTML = '';
      return;
    }

    let html = '';
    distinctWards.forEach(w => {
      const isSelected = currentWard === w;
      html += `
        <button 
          type="button" 
          onclick="vdSelectWard(${w})" 
          class="px-3 py-1.5 rounded-lg text-xs font-bold transition whitespace-nowrap cursor-pointer active:scale-95 ${isSelected ? 'bg-emerald-600 text-white font-black shadow-xs' : 'bg-slate-100 hover:bg-slate-200 text-slate-700'}"
        >
          वार्ड ${w}
        </button>
      `;
    });

    listEl.innerHTML = html;
  }

  // Get voters matching active booth & single selected ward
  function getWardScopedVoters() {
    if (currentWard !== null && currentWard !== undefined) {
      const scoped = votersList.filter(v => v.ward_no === currentWard);
      if (scoped.length > 0) return scoped;
    }
    return votersList;
  }

  // Get voters matching active booth, ward, and status filter
  function getVisibleVoters() {
    const wardVoters = getWardScopedVoters();
    return wardVoters.filter(v => {
      if (statusFilter === 'pending') return !v.is_voted;
      if (statusFilter === 'voted') return v.is_voted;
      return true;
    });
  }

  // Render Top Stats & Progress Bar
  function vdRenderStats() {
    const wardVoters = getWardScopedVoters();
    const total = wardVoters.length;
    const voted = wardVoters.filter(v => v.is_voted).length;
    const pct = total > 0 ? ((voted / total) * 100).toFixed(1) : '0.0';

    const totalEl = document.getElementById('vdBoothTotalCount');
    const votedEl = document.getElementById('vdBoothVotedCount');
    const pctEl = document.getElementById('vdBoothTurnoutPercent');
    const progBar = document.getElementById('vdProgressBar');

    if (totalEl) totalEl.textContent = total;
    if (votedEl) votedEl.textContent = voted;
    if (pctEl) pctEl.textContent = `${pct}%`;
    if (progBar) progBar.style.width = `${pct}%`;
  }

  // Render Dynamic Century Jumpers (1-100, 101-200, etc.)
  function vdRenderCenturyJumpers() {
    const bar = document.getElementById('vdCenturyBar');
    if (!bar) return;

    const visibleVoters = getWardScopedVoters();
    if (visibleVoters.length === 0) {
      bar.innerHTML = '';
      return;
    }

    const maxSerial = Math.max(...visibleVoters.map(v => v.serial_no));
    let html = '';
    for (let start = 1; start <= maxSerial; start += 100) {
      const end = Math.min(start + 99, maxSerial);
      html += `
        <button 
          type="button" 
          onclick="vdScrollToSerial(${start})" 
          class="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-emerald-50 hover:text-emerald-700 active:bg-emerald-100 border border-slate-200 font-mono font-bold text-slate-700 shrink-0 transition cursor-pointer"
        >
          ${start}-${end}
        </button>
      `;
    }
    bar.innerHTML = html;
  }

  // Scroll Grid to specific serial number
  window.vdScrollToSerial = function (serial) {
    const visible = getVisibleVoters();
    const match = visible.find(v => v.serial_no >= serial);
    if (!match) return;

    const el = document.getElementById(`vdBtn_${match.id}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('ring-4', 'ring-blue-400');
      setTimeout(() => el.classList.remove('ring-4', 'ring-blue-400'), 1200);
      if (window.triggerHaptic) window.triggerHaptic('light');
    }
  };

  // Filter Toggle: all vs pending vs voted
  window.vdSetStatusFilter = function (filter) {
    statusFilter = filter;
    ['all', 'pending', 'voted'].forEach(f => {
      const btn = document.getElementById(`vdFilter${f.charAt(0).toUpperCase() + f.slice(1)}`);
      if (btn) {
        if (f === filter) {
          btn.className = 'px-3 py-1.5 rounded-lg text-center bg-white text-slate-900 font-black shadow-xs transition';
        } else {
          btn.className = 'px-3 py-1.5 rounded-lg text-center text-slate-600 hover:text-slate-900 transition';
        }
      }
    });

    if (window.triggerHaptic) window.triggerHaptic('selection');
    vdRenderGrid();
  };

  // Render the Clean Serial Number Grid (Zero W1, W2 Clutter)
  function vdRenderGrid() {
    const grid = document.getElementById('vdSerialGrid');
    const statusText = document.getElementById('vdGridStatusText');
    if (!grid) return;

    const visibleVoters = getVisibleVoters();

    if (statusText) {
      const filterLabel = statusFilter === 'pending' ? 'बाकी' : statusFilter === 'voted' ? 'वोटेड' : 'कुल';
      const wardLabel = (currentWard !== null && currentWard !== undefined) ? ` · वार्ड ${currentWard}` : '';
      const activeTab = document.getElementById(`vdTab${currentPart}`);
      const gpName = activeTab ? (activeTab.dataset.gp || '').trim() : '';
      const gpPrefix = gpName ? `${gpName} · ` : '';
      statusText.textContent = `${gpPrefix}बूथ ${currentPart}${wardLabel} · ${visibleVoters.length} मतदाता (${filterLabel})`;
    }

    if (visibleVoters.length === 0) {
      grid.innerHTML = `
        <div class="col-span-full py-16 text-center text-slate-400 font-bold">
          ${statusFilter === 'pending' ? '🎉 इस वार्ड/भाग के सभी वोट डाले जा चुके हैं!' : 'कोई मतदाता नहीं मिला'}
        </div>
      `;
      return;
    }

    grid.innerHTML = visibleVoters.map(v => buildTileHtml(v)).join('');
  }

  // Generate HTML for a single serial tile (Clean, zero duplicate W1/W2 badges)
  function buildTileHtml(v) {
    const isVoted = v.is_voted;
    
    // Status indicators
    let indicatorHtml = '';
    let dupTitleExtra = '';
    if (v.is_duplicate) {
      const locStr = v.duplicate_pair_ward ? `वार्ड ${v.duplicate_pair_ward} ` : '';
      const pairTxt = v.duplicate_pair_sl ? ` (मूल: ${locStr}क्र. #${v.duplicate_pair_sl})` : '';
      dupTitleExtra = ` [DUP${pairTxt}]`;
      indicatorHtml = `<span class="text-[8px] font-black text-rose-600 bg-rose-50 border border-rose-200 px-1 rounded leading-none" title="डुप्लीकेट${pairTxt}">DUP</span>`;
    } else if (v.duplicate_pair_sl) {
      const locStr = v.duplicate_pair_ward ? `वार्ड ${v.duplicate_pair_ward} ` : '';
      const pairTxt = ` (प्रतिलिपि: ${locStr}क्र. #${v.duplicate_pair_sl})`;
      dupTitleExtra = ` [PAIR${pairTxt}]`;
      indicatorHtml = `<span class="text-[8px] font-black text-blue-600 bg-blue-50 border border-blue-200 px-1 rounded leading-none" title="दोहराव मिला${pairTxt}">PAIR</span>`;
    } else if (v.is_outside) {
      indicatorHtml = '<span class="text-[9px] font-black text-amber-600 leading-none" title="प्रवासी मतदाता">✈</span>';
    }

    if (isVoted) {
      return `
        <button 
          type="button" 
          id="vdBtn_${v.id}"
          data-voter-id="${v.id}"
          data-serial="${v.serial_no}"
          onclick="vdGridTileClick(${v.id}, ${v.serial_no})"
          class="aspect-square flex flex-col items-center justify-center rounded-xl bg-emerald-600 active:scale-95 border-2 border-emerald-500 text-white font-black transition shadow-xs cursor-pointer select-none ring-2 ring-emerald-400/40 p-1"
          title="#${v.serial_no}: ${v.name}${dupTitleExtra} (Voted - Tap to Undo)"
        >
          <span class="text-[9px] sm:text-[10px] leading-none mb-0.5 font-bold">✓</span>
          <span class="text-xs sm:text-sm font-black leading-tight">${v.serial_no}</span>
          ${indicatorHtml}
        </button>
      `;
    } else {
      return `
        <button 
          type="button" 
          id="vdBtn_${v.id}"
          data-voter-id="${v.id}"
          data-serial="${v.serial_no}"
          onclick="vdGridTileClick(${v.id}, ${v.serial_no})"
          class="aspect-square flex flex-col items-center justify-center rounded-xl bg-white hover:bg-slate-50 active:scale-95 border-2 border-slate-200 text-slate-800 font-black transition shadow-2xs cursor-pointer select-none p-1"
          title="#${v.serial_no}: ${v.name}${dupTitleExtra} (Tap to mark Voted)"
        >
          <span class="text-xs sm:text-sm font-black leading-tight">${v.serial_no}</span>
          ${indicatorHtml}
        </button>
      `;
    }
  }

  // Instant Tap-to-Turn-Green Interaction (0ms Optimistic UI)
  window.vdGridTileClick = async function (voterId, serialNo) {
    const voter = votersList.find(v => v.id === voterId);
    if (!voter) return;

    const newStatus = !voter.is_voted;
    voter.is_voted = newStatus;

    // 1. Optimistic instant visual swap (0ms!)
    const btn = document.getElementById(`vdBtn_${voterId}`);
    if (btn) {
      btn.outerHTML = buildTileHtml(voter);
    }

    // 2. Tactile physical haptic buzz
    if (window.triggerHaptic) {
      window.triggerHaptic(newStatus ? 'success' : 'medium');
    }

    // 3. Update top counter & progress bar instantly
    vdRenderStats();

    // 4. Background silent sync with server
    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const res = await fetch('/api/turnout/mark_single' + (curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : ''), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voter_id: voterId,
          is_voted: newStatus ? 1 : 0,
          tenant: curTenant
        })
      });
      const data = await res.json();
      if (!data.success) {
        // Revert on server error
        voter.is_voted = !newStatus;
        const revertBtn = document.getElementById(`vdBtn_${voterId}`);
        if (revertBtn) {
          revertBtn.outerHTML = buildTileHtml(voter);
        }
        vdRenderStats();
        alert(data.error || 'मतदान स्थिति अपडेट करने में त्रुटि आई');
      } else {
        // Keep omnisearch in sync
        if (typeof window.patchVoterInCache === 'function') {
          window.patchVoterInCache(voterId, { is_voted: newStatus ? 1 : 0 });
        }
      }
    } catch (err) {
      console.error('Silent sync error:', err);
      voter.is_voted = !newStatus;
      const revertBtn = document.getElementById(`vdBtn_${voterId}`);
      if (revertBtn) {
        revertBtn.outerHTML = buildTileHtml(voter);
      }
      vdRenderStats();
    }
  };

  // Direct Serial Number Input & Mark (e.g. typing 142 + Enter)
  window.vdMarkByDirectInput = async function () {
    const input = document.getElementById('vdQuickSerialInput');
    const val = (input?.value || '').trim();
    if (!val) {
      input?.focus();
      return;
    }

    const serialNum = parseInt(val, 10);
    if (isNaN(serialNum)) {
      alert('कृपया वैध क्रम संख्या (संख्या) दर्ज करें।');
      return;
    }

    const pool = getVisibleVoters();
    const targetVoter = pool.find(v => v.serial_no === serialNum);

    if (!targetVoter) {
      const wardTxt = (currentWard !== null && currentWard !== undefined) ? `वार्ड ${currentWard}` : `भाग ${currentPart}`;
      alert(`क्रम संख्या #${serialNum} ${wardTxt} में नहीं मिली।`);
      return;
    }

    if (!targetVoter.is_voted) {
      await window.vdGridTileClick(targetVoter.id, targetVoter.serial_no);
    }

    window.vdScrollToSerial(serialNum);
    if (input) input.value = '';
  };


  // =========================================================================
  // SEAMLESS HIDDEN IFRAME PRINT ENGINE (Zero blank tabs, zero about:blank)
  // =========================================================================
  function vdPrintHtml(htmlContent) {
    let iframe = document.getElementById('vdPrintFrame');
    if (iframe) {
      try { iframe.remove(); } catch (e) {}
    }

    iframe = document.createElement('iframe');
    iframe.id = 'vdPrintFrame';
    iframe.setAttribute('style', 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden;');
    document.body.appendChild(iframe);

    const doc = iframe.contentWindow.document;
    doc.open();
    doc.write(htmlContent);
    doc.close();

    setTimeout(() => {
      try {
        iframe.contentWindow.focus();
        iframe.contentWindow.print();
      } catch (err) {
        console.error('Iframe print error:', err);
      }
    }, 250);
  }

  // =========================================================================
  // BOOTH MARKING SHEET ENGINE (Ward 82 Style 20x30 Matrix, 600 per Page)
  // =========================================================================

  window.vdOpenMarkingSheetModal = function () {
    const modal = document.getElementById('vdMarkingSheetModal');
    if (!modal) return;

    const scoped = getWardScopedVoters();
    const total = scoped.length;
    const voted = scoped.filter(v => v.is_voted).length;
    const pending = total - voted;

    const subTitle = document.getElementById('vdMarkingModalSubtitle');
    const activeTab = document.getElementById(`vdTab${currentPart}`);
    const gpName = activeTab ? (activeTab.dataset.gp || '').trim() : '';
    const gpPrefix = gpName ? `${gpName} · ` : '';
    const wardTxt = (currentWard !== null && currentWard !== undefined) ? `${gpPrefix}वार्ड ${currentWard} (बूथ ${currentPart})` : `${gpPrefix}बूथ ${currentPart}`;
    if (subTitle) subTitle.textContent = `${wardTxt} · कुल ${total} मतदाता`;

    const totEl = document.getElementById('vdModalTotalCount');
    const votEl = document.getElementById('vdModalVotedCount');
    const penEl = document.getElementById('vdModalPendingCount');

    if (totEl) totEl.textContent = total;
    if (votEl) votEl.textContent = voted;
    if (penEl) penEl.textContent = pending;

    modal.classList.remove('hidden');
  };

  window.vdCloseMarkingSheetModal = function () {
    const modal = document.getElementById('vdMarkingSheetModal');
    if (modal) modal.classList.add('hidden');
  };

  // Generate and Print the 20x30 Marking Sheet (1-2 pages max)
  window.vdPrintMarkingSheet = function () {
    const scoped = getWardScopedVoters();
    if (scoped.length === 0) {
      alert('कोई मतदाता डेटा उपलब्ध नहीं है।');
      return;
    }

    const isLive = document.querySelector('input[name="vdSheetModeRadio"]:checked')?.value === 'live';
    const incOutside = document.getElementById('vdIncOutsideCb')?.checked ?? true;
    const incDup = document.getElementById('vdIncDupCb')?.checked ?? true;
    const incDeceased = document.getElementById('vdIncDeceasedCb')?.checked ?? true;
    const incSupporter = document.getElementById('vdIncSupporterCb')?.checked ?? true;

    const totalVoters = scoped.length;
    const maxSerial = Math.max(...scoped.map(v => v.serial_no));
    const votedList = isLive ? scoped.filter(v => v.is_voted).map(v => v.serial_no) : [];
    const votedCount = votedList.length;
    const pendingCount = totalVoters - votedCount;
    const turnoutPct = totalVoters > 0 ? ((votedCount / totalVoters) * 100).toFixed(1) : '0.0';

    const outsideSet = new Set(scoped.filter(v => v.is_outside).map(v => v.serial_no));
    const dupSet = new Set(scoped.filter(v => v.is_duplicate || v.survey_status === 'DUPLICATE').map(v => v.serial_no));
    const deceasedSet = new Set(scoped.filter(v => v.survey_status === 'DECEASED').map(v => v.serial_no));
    const supporterSet = new Set(scoped.filter(v => String(v.voter_category) === '100').map(v => v.serial_no));
    const activeSerials = new Set(scoped.map(v => v.serial_no));

    const activeTab = document.getElementById(`vdTab${currentPart}`);
    const gpName = activeTab ? (activeTab.dataset.gp || '').trim() : '';
    const gpTxt = gpName ? `${gpName} · ` : '';
    const wardTxt = (currentWard !== null && currentWard !== undefined) ? `${gpTxt}वार्ड ${currentWard} (बूथ ${currentPart})` : `${gpTxt}बूथ ${currentPart}`;
    const nowTime = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
    const totalPages = Math.max(1, Math.ceil(maxSerial / 600));

    const colGroupHtml = '<colgroup>' + '<col class="c-w">'.repeat(20) + '</colgroup>';

    let pagesHtml = '';

    for (let p = 0; p < totalPages; p++) {
      const pageStart = p * 600 + 1;
      const pageEnd = (p + 1) * 600;

      let rowsHtml = '';
      for (let r = 0; r < 30; r++) {
        const rowStart = pageStart + r * 20;
        const rowEnd = rowStart + 19;
        const isCentury = (rowEnd % 100 === 0);
        const trClass = isCentury ? 'class="century-row"' : '';

        let cellsHtml = '';
        for (let c = 0; c < 20; c++) {
          const num = rowStart + c;
          const isBlock = ((c + 1) % 5 === 0 && c < 19) ? 'block-col' : '';

          if (num <= maxSerial && num <= pageEnd) {
            if (activeSerials.has(num)) {
              const isVoted = votedList.includes(num);
              const isDec = incDeceased && deceasedSet.has(num);
              const isDup = incDup && dupSet.has(num);
              const isOut = incOutside && outsideSet.has(num);
              const isStar = incSupporter && supporterSet.has(num);
              const starBadge = (isStar && !isVoted && !isDup && !isDec) ? '<span class="star-tag">★</span>' : '';

              if (isDec) {
                cellsHtml += `<td class="cell deceased ${isBlock}"><span class="dec-box"><span class="dec-cross">†</span><span class="dec-num">${num}</span></span><span class="dec-badge">शांत</span></td>`;
              } else if (isDup) {
                cellsHtml += `<td class="cell rvoter ${isBlock}"><span class="strike-num">${num}</span><span class="dup-badge">DUP</span></td>`;
              } else if (isVoted) {
                cellsHtml += `<td class="cell voted ${isBlock}"><span class="v-box"><span class="v-check">✓</span><span class="v-num">${num}</span></span></td>`;
              } else if (isOut) {
                cellsHtml += `<td class="cell outside ${isBlock}"><span class="out-box"><span class="out-plane">✈</span><span class="out-num">${num}</span></span>${starBadge}</td>`;
              } else {
                cellsHtml += `<td class="cell pending ${isBlock}">${num}${starBadge}</td>`;
              }
            } else {
              // Deleted / gap serial in official roll (ECI deleted / shifted gap)
              cellsHtml += `<td class="cell del-gap ${isBlock}"><span class="strike-num">${num}</span><span class="del-badge">DEL</span></td>`;
            }
          } else {
            cellsHtml += `<td class="cell empty ${isBlock}"></td>`;
          }
        }
        rowsHtml += `<tr ${trClass}>${cellsHtml}</tr>`;
      }

      pagesHtml += `
        <div class="page">
          <div class="top-tag">
            <div class="tag-left">
              <span class="booth-badge">${wardTxt}</span>
              <span class="live-pulse">${isLive ? '🔴 ताज़ा लाइव संस्करण (FRESH SHEET)' : '⚪ ब्लैंक संस्करण (BLANK SHEET)'}</span>
            </div>
            <div class="tag-center">
              कुल: <strong>${totalVoters}</strong> | 
              <span class="stat-voted">मतदान: <strong>${votedCount}</strong> (${turnoutPct}%) [✓]</span> | 
              <span class="stat-pending">शेष: <strong>${pendingCount}</strong></span>
            </div>
            <div class="tag-right">
              अपडेट: ${nowTime} | पृष्ठ ${p + 1}/${totalPages}
            </div>
          </div>

          <div class="legend-bar">
            <span><span class="leg-box leg-white"></span> <strong>सफेद = शेष स्थानीय</strong> (पेन से गोला लगाएं)</span>
            <span><span class="leg-box leg-green">✓</span> <strong>हरा (✓) = वोट हो चुका</strong> (सॉफ्टवेयर दर्ज / बोगस रोकें)</span>
            <span><span class="leg-box leg-outside">✈</span> <strong>एम्बर (✈) = प्रवासी / बाहर</strong> (फर्जी वोट सतर्कता)</span>
            <span><span class="leg-box leg-del"><s>12</s></span> <strong>कटा हुआ [DUP] = डुप्लीकेट / दोहराव</strong></span>
            <span><span class="leg-box leg-dec">†</span> <strong>स्लेट (†) = शांत / फोत</strong></span>
            <span><span class="leg-star">★</span> <strong>पक्का समर्थक</strong></span>
          </div>

          <table class="grid">
            ${colGroupHtml}
            <tbody>
              ${rowsHtml}
            </tbody>
          </table>
        </div>
      `;
    }

    const printHtml = `<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="utf-8">
<title>${wardTxt} - अंकन पत्रक</title>
<style>
@page {
    size: A4 portrait;
    margin: 4mm 5mm 3mm 5mm;
}
* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
}
html, body {
    margin: 0;
    padding: 0;
    font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
    color: #0f172a;
    background: #fff;
}
.page {
    box-sizing: border-box;
    width: 100%;
    height: 276mm;
    max-height: 276mm;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    page-break-after: always;
    break-after: page;
}
.page:last-child {
    page-break-after: auto;
    break-after: auto;
}
.top-tag {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 8pt;
    font-weight: 700;
    color: #1e3a8a;
    padding: 1px 4px 2px 4px;
    border-bottom: 2px solid #0f172a;
    margin-bottom: 2px;
    flex-shrink: 0;
}
.tag-left {
    display: flex;
    align-items: center;
    gap: 6px;
}
.booth-badge {
    background: #1e3a8a;
    color: #fff;
    padding: 1.5px 7px;
    border-radius: 3px;
    font-size: 8pt;
    font-weight: 800;
}
.live-pulse {
    color: #b91c1c;
    font-size: 7.2pt;
    font-weight: 800;
    background: #fee2e2;
    padding: 1.5px 5px;
    border-radius: 3px;
    border: 0.5px solid #fca5a5;
}
.tag-center {
    background: #f8fafc;
    padding: 1.5px 8px;
    border-radius: 3px;
    border: 1px solid #cbd5e1;
    font-size: 7.8pt;
    color: #334155;
}
.stat-voted {
    color: #15803d;
    font-weight: 800;
}
.stat-pending {
    color: #b45309;
    font-weight: 800;
}
.tag-right {
    color: #475569;
    font-size: 7.5pt;
    font-weight: 700;
}
.legend-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #f1f5f9;
    padding: 2px 6px;
    font-size: 6.6pt;
    color: #1e293b;
    margin-bottom: 2px;
    border: 1px solid #cbd5e1;
    border-radius: 3px;
    font-weight: 600;
    flex-shrink: 0;
}
.leg-box {
    display: inline-block;
    width: 12px;
    height: 10px;
    line-height: 10px;
    text-align: center;
    border: 1px solid #000;
    border-radius: 1.5px;
    vertical-align: middle;
    font-size: 6pt;
    font-weight: 800;
    margin-right: 2px;
}
.leg-white { background: #fff; border-color: #64748b; }
.leg-green { background: #a7f3d0; border-color: #059669; color: #064e3b; }
.leg-outside { background: #fef08a; border-color: #d97706; color: #854d0e; }
.leg-del { background: #fee2e2; border-color: #dc2626; color: #b91c1c; }
.leg-dec { background: #e2e8f0; border-color: #64748b; color: #334155; }
.leg-star { color: #15803d; font-size: 7.5pt; font-weight: 900; }

table.grid {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
    flex-grow: 1;
    border: 2px solid #0f172a;
}
col.c-w { width: 5%; }
td.block-col { border-right: 2px solid #0f172a !important; }
tr.century-row td { border-bottom: 2px solid #0f172a !important; }

td.cell {
    width: 5%;
    max-width: 5%;
    box-sizing: border-box;
    text-align: center;
    vertical-align: middle;
    font-size: 8.5pt;
    font-weight: 800;
    height: 8.4mm;
    max-height: 8.4mm;
    line-height: 8.4mm;
    padding: 0;
    border: 1px solid #475569;
    position: relative;
    user-select: none;
    overflow: hidden;
    white-space: nowrap;
}

td.pending { color: #0f172a; background: #ffffff; }

td.voted { background: #a7f3d0 !important; color: #064e3b !important; }
.v-box {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 1px;
    line-height: 1;
}
.v-check { font-size: 7.5pt; color: #047857; font-weight: 900; }
.v-num { font-size: 8pt; font-weight: 800; }

td.outside { background: #fef08a !important; color: #713f12 !important; }
.out-box {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 1px;
    line-height: 1;
}
.out-plane { font-size: 6.5pt; line-height: 1; }
.out-num { font-size: 8pt; font-weight: 800; }

td.rvoter { background: #fee2e2 !important; color: #991b1b !important; }
td.del-gap { background: #fef2f2 !important; color: #991b1b !important; }
.strike-num {
    text-decoration: line-through;
    text-decoration-color: #dc2626;
    text-decoration-thickness: 1.5px;
    font-size: 8pt;
    font-weight: 800;
    opacity: 0.85;
}
.dup-badge {
    position: absolute;
    bottom: 0.5px;
    right: 1px;
    font-size: 4.5pt;
    font-weight: 900;
    line-height: 1;
    color: #991b1b;
    background: #fecaca;
    border: 0.5px solid #f87171;
    border-radius: 1.5px;
    padding: 0 1px;
    letter-spacing: -0.2px;
}
.del-badge {
    position: absolute;
    bottom: 0.5px;
    right: 1px;
    font-size: 4.5pt;
    font-weight: 900;
    line-height: 1;
    color: #7f1d1d;
    background: #fee2e2;
    border: 0.5px solid #fca5a5;
    border-radius: 1.5px;
    padding: 0 1px;
    letter-spacing: -0.2px;
}

td.deceased { background: #f1f5f9 !important; color: #64748b !important; }
.dec-box {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 1px;
    line-height: 1;
}
.dec-cross { font-size: 7.5pt; color: #475569; font-weight: 900; }
.dec-num { font-size: 8pt; font-weight: 800; }
.dec-badge {
    position: absolute;
    bottom: 0.5px;
    right: 1px;
    font-size: 4.5pt;
    font-weight: 900;
    line-height: 1;
    color: #475569;
    background: #e2e8f0;
    border: 0.5px solid #cbd5e1;
    border-radius: 1.5px;
    padding: 0 1px;
    letter-spacing: -0.2px;
}

.star-tag {
    position: absolute;
    top: 0.5px;
    right: 1px;
    font-size: 5pt;
    color: #15803d;
    line-height: 1;
}

td.empty { background: #f8fafc; border: 1px solid #cbd5e1; }
</style>
</head>
<body>
${pagesHtml}
<script>
window.onafterprint = function() {
    if (window.opener) window.close();
};
<\/script>
</body>
</html>`;

    vdPrintHtml(printHtml);
  };


  // =========================================================================
  // FILTERABLE UNVOTED VOTER LIST ENGINE
  // =========================================================================

  let filteredUnvotedPool = [];

  window.vdOpenUnvotedModal = function () {
    const modal = document.getElementById('vdUnvotedModal');
    if (!modal) return;

    // Populate Caste dropdown from distinct caste tags in votersList
    const casteSelect = document.getElementById('vdUnvotedCasteSelect');
    if (casteSelect) {
      const distinctCastes = [...new Set(votersList.map(v => v.caste_tag).filter(c => c && c.trim()))].sort();
      let optHtml = '<option value="all">सभी समुदाय (All Communities)</option>';
      distinctCastes.forEach(c => {
        optHtml += `<option value="${c}">${c}</option>`;
      });
      casteSelect.innerHTML = optHtml;
    }

    modal.classList.remove('hidden');
    window.vdFilterUnvotedList();
  };

  window.vdCloseUnvotedModal = function () {
    const modal = document.getElementById('vdUnvotedModal');
    if (modal) modal.classList.add('hidden');
  };

  // Filter unvoted voters dynamically
  window.vdFilterUnvotedList = function () {
    const scoped = getWardScopedVoters();
    const unvoted = scoped.filter(v => !v.is_voted);

    const casteVal = document.getElementById('vdUnvotedCasteSelect')?.value || 'all';
    const catVal = document.getElementById('vdUnvotedCategorySelect')?.value || 'all';
    const outsideVal = document.getElementById('vdUnvotedOutsideSelect')?.value || 'local_only';
    const lifeStatusVal = document.getElementById('vdUnvotedLifeStatusSelect')?.value || 'active_only';
    const genderVal = document.getElementById('vdUnvotedGenderSelect')?.value || 'all';
    const sortVal = document.getElementById('vdUnvotedSortSelect')?.value || 'serial';

    filteredUnvotedPool = unvoted.filter(v => {
      // 1. Caste filter
      if (casteVal !== 'all' && v.caste_tag !== casteVal) return false;

      // 2. Sentiment/Category filter
      if (catVal !== 'all' && String(v.voter_category ?? '0') !== String(catVal)) return false;

      // 3. Residence filter
      if (outsideVal === 'local_only' && v.is_outside) return false;
      if (outsideVal === 'outside_only' && !v.is_outside) return false;

      // 4. Life Status / Deduplication Filter
      const isDec = (v.survey_status === 'DECEASED');
      const isDup = (v.survey_status === 'DUPLICATE' || v.is_duplicate);
      if (lifeStatusVal === 'active_only' && (isDec || isDup)) return false;
      if (lifeStatusVal === 'deceased_only' && !isDec) return false;
      if (lifeStatusVal === 'duplicate_only' && !isDup) return false;

      // 5. Gender filter
      if (genderVal !== 'all') {
        const g = String(v.gender || '').toUpperCase();
        if (genderVal === 'M' && !g.startsWith('M') && !g.includes('पु')) return false;
        if (genderVal === 'F' && !g.startsWith('F') && !g.includes('म') && !g.includes('स्त्री')) return false;
      }

      return true;
    });

    // 5. Sorting
    if (sortVal === 'house') {
      filteredUnvotedPool.sort((a, b) => (String(a.house_no).localeCompare(String(b.house_no))) || (a.serial_no - b.serial_no));
    } else if (sortVal === 'name') {
      filteredUnvotedPool.sort((a, b) => a.name.localeCompare(b.name));
    } else {
      filteredUnvotedPool.sort((a, b) => a.serial_no - b.serial_no);
    }

    // Update Counter & Estimate
    const matchCountEl = document.getElementById('vdUnvotedMatchCount');
    const pageEstEl = document.getElementById('vdUnvotedPageEstimate');
    if (matchCountEl) matchCountEl.textContent = filteredUnvotedPool.length;
    if (pageEstEl) {
      const pCount = Math.max(1, Math.ceil(filteredUnvotedPool.length / 32));
      pageEstEl.textContent = `${pCount} पेज (A4)`;
    }

    // Render Preview Table
    const tbody = document.getElementById('vdUnvotedTableBody');
    if (!tbody) return;

    if (filteredUnvotedPool.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="8" class="p-8 text-center text-slate-400 font-bold">
            चयनित फिल्टर अनुसार कोई शेष मतदाता नहीं मिला।
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = filteredUnvotedPool.map(v => {
      let statusBadge = '<span class="text-slate-400 font-medium">सामान्य</span>';
      if (v.survey_status === 'DECEASED') {
        statusBadge = '<span class="text-slate-600 font-bold bg-slate-100 border border-slate-300 px-1.5 py-0.5 rounded text-[10px]">🕊️ शांत</span>';
      } else if (v.survey_status === 'DUPLICATE' || v.is_duplicate) {
        const locTxt = v.duplicate_pair_ward ? `व.${v.duplicate_pair_ward} ` : '';
        const pairTxt = v.duplicate_pair_sl ? `: ${locTxt}#${v.duplicate_pair_sl}` : '';
        statusBadge = `<span class="text-rose-600 font-bold bg-rose-50 border border-rose-200 px-1.5 py-0.5 rounded text-[10px]" title="मूल: ${locTxt}क्र. #${v.duplicate_pair_sl || ''}">DUP${pairTxt}</span>`;
      } else if (v.duplicate_pair_sl) {
        const locTxt = v.duplicate_pair_ward ? `व.${v.duplicate_pair_ward} ` : '';
        statusBadge = `<span class="text-blue-600 font-bold bg-blue-50 border border-blue-200 px-1.5 py-0.5 rounded text-[10px]" title="प्रतिलिपि: ${locTxt}क्र. #${v.duplicate_pair_sl}">PAIR: ${locTxt}#${v.duplicate_pair_sl}</span>`;
      } else if (v.is_outside) {
        statusBadge = '<span class="text-amber-700 font-bold bg-amber-50 px-1.5 py-0.5 rounded text-[10px]">✈ प्रवासी</span>';
      } else if (String(v.voter_category) === '100') {
        statusBadge = '<span class="text-emerald-700 font-bold bg-emerald-50 px-1.5 py-0.5 rounded text-[10px]">★ समर्थक</span>';
      }

      const phoneDisplay = v.contact_phone ? `<span class="font-mono font-bold text-slate-700">${v.contact_phone}</span>` : '<span class="text-slate-300">-</span>';

      return `
        <tr class="hover:bg-slate-50 transition">
          <td class="p-2 text-center font-black text-indigo-950">${v.serial_no}</td>
          <td class="p-2 font-bold text-slate-900">${v.name}</td>
          <td class="p-2 text-slate-600">${v.rel_name || '-'}</td>
          <td class="p-2 text-center font-mono text-slate-700">${v.house_no || '-'}</td>
          <td class="p-2 text-center text-slate-600">${v.age ? v.age + '/' : ''}${v.gender || '-'}</td>
          <td class="p-2 text-slate-600 font-medium">${v.caste_tag || '-'}</td>
          <td class="p-2">${phoneDisplay}</td>
          <td class="p-2 text-center">${statusBadge}</td>
        </tr>
      `;
    }).join('');
  };

  // Generate and Print A4 Canvassing Table
  window.vdPrintUnvotedList = function () {
    if (filteredUnvotedPool.length === 0) {
      if (typeof window.showToast === 'function') {
        window.showToast('प्रिंट करने हेतु कोई मतदाता नहीं मिला।', 'warning', 3000);
      } else {
        alert('प्रिंट करने हेतु कोई मतदाता नहीं मिला।');
      }
      return;
    }

    const activeTab = document.getElementById(`vdTab${currentPart}`);
    const gpName = activeTab ? (activeTab.dataset.gp || '').trim() : '';
    const gpTxt = gpName ? `${gpName} · ` : '';
    const wardTxt = (currentWard !== null && currentWard !== undefined) ? `${gpTxt}वार्ड ${currentWard} (बूथ ${currentPart})` : `${gpTxt}बूथ ${currentPart}`;
    const nowTime = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
    const nowDate = new Date().toLocaleDateString('hi-IN', { day: '2-digit', month: '2-digit', year: 'numeric' });

    const casteVal = document.getElementById('vdUnvotedCasteSelect')?.value || 'all';
    const catVal = document.getElementById('vdUnvotedCategorySelect')?.value || 'all';
    const lifeStatusVal = document.getElementById('vdUnvotedLifeStatusSelect')?.value || 'active_only';
    const lifeStatusTxt = lifeStatusVal === 'active_only' ? 'जीवित व मान्य' : lifeStatusVal === 'deceased_only' ? 'केवल शांत' : lifeStatusVal === 'duplicate_only' ? 'केवल डुप्लीकेट' : 'समस्त';
    const filterDesc = `समुदाय: ${casteVal === 'all' ? 'समस्त' : casteVal} | श्रेणी: ${catVal === 'all' ? 'समस्त' : catVal} | स्थिति: ${lifeStatusTxt} | कुल शेष: ${filteredUnvotedPool.length}`;

    let rowsHtml = '';
    filteredUnvotedPool.forEach((v, idx) => {
      let tagTxt = '';
      if (v.survey_status === 'DECEASED') tagTxt = '🕊️ शांत';
      else if (v.survey_status === 'DUPLICATE' || v.is_duplicate) {
        const locTxt = v.duplicate_pair_ward ? `व.${v.duplicate_pair_ward} ` : '';
        tagTxt = v.duplicate_pair_sl ? `[DUP: ${locTxt}#${v.duplicate_pair_sl}]` : '[DUP]';
      }
      else if (v.duplicate_pair_sl) {
        const locTxt = v.duplicate_pair_ward ? `व.${v.duplicate_pair_ward} ` : '';
        tagTxt = `[PAIR: ${locTxt}#${v.duplicate_pair_sl}]`;
      }
      else if (v.is_outside) tagTxt = '✈ प्रवासी';
      else if (String(v.voter_category) === '100') tagTxt = '★ समर्थक';

      rowsHtml += `
        <tr>
          <td class="center sl-col">${v.serial_no}</td>
          <td class="name-col"><strong>${v.name}</strong></td>
          <td class="rel-col">${v.rel_name || '-'}</td>
          <td class="center">${v.house_no || '-'}</td>
          <td class="center">${v.age ? v.age + '/' : ''}${v.gender || '-'}</td>
          <td class="center">${v.caste_tag || '-'}</td>
          <td class="center phone-col">${v.contact_phone || '-'}</td>
          <td class="center">${tagTxt}</td>
          <td class="center tick-col"><span class="tick-circle">○</span></td>
        </tr>
      `;
    });

    const printHtml = `<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="utf-8">
<title>${wardTxt} - शेष मतदाता सूची</title>
<style>
@page {
    size: A4 portrait;
    margin: 8mm 7mm 8mm 7mm;
    @bottom-right {
        content: counter(page) " / " counter(pages);
        font-size: 8pt;
    }
}
* {
    box-sizing: border-box;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
}
body {
    font-family: 'Segoe UI', Arial, sans-serif;
    margin: 0;
    padding: 0;
    font-size: 8.5pt;
    color: #0f172a;
}
.header-card {
    border: 1.5px solid #1e3a8a;
    border-radius: 4px;
    padding: 6px 10px;
    margin-bottom: 6px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #f8fafc;
}
.title-block h1 {
    margin: 0;
    font-size: 11pt;
    font-weight: 800;
    color: #1e3a8a;
}
.title-block p {
    margin: 2px 0 0 0;
    font-size: 7.5pt;
    color: #475569;
}
.stats-badge {
    background: #e2e8f0;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 8pt;
    font-weight: bold;
    color: #0f172a;
    text-align: right;
}
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 8pt;
}
thead {
    display: table-header-group;
}
tr {
    page-break-inside: avoid;
}
th {
    background: #1e3a8a !important;
    color: #ffffff !important;
    font-weight: 800;
    padding: 4px 5px;
    border: 1px solid #0f172a;
    text-align: left;
    font-size: 7.5pt;
}
th.center, td.center {
    text-align: center;
}
td {
    padding: 3px 4px;
    border: 1px solid #cbd5e1;
    vertical-align: middle;
}
tbody tr:nth-child(even) {
    background-color: #f8fafc;
}
.sl-col {
    font-weight: 900;
    color: #1e3a8a;
    width: 36px;
    font-size: 9pt;
}
.name-col {
    width: 125px;
}
.rel-col {
    width: 110px;
    color: #334155;
}
.phone-col {
    font-family: Consolas, monospace;
    font-weight: 700;
    color: #047857;
    width: 90px;
}
.tick-col {
    width: 38px;
}
.tick-circle {
    font-size: 12pt;
    color: #64748b;
}
</style>
</head>
<body>
<div class="header-card">
    <div class="title-block">
        <h1>${wardTxt} — शेष मतदाता संपर्क सूची (जिन्होंने वोट नहीं डाला)</h1>
        <p>${filterDesc} | तारीख: ${nowDate} (${nowTime})</p>
    </div>
    <div class="stats-badge">
        कुल शेष: ${filteredUnvotedPool.length}
    </div>
</div>

<table>
    <thead>
        <tr>
            <th class="center" style="width: 36px;">क्र. सं.</th>
            <th style="width: 125px;">मतदाता का नाम</th>
            <th style="width: 110px;">संबंधी का नाम</th>
            <th class="center" style="width: 44px;">मकान नं.</th>
            <th class="center" style="width: 44px;">आयु/लिंग</th>
            <th class="center" style="width: 60px;">समुदाय</th>
            <th class="center" style="width: 90px;">मोबाइल नंबर</th>
            <th class="center" style="width: 55px;">स्थिति</th>
            <th class="center" style="width: 38px;">टिक</th>
        </tr>
    </thead>
    <tbody>
        ${rowsHtml}
    </tbody>
</table>

<script>
window.onafterprint = function() {
    if (window.opener) window.close();
};
<\/script>
</body>
</html>`;

    vdPrintHtml(printHtml);
  };

  // =========================================================================
  // VOICE BROADCAST MODAL HANDLERS
  // =========================================================================
  window.vdOpenVoiceCallingModal = async function () {
    const modal = document.getElementById('vdVoiceCallingModal');
    if (!modal) return;

    const scoped = getWardScopedVoters();
    const unvoted = scoped.filter(v => !v.is_voted);

    const totalUnvotedEl = document.getElementById('vdVoiceUnvotedTotal');
    const uniqueMobilesEl = document.getElementById('vdVoiceUniqueMobiles');
    const subTitle = document.getElementById('vdVoiceModalSubtitle');
    const soundSelect = document.getElementById('vdVoiceSoundSelect');
    const balanceDisplay = document.getElementById('vdVoiceBalanceDisplay');
    const statusBanner = document.getElementById('vdVoiceStatusBanner');

    if (statusBanner) statusBanner.classList.add('hidden');

    const totalUnvoted = unvoted.length;
    const uniqueMobilesSet = new Set();
    unvoted.forEach(v => {
      const phone = String(v.contact_phone || '').replace(/\D/g, '');
      if (phone.length >= 10) {
        uniqueMobilesSet.add(phone.slice(-10));
      }
    });

    if (totalUnvotedEl) totalUnvotedEl.textContent = totalUnvoted;
    if (uniqueMobilesEl) uniqueMobilesEl.textContent = uniqueMobilesSet.size;

    const activeTab = document.getElementById(`vdTab${currentPart}`);
    const gpName = activeTab ? (activeTab.dataset.gp || '').trim() : '';
    const gpPrefix = gpName ? `${gpName} · ` : '';
    const wardTxt = (currentWard !== null && currentWard !== undefined)
      ? `${gpPrefix}वार्ड ${currentWard} (बूथ ${currentPart})`
      : `${gpPrefix}बूथ ${currentPart}`;
    if (subTitle) subTitle.textContent = `${wardTxt} · शेष ${totalUnvoted} मतदाताओं को कॉल`;

    modal.classList.remove('hidden');

    // Fetch live balance
    const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
    const tenantQuery = curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : '';

    try {
      if (balanceDisplay) balanceDisplay.textContent = '...';
      const balRes = await fetch(`/api/voice/balance${tenantQuery}`);
      const balData = await balRes.json();
      if (balData.success && balData.quota) {
        const avail = balData.quota.available ?? 0;
        if (balanceDisplay) balanceDisplay.textContent = `${avail} कॉल`;
      } else {
        if (balanceDisplay) balanceDisplay.textContent = '0 कॉल';
      }
    } catch (e) {
      if (balanceDisplay) balanceDisplay.textContent = '0';
    }

    // Fetch approved sounds
    try {
      if (soundSelect) soundSelect.innerHTML = '<option value="">ध्वनि संदेश लोड हो रहे हैं...</option>';
      const sndRes = await fetch(`/api/voice/sounds${tenantQuery}`);
      const sndData = await sndRes.json();
      if (sndData.success && Array.isArray(sndData.sounds) && sndData.sounds.length > 0) {
        let optHtml = '<option value="">-- अनुमोदित ऑडियो संदेश चुनें --</option>';
        sndData.sounds.forEach(s => {
          const sId = s.audio_id || '';
          const sName = s.sound_name || s.name || s;
          const sLabel = s.display_label || s.original_filename || sName;
          const durText = s.duration_seconds ? `${s.duration_seconds}s` : '';
          const credText = `${s.credits_per_call || 1} क्रेडिट`;
          const badge = durText ? ` [${durText} · ${credText}]` : ` [${credText}]`;
          optHtml += `<option value="${sId || sName}" data-audio-id="${sId}" data-sound-name="${sName}" data-credits="${s.credits_per_call || 1}">${sLabel}${badge}</option>`;
        });
        if (soundSelect) soundSelect.innerHTML = optHtml;
      } else {
        if (soundSelect) {
          soundSelect.innerHTML = '<option value="">⚠️ कोई अनुमोदित ऑडियो उपलब्ध नहीं (प्रचार केंद्र से अपलोड करें)</option>';
        }
      }
    } catch (e) {
      if (soundSelect) soundSelect.innerHTML = '<option value="">त्रुटि: ऑडियो लोड नहीं हो सके</option>';
    }
  };

  window.vdCloseVoiceCallingModal = function () {
    const modal = document.getElementById('vdVoiceCallingModal');
    if (modal) modal.classList.add('hidden');
  };

  window.vdLaunchVoiceBlast = async function () {
    const soundSelect = document.getElementById('vdVoiceSoundSelect');
    const selectedVal = soundSelect ? soundSelect.value.trim() : '';
    const selectedOpt = soundSelect && soundSelect.selectedIndex >= 0 ? soundSelect.options[soundSelect.selectedIndex] : null;
    const audioId = selectedOpt ? (selectedOpt.dataset.audioId || (selectedVal && /^\d+$/.test(selectedVal) ? selectedVal : null)) : null;
    const soundName = selectedOpt ? (selectedOpt.dataset.soundName || selectedOpt.textContent) : selectedVal;
    const statusBanner = document.getElementById('vdVoiceStatusBanner');
    const btn = document.getElementById('btnLaunchVoiceBlast');
    const btnText = document.getElementById('btnLaunchVoiceBlastText');

    if (!selectedVal) {
      if (typeof window.showToast === 'function') {
        window.showToast('कृपया पहले अनुमोदित ऑडियो संदेश चुनें।', 'warning', 3000);
      } else {
        alert('कृपया पहले अनुमोदित ऑडियो संदेश चुनें।');
      }
      return;
    }

    const uniqueCount = parseInt(document.getElementById('vdVoiceUniqueMobiles')?.textContent || '0', 10);
    if (uniqueCount === 0) {
      if (typeof window.showToast === 'function') {
        window.showToast('कॉल करने हेतु कोई वैध मोबाइल नंबर उपलब्ध नहीं है।', 'warning', 3000);
      } else {
        alert('कॉल करने हेतु कोई वैध मोबाइल नंबर उपलब्ध नहीं है।');
      }
      return;
    }

    const confirmMsg = `क्या आप बूथ ${currentPart} के ${uniqueCount} शेष मतदाताओं को ऑडियो कॉल शुरू करना चाहते हैं?`;
    if (!confirm(confirmMsg)) return;

    if (btn) btn.disabled = true;
    if (btnText) btnText.textContent = 'कॉल भेजी जा रही है...';
    if (statusBanner) {
      statusBanner.className = 'p-3 rounded-xl text-xs font-bold bg-indigo-50 border border-indigo-200 text-indigo-900 flex items-center gap-2';
      statusBanner.innerHTML = '<span class="animate-spin">⏳</span><span>कॉलिंग गेटवे से संपर्क किया जा रहा है...</span>';
      statusBanner.classList.remove('hidden');
    }

    const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
    const tenantQuery = curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : '';

    const payload = {
      target_type: 'unvoted',
      part_no: currentPart,
      ward_no: (typeof currentWard !== 'undefined' && currentWard && currentWard !== 'all') ? currentWard : null,
      audio_id: audioId ? parseInt(audioId, 10) : null,
      sound_name: soundName,
      campaign_name: `PollingDay Part ${currentPart}${typeof currentWard !== 'undefined' && currentWard && currentWard !== 'all' ? ' Ward ' + currentWard : ''} Unvoted Blast`
    };

    try {
      const res = await fetch(`/api/voice/dispatch${tenantQuery}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();

      if (res.ok && data.success) {
        if (statusBanner) {
          statusBanner.className = 'p-3 rounded-xl text-xs font-bold bg-emerald-50 border border-emerald-200 text-emerald-900 flex items-center gap-2';
          statusBanner.innerHTML = `<span>✅</span><span>वॉयस ब्रॉडकास्ट सफलतापूर्वक शुरू हो गया! (${data.total_dispatched || uniqueCount} कॉल कतारबद्ध)</span>`;
        }
        if (typeof window.showToast === 'function') {
          window.showToast(`✅ वॉयस कॉल अभियान सफलतापूर्वक शुरू! (${data.total_dispatched || uniqueCount} कॉल्स)`, 'success', 4000);
        }
        setTimeout(() => {
          window.vdCloseVoiceCallingModal();
        }, 2200);
      } else {
        const errMsg = data.error || 'वॉयस कॉल डिस्पैच में विफलता।';
        if (statusBanner) {
          statusBanner.className = 'p-3 rounded-xl text-xs font-bold bg-rose-50 border border-rose-200 text-rose-900 flex items-center gap-2';
          statusBanner.innerHTML = `<span>❌</span><span>${errMsg}</span>`;
        }
        if (typeof window.showToast === 'function') {
          window.showToast(errMsg, 'error', 4000);
        }
      }
    } catch (err) {
      const errMsg = `नेटवर्क त्रुटि: ${err.message}`;
      if (statusBanner) {
        statusBanner.className = 'p-3 rounded-xl text-xs font-bold bg-rose-50 border border-rose-200 text-rose-900 flex items-center gap-2';
        statusBanner.innerHTML = `<span>❌</span><span>${errMsg}</span>`;
      }
    } finally {
      if (btn) btn.disabled = false;
      if (btnText) btnText.textContent = 'कॉल शुरू करें (Launch Voice Blast)';
    }
  };

})();
