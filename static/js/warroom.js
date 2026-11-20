/**
 * warroom.js - War Room Political Strategy & Sentiment Analytics
 * Part of SmartBooth (Mobile & Outdoor Polling Platform)
 * 100% Mobile & PWA Card-First Architecture + Desktop Analytics Table
 */

(function () {
  'use strict';

  let cachedWarroomStats = null;
  let allWarroomUnits = [];
  let currentSort = 'num_asc';
  let warroomRefreshTimer = null;

  // Toggle Mathematical Margin Explanation Drawer
  window.toggleMarginHelp = function () {
    const drawer = document.getElementById('wrMarginHelpDrawer');
    const chevron = document.getElementById('wrMarginHelpChevron');
    if (!drawer) return;
    const isHidden = drawer.classList.contains('hidden');
    if (isHidden) {
      drawer.classList.remove('hidden');
      if (chevron) chevron.textContent = '▲';
    } else {
      drawer.classList.add('hidden');
      if (chevron) chevron.textContent = '▼';
    }
  };

  /**
   * Load warroom analytics from server.
   * @param {boolean} isSilent - When true (background periodic polling), updates data in-place without wiping DOM or showing loading spinners.
   */
  window.loadWarroomData = async function (isSilent = false) {
    const cardsContainer = document.getElementById('wrCardsContainer');
    const tableBody = document.getElementById('wrBoothTableBody');
    const lastUpdatedText = document.getElementById('wrLastUpdatedText');

    // Only show full loading placeholders on initial non-silent load when no data is cached yet
    if (!isSilent && !cachedWarroomStats) {
      if (cardsContainer) {
        cardsContainer.innerHTML = `
          <div class="col-span-full p-8 text-center bg-white rounded-xl border border-slate-200 text-slate-400 text-xs font-bold">
            लोड हो रहा है (Loading war room metrics)...
          </div>
        `;
      }
      if (tableBody) {
        tableBody.innerHTML = `<tr><td colspan="8" class="p-4 text-center text-slate-400">Loading war room metrics...</td></tr>`;
      }
    }

    if (isSilent && lastUpdatedText) {
      lastUpdatedText.textContent = 'सिंक हो रहा है...';
    }

    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const url = '/api/warroom/stats' + (curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : '');
      const res = await fetch(url);
      const data = await res.json();

      if (!data.success) {
        if (!isSilent && !cachedWarroomStats) {
          if (cardsContainer) cardsContainer.innerHTML = `<div class="col-span-full p-6 text-center bg-rose-50 text-rose-600 rounded-xl border border-rose-200 font-bold text-xs">डेटा लोड करने में असमर्थ</div>`;
          if (tableBody) tableBody.innerHTML = `<tr><td colspan="8" class="p-4 text-center text-rose-500 font-bold">डेटा लोड करने में असमर्थ</td></tr>`;
        }
        if (lastUpdatedText) lastUpdatedText.textContent = 'पुनः प्रयास जारी';
        return;
      }

      cachedWarroomStats = data;
      const sentiment = data.sentiment || {};
      const projection = data.projection || {};
      const turnout = data.turnout || {};

      const tot = data.total_electorate || data.total_voters || sentiment.total || 0;
      const fav = data.favored_count ?? sentiment.favored_100 ?? 0;
      const swg = data.swing_count ?? sentiment.swing_50 ?? 0;
      const opp = data.opposition_count ?? sentiment.opposition ?? 0;
      const unc = data.unclassified_count ?? sentiment.unclassified ?? Math.max(0, tot - fav - swg - opp);

      const votedTot = data.voted_count ?? turnout.voted ?? 0;
      const votedFav = data.voted_favored_count ?? 0;
      const votedSwg = data.voted_swing_count ?? 0;
      const votedOpp = data.voted_opposition_count ?? 0;
      const turnoutPct = data.turnout_pct ?? turnout.turnout_pct ?? (tot > 0 ? ((votedTot / tot) * 100).toFixed(1) : '0.0');

      // Projected Win Margin Calculations
      const projVotes = projection.projected_votes ?? Math.round(fav + (swg * 0.5) + (unc * 0.3));
      const projOpp = projection.projected_opp_votes ?? Math.round(opp + (swg * 0.5) + (unc * 0.30));
      const projLead = projection.projected_lead ?? (projVotes - projOpp);

      // Live Actual Polled Lead (Ground Reality from EVM / ballot box today)
      const polledLead = votedFav - votedOpp;

      // Update Top KPI Numbers
      const elProjV = document.getElementById('wrProjVotes');
      if (elProjV) elProjV.textContent = Number(projVotes).toLocaleString('en-IN');

      const elProjO = document.getElementById('wrProjOpp');
      if (elProjO) elProjO.textContent = Number(projOpp).toLocaleString('en-IN');

      const elProjL = document.getElementById('wrProjLead');
      if (elProjL) {
        elProjL.textContent = (projLead >= 0 ? `+${Number(projLead).toLocaleString('en-IN')}` : `${Number(projLead).toLocaleString('en-IN')}`);
        elProjL.className = `text-xl sm:text-3xl font-black mt-0.5 sm:mt-1 font-mono tracking-tight ${projLead >= 0 ? 'text-amber-300' : 'text-rose-400'}`;
      }

      const elWinBadge = document.getElementById('wrWinBadge');
      if (elWinBadge) {
        if (projLead > 0) {
          elWinBadge.textContent = `🎯 विजयी बढ़त अनुमान: +${Number(projLead).toLocaleString('en-IN')} मत`;
          elWinBadge.className = 'bg-emerald-500 text-slate-950 text-[11px] sm:text-xs font-black px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-full shadow-sm border border-emerald-300';
        } else {
          elWinBadge.textContent = `⚠️ कड़ी टक्कर / बढ़त आवश्यक: ${Number(projLead).toLocaleString('en-IN')} मत`;
          elWinBadge.className = 'bg-rose-500 text-white text-[11px] sm:text-xs font-black px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-full shadow-sm border border-rose-300';
        }
      }

      // Update Live Polled Ballots Strip
      const elPolledLeadBadge = document.getElementById('wrPolledLeadBadge');
      if (elPolledLeadBadge) {
        if (polledLead > 0) {
          elPolledLeadBadge.textContent = `+${Number(polledLead).toLocaleString('en-IN')} मत बढ़त`;
          elPolledLeadBadge.className = 'font-mono font-black text-emerald-400 text-xs sm:text-sm';
        } else if (polledLead === 0) {
          elPolledLeadBadge.textContent = `0 मत (बराबर मुकाबला)`;
          elPolledLeadBadge.className = 'font-mono font-black text-amber-300 text-xs sm:text-sm';
        } else {
          elPolledLeadBadge.textContent = `${Number(polledLead).toLocaleString('en-IN')} मत अंतर`;
          elPolledLeadBadge.className = 'font-mono font-black text-rose-400 text-xs sm:text-sm';
        }
      }

      const elPolledDetailText = document.getElementById('wrPolledDetailText');
      if (elPolledDetailText) {
        elPolledDetailText.textContent = `कुल ${Number(votedTot).toLocaleString('en-IN')} मत पड़े (${turnoutPct}%) • पक्के समर्थक: ${Number(votedFav).toLocaleString('en-IN')} | विपक्षी: ${Number(votedOpp).toLocaleString('en-IN')}`;
      }

      // Update Mathematical Breakdown Drawer Figures
      const elHelpFav = document.getElementById('wrHelpFavCount');
      if (elHelpFav) elHelpFav.textContent = `${Number(fav).toLocaleString('en-IN')} मत (100% पक्का समर्थक)`;

      const elHelpSwg = document.getElementById('wrHelpSwgCount');
      if (elHelpSwg) elHelpSwg.textContent = `+${Number(Math.round(swg * 0.5)).toLocaleString('en-IN')} मत (${Number(swg).toLocaleString('en-IN')} स्विंग का 50%)`;

      const elHelpUnc = document.getElementById('wrHelpUncCount');
      if (elHelpUnc) elHelpUnc.textContent = `+${Number(Math.round(unc * 0.3)).toLocaleString('en-IN')} मत (${Number(unc).toLocaleString('en-IN')} अनमार्क का 30%)`;

      const elHelpLeadFormula = document.getElementById('wrHelpLeadFormula');
      if (elHelpLeadFormula) {
        elHelpLeadFormula.textContent = `${projLead >= 0 ? '+' : ''}${Number(projLead).toLocaleString('en-IN')} मत`;
      }

      const elHelpPolledLeadNote = document.getElementById('wrHelpPolledLeadNote');
      if (elHelpPolledLeadNote) {
        elHelpPolledLeadNote.textContent = `(अब तक मतपेटी में पड़े वोटों पर बढ़त: ${polledLead >= 0 ? '+' : ''}${Number(polledLead).toLocaleString('en-IN')} मत)`;
      }

      // Update Silent Live Timestamp
      if (lastUpdatedText) {
        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        lastUpdatedText.textContent = `लाइव • ${timeStr}`;
      }

      // Unit Breakdown Processing
      allWarroomUnits = data.booth_breakdown || data.booths || [];
      window.sortWarroomUnits(currentSort, false);

      if (!isSilent && window.triggerHaptic) window.triggerHaptic('light');
    } catch (e) {
      console.error('loadWarroomData error:', e);
      if (!isSilent && !cachedWarroomStats) {
        if (cardsContainer) cardsContainer.innerHTML = `<div class="col-span-full p-6 text-center bg-rose-50 text-rose-600 rounded-xl border border-rose-200 font-bold text-xs">नेटवर्क त्रुटि आई</div>`;
        if (tableBody) tableBody.innerHTML = `<tr><td colspan="8" class="p-4 text-center text-rose-500 font-bold">नेटवर्क त्रुटि आई</td></tr>`;
      }
      if (lastUpdatedText) lastUpdatedText.textContent = 'कनेक्शन त्रुटि';
    }
  };

  window.sortWarroomUnits = function (sortVal, triggerFeedback = true) {
    currentSort = sortVal || 'num_asc';

    allWarroomUnits.sort((a, b) => {
      const numA = parseInt(a.part_no, 10) || 0;
      const numB = parseInt(b.part_no, 10) || 0;
      const marginA = (a.favored ?? a.favored_100 ?? 0) - (a.opp ?? a.opposition ?? 0);
      const marginB = (b.favored ?? b.favored_100 ?? 0) - (b.opp ?? b.opposition ?? 0);
      const turnoutA = a.turnout_pct ?? 0;
      const turnoutB = b.turnout_pct ?? 0;
      const totA = a.total ?? 0;
      const totB = b.total ?? 0;

      if (currentSort === 'num_asc') return numA - numB;
      if (currentSort === 'deficit_first') return marginA - marginB;
      if (currentSort === 'strong_first') return marginB - marginA;
      if (currentSort === 'turnout_asc') return turnoutA - turnoutB;
      if (currentSort === 'total_desc') return totB - totA;
      return numA - numB;
    });

    window.filterWarroomUnits();
    if (triggerFeedback && window.triggerHaptic) window.triggerHaptic('selection');
  };

  window.filterWarroomUnits = function () {
    const searchInput = document.getElementById('wrSearchInput');
    const query = (searchInput ? searchInput.value : '').trim().toLowerCase();

    const filtered = allWarroomUnits.filter(u => {
      if (!query) return true;
      const partStr = String(u.part_no || '');
      const nameStr = String(u.name || u.booth_name || '').toLowerCase();
      return partStr.includes(query) || nameStr.includes(query);
    });

    renderWarroomUnits(filtered);
  };

  function getStatusMeta(fav, swg, opp) {
    if (fav === 0 && swg === 0 && opp === 0) {
      return {
        badge: '<span class="bg-slate-100 text-slate-600 border border-slate-300 text-[11px] font-bold px-2.5 py-1 rounded-full whitespace-nowrap">⚪ सर्वे बाकी</span>',
        borderColor: 'border-slate-200',
        label: 'Unsurveyed'
      };
    } else if (fav > opp) {
      const lead = fav - opp;
      return {
        badge: `<span class="bg-emerald-100 text-emerald-900 border border-emerald-300 text-[11px] font-extrabold px-2.5 py-1 rounded-full whitespace-nowrap">🟢 मजबूत किला (+${lead})</span>`,
        borderColor: 'border-emerald-300/80',
        label: 'Stronghold'
      };
    } else if (fav === opp) {
      return {
        badge: '<span class="bg-amber-100 text-amber-900 border border-amber-300 text-[11px] font-extrabold px-2.5 py-1 rounded-full whitespace-nowrap">🟡 कांटे की टक्कर (0)</span>',
        borderColor: 'border-amber-300/80',
        label: 'Battleground'
      };
    } else {
      const deficit = opp - fav;
      return {
        badge: `<span class="bg-rose-100 text-rose-900 border border-rose-300 text-[11px] font-extrabold px-2.5 py-1 rounded-full whitespace-nowrap">🔴 रणनीति आवश्यक (-${deficit})</span>`,
        borderColor: 'border-rose-300/80',
        label: 'Deficit'
      };
    }
  }

  function renderWarroomUnits(units) {
    const isWard = cachedWarroomStats && cachedWarroomStats.is_ward_election;
    const cardsContainer = document.getElementById('wrCardsContainer');
    const tableBody = document.getElementById('wrBoothTableBody');
    const tableFoot = document.getElementById('wrBoothTableFoot');
    const unitCountBadge = document.getElementById('wrUnitCountBadge');

    if (unitCountBadge) {
      const label = isWard ? 'वार्ड' : 'भाग / बूथ';
      unitCountBadge.textContent = `${units.length} ${label}`;
    }

    // Compute Aggregates across the active list
    const totTotal = units.reduce((acc, b) => acc + (b.total || 0), 0);
    const totVotedFav = units.reduce((acc, b) => acc + (b.voted_favored || 0), 0);
    const totFav = units.reduce((acc, b) => acc + (b.favored ?? b.favored_100 ?? 0), 0);
    const totVotedSwg = units.reduce((acc, b) => acc + (b.voted_swing || 0), 0);
    const totSwg = units.reduce((acc, b) => acc + (b.swing ?? b.swing_50 ?? 0), 0);
    const totVotedOpp = units.reduce((acc, b) => acc + (b.voted_opp || 0), 0);
    const totOpp = units.reduce((acc, b) => acc + (b.opp ?? b.opposition ?? 0), 0);
    const totVoted = units.reduce((acc, b) => acc + (b.voted || 0), 0);
    const overallTurnoutPct = totTotal > 0 ? (Math.round((totVoted / totTotal) * 1000) / 10).toFixed(1) : '0.0';
    const totalStatus = getStatusMeta(totFav, totSwg, totOpp);

    // 1. Render Mobile & PWA Strategy Cards
    if (cardsContainer) {
      if (units.length === 0) {
        cardsContainer.innerHTML = `
          <div class="col-span-full p-8 text-center bg-white rounded-xl border border-slate-200 text-slate-500 text-xs font-bold">
            कोई ${isWard ? 'वार्ड' : 'बूथ'} नहीं मिला (No matching units found)
          </div>
        `;
      } else {
        // Prepend Mobile Total Summary Card at the top
        const summaryCardHtml = `
          <div class="col-span-full bg-slate-900 text-white rounded-2xl p-3.5 sm:p-4 border-2 border-indigo-500/50 shadow-md space-y-3">
            <div class="flex items-center justify-between border-b border-slate-800 pb-2.5">
              <div class="flex items-center gap-2">
                <span class="bg-amber-400 text-slate-950 font-black text-xs px-2.5 py-0.5 rounded-md font-mono">∑ कुल</span>
                <span class="text-xs sm:text-sm font-black text-white">समग्र योग (Total Voting Summary)</span>
              </div>
              ${totalStatus.badge}
            </div>
            <div class="grid grid-cols-4 gap-1.5 sm:gap-2 text-center">
              <div class="bg-slate-800/80 rounded-xl p-2 border border-slate-700 flex flex-col justify-center">
                <div class="text-[9px] text-slate-400 font-bold uppercase truncate">कुल मतदान</div>
                <div class="text-[11px] sm:text-sm font-black font-mono text-cyan-300 mt-0.5 leading-tight">
                  ${Number(totVoted).toLocaleString('en-IN')}<span class="text-[9px] text-slate-400 font-normal">/${Number(totTotal).toLocaleString('en-IN')}</span>
                </div>
              </div>
              <div class="bg-slate-800/80 rounded-xl p-2 border border-emerald-500/30 flex flex-col justify-center">
                <div class="text-[9px] text-emerald-400 font-bold uppercase truncate">🟢 पक्के</div>
                <div class="text-[11px] sm:text-sm font-black font-mono text-emerald-300 mt-0.5 leading-tight">
                  ${Number(totVotedFav).toLocaleString('en-IN')}<span class="text-[9px] text-emerald-400/70 font-normal">/${Number(totFav).toLocaleString('en-IN')}</span>
                </div>
              </div>
              <div class="bg-slate-800/80 rounded-xl p-2 border border-amber-500/30 flex flex-col justify-center">
                <div class="text-[9px] text-amber-400 font-bold uppercase truncate">🟡 50-50</div>
                <div class="text-[11px] sm:text-sm font-black font-mono text-amber-300 mt-0.5 leading-tight">
                  ${Number(totVotedSwg).toLocaleString('en-IN')}<span class="text-[9px] text-amber-400/70 font-normal">/${Number(totSwg).toLocaleString('en-IN')}</span>
                </div>
              </div>
              <div class="bg-slate-800/80 rounded-xl p-2 border border-rose-500/30 flex flex-col justify-center">
                <div class="text-[9px] text-rose-400 font-bold uppercase truncate">🔴 विपक्षी</div>
                <div class="text-[11px] sm:text-sm font-black font-mono text-rose-300 mt-0.5 leading-tight">
                  ${Number(totVotedOpp).toLocaleString('en-IN')}<span class="text-[9px] text-rose-400/70 font-normal">/${Number(totOpp).toLocaleString('en-IN')}</span>
                </div>
              </div>
            </div>
            <div class="flex items-center justify-between text-xs font-bold px-1 text-slate-300">
              <span class="flex items-center gap-1">
                <span>🗳️ कुल मतदान:</span>
                <span class="font-mono text-cyan-300 font-black">${overallTurnoutPct}%</span>
                <span class="text-[11px] text-slate-400 font-normal">(${Number(totVoted).toLocaleString('en-IN')} वोट पड़े)</span>
              </span>
              <span class="text-amber-300 font-mono text-[11px]">बढ़त: ${totFav - totOpp >= 0 ? '+' : ''}${totFav - totOpp}</span>
            </div>
          </div>
        `;

        const cardsHtml = units.map(b => {
          const bTot = b.total || 0;
          const bFav = b.favored ?? b.favored_100 ?? 0;
          const bVotedFav = b.voted_favored ?? 0;
          const bSwg = b.swing ?? b.swing_50 ?? 0;
          const bVotedSwg = b.voted_swing ?? 0;
          const bOpp = b.opp ?? b.opposition ?? 0;
          const bVotedOpp = b.voted_opp ?? 0;
          const bVoted = b.voted ?? 0;
          const bTurnoutPct = b.turnout_pct ?? 0;
          const pendingFav = b.pending_favored_gotv ?? Math.max(0, bFav - bVotedFav);

          const status = getStatusMeta(bFav, bSwg, bOpp);

          const favVotedPct = bFav > 0 ? Math.round((bVotedFav / bFav) * 100) : 0;
          const swgVotedPct = bSwg > 0 ? Math.round((bVotedSwg / bSwg) * 100) : 0;
          const oppVotedPct = bOpp > 0 ? Math.round((bVotedOpp / bOpp) * 100) : 0;

          const favWidth = bTot > 0 ? Math.round((bFav / bTot) * 100) : 0;
          const swgWidth = bTot > 0 ? Math.round((bSwg / bTot) * 100) : 0;
          const oppWidth = bTot > 0 ? Math.round((bOpp / bTot) * 100) : 0;
          const uncWidth = Math.max(0, 100 - favWidth - swgWidth - oppWidth);

          const unitTitle = isWard ? `वार्ड ${b.part_no}` : (b.name || `भाग ${b.part_no}`);

          return `
            <div class="bg-white rounded-2xl border-2 ${status.borderColor} p-3.5 shadow-sm space-y-3 transition-all hover:shadow-md">
              <div class="flex items-center justify-between gap-2 border-b border-slate-100 pb-2.5">
                <div class="flex items-center gap-2 min-w-0">
                  <span class="bg-indigo-900 text-white font-black text-xs px-2.5 py-1 rounded-lg shadow-2xs font-mono shrink-0">
                    ${unitTitle}
                  </span>
                  <span class="text-xs text-slate-600 font-bold truncate">
                    <span class="text-indigo-700 font-black">${Number(bVoted).toLocaleString('en-IN')}</span> / ${Number(bTot).toLocaleString('en-IN')} कुल मत
                  </span>
                </div>
                ${status.badge}
              </div>

              <div class="space-y-1">
                <div class="flex items-center justify-between text-xs font-bold">
                  <span class="text-slate-700 flex items-center gap-1">
                    <span>🗳️ मतदान टर्नआउट</span>
                  </span>
                  <span class="text-blue-700 font-extrabold font-mono text-sm">
                    ${bTurnoutPct}% <span class="text-[11px] font-normal text-slate-500">(${Number(bVoted).toLocaleString('en-IN')} वोट पड़े)</span>
                  </span>
                </div>
                <div class="w-full bg-slate-100 h-2 rounded-full overflow-hidden border border-slate-200">
                  <div class="bg-gradient-to-r from-blue-600 to-indigo-600 h-full rounded-full transition-all duration-300" style="width: ${Math.min(100, Math.max(0, bTurnoutPct))}%;"></div>
                </div>
              </div>

              <div class="grid grid-cols-3 gap-2">
                <div class="bg-emerald-50/90 border border-emerald-200/90 rounded-xl p-2 text-center shadow-2xs">
                  <div class="text-[10px] font-black text-emerald-800 uppercase tracking-tight">🟢 100% पक्के</div>
                  <div class="text-sm font-black text-emerald-700 font-mono mt-0.5">${Number(bVotedFav).toLocaleString('en-IN')} / ${Number(bFav).toLocaleString('en-IN')}</div>
                  <div class="text-[9px] text-emerald-600 font-bold mt-0.5">${favVotedPct}% वोट पड़े</div>
                </div>

                <div class="bg-amber-50/90 border border-amber-200/90 rounded-xl p-2 text-center shadow-2xs">
                  <div class="text-[10px] font-black text-amber-800 uppercase tracking-tight">🟡 50-50</div>
                  <div class="text-sm font-black text-amber-700 font-mono mt-0.5">${Number(bVotedSwg).toLocaleString('en-IN')} / ${Number(bSwg).toLocaleString('en-IN')}</div>
                  <div class="text-[9px] text-amber-600 font-bold mt-0.5">${swgVotedPct}% वोट पड़े</div>
                </div>

                <div class="bg-rose-50/90 border border-rose-200/90 rounded-xl p-2 text-center shadow-2xs">
                  <div class="text-[10px] font-black text-rose-800 uppercase tracking-tight">🔴 विपक्षी</div>
                  <div class="text-sm font-black text-rose-700 font-mono mt-0.5">${Number(bVotedOpp).toLocaleString('en-IN')} / ${Number(bOpp).toLocaleString('en-IN')}</div>
                  <div class="text-[9px] text-rose-600 font-bold mt-0.5">${oppVotedPct}% वोट पड़े</div>
                </div>
              </div>

              <div class="space-y-1 pt-0.5">
                <div class="flex items-center justify-between text-[10px] font-bold text-slate-400">
                  <span>मतदाता वर्गीकरण अनुपात</span>
                  <span>🟢 ${favWidth}% · 🟡 ${swgWidth}% · 🔴 ${oppWidth}%</span>
                </div>
                <div class="w-full h-1.5 rounded-full overflow-hidden flex bg-slate-200">
                  <div class="bg-emerald-500 h-full" style="width: ${favWidth}%;" title="पक्के: ${favWidth}%"></div>
                  <div class="bg-amber-400 h-full" style="width: ${swgWidth}%;" title="स्विंग: ${swgWidth}%"></div>
                  <div class="bg-rose-500 h-full" style="width: ${oppWidth}%;" title="विपक्षी: ${oppWidth}%"></div>
                  <div class="bg-slate-300 h-full" style="width: ${uncWidth}%;" title="अनमार्क: ${uncWidth}%"></div>
                </div>
              </div>

              ${pendingFav > 0 ? `
                <div class="bg-amber-50 text-amber-900 border border-amber-200 text-xs font-bold px-2.5 py-1.5 rounded-xl flex items-center justify-between">
                  <span class="flex items-center gap-1.5">
                    <span>🎯</span>
                    <span>पक्के समर्थक वोट बाकी (GOTV):</span>
                  </span>
                  <span class="font-black text-amber-900 font-mono bg-amber-200/60 px-2 py-0.5 rounded-md">
                    ${Number(pendingFav).toLocaleString('en-IN')} वोट
                  </span>
                </div>
              ` : `
                <div class="bg-emerald-50 text-emerald-800 border border-emerald-200 text-[11px] font-bold px-2.5 py-1 rounded-xl text-center">
                  ✅ सभी पक्के समर्थकों का शत-प्रतिशत मतदान पूर्ण
                </div>
              `}
            </div>
          `;
        }).join('');

        cardsContainer.innerHTML = summaryCardHtml + cardsHtml;
      }
    }

    // 2. Render Desktop Table Body
    if (tableBody) {
      if (units.length === 0) {
        tableBody.innerHTML = `<tr><td colspan="8" class="p-4 text-center text-slate-400">कोई इकाई उपलब्ध नहीं है।</td></tr>`;
      } else {
        tableBody.innerHTML = units.map(b => {
          const bTot = b.total || 0;
          const bFav = b.favored ?? b.favored_100 ?? 0;
          const bVotedFav = b.voted_favored ?? 0;
          const bSwg = b.swing ?? b.swing_50 ?? 0;
          const bVotedSwg = b.voted_swing ?? 0;
          const bOpp = b.opp ?? b.opposition ?? 0;
          const bVotedOpp = b.voted_opp ?? 0;
          const bVoted = b.voted ?? 0;
          const bTurnoutPct = b.turnout_pct ?? 0;
          const status = getStatusMeta(bFav, bSwg, bOpp);
          const unitTitle = isWard ? `वार्ड ${b.part_no}` : (b.name || `भाग ${b.part_no}`);

          return `
            <tr class="hover:bg-slate-50 border-b border-slate-100">
              <td class="py-2.5 px-3 text-center font-mono font-bold text-sm text-slate-700">${b.part_no || '-'}</td>
              <td class="py-2.5 px-3 font-semibold text-sm text-slate-900">${unitTitle}</td>
              <td class="py-2.5 px-3 text-center font-mono text-sm" title="मतदान हुआ / कुल मतदाता: ${bVoted}/${bTot}">
                <span class="font-black text-indigo-700">${Number(bVoted).toLocaleString('en-IN')}</span> / <span class="font-bold text-slate-700">${Number(bTot).toLocaleString('en-IN')}</span>
              </td>
              <td class="py-2.5 px-3 text-center font-black text-sm text-emerald-700" title="मतदान हुआ / कुल पक्के: ${bVotedFav}/${bFav}">${Number(bVotedFav).toLocaleString('en-IN')}/${Number(bFav).toLocaleString('en-IN')}</td>
              <td class="py-2.5 px-3 text-center font-bold text-sm text-amber-700" title="मतदान हुआ / कुल 50-50: ${bVotedSwg}/${bSwg}">${Number(bVotedSwg).toLocaleString('en-IN')}/${Number(bSwg).toLocaleString('en-IN')}</td>
              <td class="py-2.5 px-3 text-center font-bold text-sm text-rose-700" title="मतदान हुआ / कुल विपक्षी: ${bVotedOpp}/${bOpp}">${Number(bVotedOpp).toLocaleString('en-IN')}/${Number(bOpp).toLocaleString('en-IN')}</td>
              <td class="py-2.5 px-3 text-center font-bold text-sm text-blue-700">${bTurnoutPct}%</td>
              <td class="py-2.5 px-3 text-center">${status.badge}</td>
            </tr>
          `;
        }).join('');
      }
    }

    // 3. Render Desktop Table Foot (Total Voting Row with Explicit Background and High Contrast)
    if (tableFoot) {
      if (units.length === 0) {
        tableFoot.innerHTML = '';
      } else {
        tableFoot.innerHTML = `
          <tr class="bg-slate-900 text-white font-extrabold border-t-2 border-slate-700 shadow-md">
            <td class="bg-slate-900 py-3.5 px-3 text-center text-amber-300 font-mono text-xs font-black">∑ कुल</td>
            <td class="bg-slate-900 py-3.5 px-3 font-black text-white text-sm whitespace-nowrap">समग्र योग (Total Voting)</td>
            <td class="bg-slate-900 py-3.5 px-3 text-center font-black font-mono text-sm text-white" title="समग्र मतदान: ${totVoted}/${totTotal}">
              <span class="text-cyan-300">${Number(totVoted).toLocaleString('en-IN')}</span> <span class="text-slate-400 font-normal">/</span> <span class="text-slate-200">${Number(totTotal).toLocaleString('en-IN')}</span>
            </td>
            <td class="bg-slate-900 py-3.5 px-3 text-center font-black font-mono text-sm" title="कुल पक्के समर्थक मतदान: ${totVotedFav}/${totFav}">
              <span class="text-emerald-400">${Number(totVotedFav).toLocaleString('en-IN')}</span> <span class="text-slate-400 font-normal">/</span> <span class="text-emerald-200">${Number(totFav).toLocaleString('en-IN')}</span>
            </td>
            <td class="bg-slate-900 py-3.5 px-3 text-center font-black font-mono text-sm" title="कुल 50-50 मतदान: ${totVotedSwg}/${totSwg}">
              <span class="text-amber-300">${Number(totVotedSwg).toLocaleString('en-IN')}</span> <span class="text-slate-400 font-normal">/</span> <span class="text-amber-200">${Number(totSwg).toLocaleString('en-IN')}</span>
            </td>
            <td class="bg-slate-900 py-3.5 px-3 text-center font-black font-mono text-sm" title="कुल विपक्षी मतदान: ${totVotedOpp}/${totOpp}">
              <span class="text-rose-400">${Number(totVotedOpp).toLocaleString('en-IN')}</span> <span class="text-slate-400 font-normal">/</span> <span class="text-rose-200">${Number(totOpp).toLocaleString('en-IN')}</span>
            </td>
            <td class="bg-slate-900 py-3.5 px-3 text-center font-black font-mono text-sm text-cyan-300">${overallTurnoutPct}%</td>
            <td class="bg-slate-900 py-3.5 px-3 text-center">${totalStatus.badge}</td>
          </tr>
        `;
      }
    }
  }

  // Initialize and schedule background silent sync
  document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('warroomViewContainer');
    if (container) {
      // First load: non-silent (shows skeleton if empty)
      window.loadWarroomData(false);

      // Periodic timer: strictly silent background refresh without tearing down DOM
      if (warroomRefreshTimer) clearInterval(warroomRefreshTimer);
      warroomRefreshTimer = setInterval(() => {
        if (document.visibilityState === 'visible') {
          window.loadWarroomData(true);
        }
      }, 30000);
    }
  });

})();
