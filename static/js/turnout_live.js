/**
 * turnout_live.js - Live Turnout & Polling Operations with Mobile Rapid Keypad
 * Part of SmartBooth (Mobile & Outdoor Polling Platform)
 */

(function () {
  'use strict';


  function getTenantParam() {
    return window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
  }

  // Live Polling & GOTV Analytics Engine

  window.loadTurnoutData = async function () {
    try {
      const curTenant = getTenantParam();
      const url = '/api/turnout/stats' + (curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : '');
      const res = await fetch(url);
      const data = await res.json();
      if (!data.success) return;

      const tot = data.total_voters || 0;
      const vtd = data.total_voted || 0;
      const pnd = Math.max(0, tot - vtd);
      const pct = data.overall_turnout_percent || (tot > 0 ? ((vtd / tot) * 100).toFixed(1) : '0.0');

      const elTot = document.getElementById('turnoutStatTotal');
      if (elTot) elTot.textContent = Number(tot).toLocaleString('en-IN');

      const elVtd = document.getElementById('turnoutStatVoted');
      if (elVtd) elVtd.textContent = Number(vtd).toLocaleString('en-IN');

      const elSub = document.getElementById('turnoutStatSubVoted');
      if (elSub && data.voted_by_gender) {
        elSub.textContent = `पुरुष: ${data.voted_by_gender.M || 0} | महिला: ${data.voted_by_gender.F || 0}`;
      }

      const elPct = document.getElementById('turnoutStatPct');
      if (elPct) elPct.textContent = `${pct}%`;

      const elBar = document.getElementById('turnoutProgressBar');
      if (elBar) elBar.style.width = `${pct}%`;

      const elPnd = document.getElementById('turnoutStatPending');
      if (elPnd) elPnd.textContent = Number(pnd).toLocaleString('en-IN');

      const elFavPct = document.getElementById('turnoutStatFavoredPct');
      if (elFavPct && data.favored_stats) {
        elFavPct.textContent = `${data.favored_stats.turnout_pct || 0}%`;
      }
      const elFavRatio = document.getElementById('turnoutStatFavoredRatio');
      if (elFavRatio && data.favored_stats) {
        elFavRatio.textContent = `${data.favored_stats.voted || 0} / ${data.favored_stats.total || 0} वोट पड़े`;
      }
    } catch (e) {
      console.error('loadTurnoutData error:', e);
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('turnoutViewContainer');
    if (container) {
      window.loadTurnoutData();
    }
  });

})();
