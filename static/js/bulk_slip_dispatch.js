/**
 * static/js/bulk_slip_dispatch.js
 * ===============================
 * Frontend Controller for SmartBooth Campaign Bulk Voter Slip Broadcast.
 * Manages Pre-Flight Estimation, Quota Verification, and Real-Time Progress Monitoring.
 */

(function () {
  'use strict';

  let currentJobId = null;
  let statusPollTimer = null;
  let currentEstimate = null;

  window.openBulkSlipModal = function () {
    if (window.CURRENT_USER && !['admin', 'super_admin'].includes(window.CURRENT_USER.role)) {
      window.showToast?.('यह सुविधा केवल मुख्य व्यवस्थापक (Admin) के लिए अधिकृत है।', 'warning');
      return;
    }

    const modal = document.getElementById('bulkSlipDispatchModal');
    if (!modal) return;

    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    // Populate wards dropdown if available
    populateWardDropdown();

    // Check if there is already an active job running for this campaign
    checkActiveBulkJob();
  };

  window.closeBulkSlipModal = function () {
    const modal = document.getElementById('bulkSlipDispatchModal');
    if (modal) {
      modal.classList.add('hidden');
      document.body.style.overflow = '';
    }
  };

  function populateWardDropdown() {
    const wardSelect = document.getElementById('bulkWardFilter');
    if (!wardSelect) return;

    // Preserve first option
    wardSelect.innerHTML = '<option value="all">🌐 संपूर्ण अभियान (All Wards / Booths)</option>';

    // Try extracting from window.ACTIVE_WARDS or existing booth selectors
    const existingWards = window.ACTIVE_WARDS || window.CAMPAIGN_WARDS || [];
    if (Array.isArray(existingWards) && existingWards.length > 0) {
      existingWards.forEach(w => {
        const opt = document.createElement('option');
        opt.value = w.ward_no || w;
        opt.textContent = `वार्ड ${w.ward_no || w} (${w.name || 'वार्ड'})`;
        wardSelect.appendChild(opt);
      });
    } else {
      // Scrape from filter select if on index page
      const filterSelect = document.getElementById('filterWard') || document.getElementById('partFilter');
      if (filterSelect && filterSelect.options) {
        for (let i = 1; i < filterSelect.options.length; i++) {
          const o = filterSelect.options[i];
          if (o.value && o.value !== 'all' && o.value !== '') {
            const opt = document.createElement('option');
            opt.value = o.value;
            opt.textContent = o.textContent;
            wardSelect.appendChild(opt);
          }
        }
      }
    }
  }

  window.calculateBulkEstimate = async function () {
    const btn = document.getElementById('btnCalculateEstimate');
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span>⏳ गणना की जा रही है...</span>';

    const mode = document.querySelector('input[name="bulkMode"]:checked')?.value || 'SMART_FAMILY';
    const wardVal = document.getElementById('bulkWardFilter')?.value;
    const catVal = document.getElementById('bulkCategoryFilter')?.value;
    const skipRecent = document.getElementById('bulkSkipRecentCheck')?.checked ? 48 : 0;

    const filters = {};
    if (wardVal && wardVal !== 'all') {
      filters.ward_nos = [parseInt(wardVal, 10)];
    }
    if (catVal && catVal !== 'all') {
      filters.category = parseInt(catVal, 10);
    }

    try {
      const res = await fetch('/api/dispatch/bulk/estimate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: mode,
          filters: filters,
          skip_recent_hours: skipRecent
        })
      });

      const data = await res.json();
      if (!data.success) {
        window.showToast?.(`त्रुटि: ${data.error || 'अनुमान नहीं लगाया जा सका'}`, 'error');
        return;
      }

      currentEstimate = data.estimation;
      renderEstimateView(currentEstimate);
    } catch (err) {
      console.error('Failed to calculate estimate:', err);
      window.showToast?.('सर्वर से संपर्क नहीं हो सका। कृपया पुनः प्रयास करें।', 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  };

  function renderEstimateView(est) {
    document.getElementById('bulkStepConfig').classList.add('hidden');
    document.getElementById('bulkStepEstimate').classList.remove('hidden');

    document.getElementById('bulkEstModeBadge').textContent =
      est.mode === 'SMART_FAMILY' ? '🏠 स्मार्ट परिवार समूह' : '👤 कड़ाई से व्यक्तिगत';

    document.getElementById('estTotalElectors').textContent = (est.total_campaign_electors || 0).toLocaleString('en-IN');
    document.getElementById('estPhoneReach').textContent = `${(est.electors_with_phone || 0).toLocaleString('en-IN')}`;
    document.getElementById('estSkippedRecent').textContent = (est.skipped_recent || 0).toLocaleString('en-IN');
    document.getElementById('estTargetMessages').textContent = (est.target_messages || 0).toLocaleString('en-IN');

    const quotaCard = document.getElementById('bulkQuotaStatusCard');
    const launchBtn = document.getElementById('btnLaunchBulkBroadcast');

    if (est.has_sufficient_quota) {
      quotaCard.className = 'rounded-2xl p-4 border border-emerald-200 bg-emerald-50/80 flex items-center justify-between gap-3';
      quotaCard.innerHTML = `
        <div class="flex items-center gap-2.5">
          <span class="text-2xl">🟢</span>
          <div>
            <p class="font-black text-xs text-emerald-950">WhatsApp संदेश कोटा पर्याप्त है</p>
            <p class="text-[11px] text-emerald-800">उपलब्ध: ${est.available_credits.toLocaleString('en-IN')} क्रेडिट्स | आवश्यक: ${est.target_messages.toLocaleString('en-IN')} क्रेडिट्स</p>
          </div>
        </div>
        <span class="bg-emerald-600 text-white font-black text-[10px] px-2.5 py-1 rounded-lg">अनुमोदित ✓</span>
      `;
      launchBtn.disabled = false;
      launchBtn.classList.remove('opacity-50', 'cursor-not-allowed');
    } else {
      quotaCard.className = 'rounded-2xl p-4 border border-rose-200 bg-rose-50/80 flex items-center justify-between gap-3';
      quotaCard.innerHTML = `
        <div class="flex items-center gap-2.5">
          <span class="text-2xl">🔴</span>
          <div>
            <p class="font-black text-xs text-rose-950">अपर्याप्त WhatsApp क्रेडिट सीमा!</p>
            <p class="text-[11px] text-rose-800">उपलब्ध: ${est.available_credits.toLocaleString('en-IN')} | आवश्यक: ${est.target_messages.toLocaleString('en-IN')} (कमी: ${est.deficit.toLocaleString('en-IN')} क्रेडिट्स)</p>
          </div>
        </div>
        <a href="https://wa.me/917300179679?text=Need%20WhatsApp%20Credits%20for%20SmartBooth" target="_blank" class="bg-rose-600 hover:bg-rose-700 text-white font-black text-[10px] px-3 py-1.5 rounded-lg shrink-0">क्रेडिट बढ़ाएं 💬</a>
      `;
      launchBtn.disabled = true;
      launchBtn.classList.add('opacity-50', 'cursor-not-allowed');
    }
  }

  window.backToBulkStepConfig = function () {
    document.getElementById('bulkStepEstimate').classList.add('hidden');
    document.getElementById('bulkStepConfig').classList.remove('hidden');
  };

  window.startBulkBroadcast = async function () {
    if (!currentEstimate) return;

    if (!confirm(`क्या आप निश्चित रूप से ${currentEstimate.target_messages} मतदाताओं को व्हाट्सएप डिजिटल पर्ची भेजना चाहते हैं?`)) {
      return;
    }

    const launchBtn = document.getElementById('btnLaunchBulkBroadcast');
    launchBtn.disabled = true;
    launchBtn.innerHTML = '<span>⏳ प्रसारण तैयार हो रहा है...</span>';

    const mode = currentEstimate.mode;
    const wardVal = document.getElementById('bulkWardFilter')?.value;
    const catVal = document.getElementById('bulkCategoryFilter')?.value;
    const skipRecent = document.getElementById('bulkSkipRecentCheck')?.checked ? 48 : 0;

    const filters = {};
    if (wardVal && wardVal !== 'all') filters.ward_nos = [parseInt(wardVal, 10)];
    if (catVal && catVal !== 'all') filters.category = parseInt(catVal, 10);

    try {
      const res = await fetch('/api/dispatch/bulk/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: mode,
          filters: filters,
          skip_recent_hours: skipRecent
        })
      });

      const data = await res.json();
      if (!data.success) {
        window.showToast?.(`प्रसारण प्रारंभ विफल: ${data.error || 'अज्ञात त्रुटि'}`, 'error');
        launchBtn.disabled = false;
        launchBtn.innerHTML = '<span>🚀 प्रसारण तुरंत शुरू करें</span>';
        return;
      }

      currentJobId = data.job_id;
      if (window.triggerHaptic) window.triggerHaptic('success');
      window.showToast?.('✅ थोक पर्ची प्रसारण शुरू हो गया है!', 'success');

      // Transition to Step 3 Monitor
      document.getElementById('bulkStepEstimate').classList.add('hidden');
      document.getElementById('bulkStepMonitor').classList.remove('hidden');

      // Start live polling
      startPollingBulkStatus();
    } catch (err) {
      console.error('Error starting bulk dispatch:', err);
      window.showToast?.('नेटवर्क त्रुटि: प्रसारण शुरू नहीं हो सका', 'error');
      launchBtn.disabled = false;
      launchBtn.innerHTML = '<span>🚀 प्रसारण तुरंत शुरू करें</span>';
    }
  };

  function startPollingBulkStatus() {
    if (statusPollTimer) clearInterval(statusPollTimer);
    fetchJobStatus();
    statusPollTimer = setInterval(fetchJobStatus, 2000);
  }

  async function fetchJobStatus() {
    if (!currentJobId) return;

    try {
      const res = await fetch(`/api/dispatch/bulk/status/${currentJobId}?_t=${Date.now()}`);
      const payload = await res.json();
      if (!payload.success || !payload.data || !payload.data.found) return;

      const job = payload.data;
      updateMonitorUI(job);

      if (['COMPLETED', 'CANCELLED', 'FAILED'].includes(job.status)) {
        clearInterval(statusPollTimer);
        statusPollTimer = null;
        if (job.status === 'COMPLETED') {
          if (window.triggerHaptic) window.triggerHaptic('success');
          window.showToast?.('🎉 सभी मतदाताओं को पर्ची प्रसारण सफलतापूर्वक पूर्ण हुआ!', 'success', 5000);
        }
      }
    } catch (err) {
      console.warn('Status poll error:', err);
    }
  }

  function updateMonitorUI(job) {
    document.getElementById('bulkJobIdBadge').textContent = job.job_id;

    // Progress Bar
    const pct = job.progress_pct || 0;
    document.getElementById('bulkProgressBar').style.width = `${pct}%`;
    document.getElementById('bulkProgressPercent').textContent = `${pct}% पूर्ण`;
    document.getElementById('bulkProgressCounts').textContent = `${(job.sent_count + job.failed_count).toLocaleString('en-IN')} / ${job.total_recipients.toLocaleString('en-IN')}`;

    // Metrics
    document.getElementById('bulkCountSent').textContent = (job.sent_count || 0).toLocaleString('en-IN');
    document.getElementById('bulkCountRendered').textContent = (job.rendered_count || 0).toLocaleString('en-IN');
    document.getElementById('bulkCountFailed').textContent = (job.failed_count || 0).toLocaleString('en-IN');
    const pending = Math.max(0, job.total_recipients - (job.sent_count + job.failed_count));
    document.getElementById('bulkCountPending').textContent = pending.toLocaleString('en-IN');

    // Status Indicator & Buttons
    const statusText = document.getElementById('bulkJobStatusText');
    const pulse = document.getElementById('bulkJobStatusPulse');
    const btnPause = document.getElementById('btnPauseBulkJob');
    const btnResume = document.getElementById('btnResumeBulkJob');
    const alertBox = document.getElementById('bulkMonitorAlert');

    if (job.status === 'RUNNING') {
      statusText.textContent = 'चल रहा है (RUNNING)';
      statusText.className = 'font-black text-xs uppercase tracking-wider text-emerald-300';
      pulse.className = 'w-3 h-3 rounded-full bg-emerald-400 animate-ping';
      btnPause.classList.remove('hidden');
      btnResume.classList.add('hidden');
      alertBox.classList.add('hidden');
    } else if (job.status === 'PAUSED') {
      statusText.textContent = 'रोका गया (PAUSED)';
      statusText.className = 'font-black text-xs uppercase tracking-wider text-amber-300';
      pulse.className = 'w-3 h-3 rounded-full bg-amber-400';
      btnPause.classList.add('hidden');
      btnResume.classList.remove('hidden');
      if (job.error_summary) {
        alertBox.className = 'p-3 rounded-xl border border-amber-300 bg-amber-950/60 text-amber-200 text-xs block';
        alertBox.textContent = `⚠️ रुकावट: ${job.error_summary}`;
      }
    } else if (job.status === 'COMPLETED') {
      statusText.textContent = 'पूर्ण (COMPLETED ✓)';
      statusText.className = 'font-black text-xs uppercase tracking-wider text-teal-300';
      pulse.className = 'w-3 h-3 rounded-full bg-teal-400';
      btnPause.classList.add('hidden');
      btnResume.classList.add('hidden');
      alertBox.classList.add('hidden');
    } else if (job.status === 'CANCELLED') {
      statusText.textContent = 'निरस्त (CANCELLED ✕)';
      statusText.className = 'font-black text-xs uppercase tracking-wider text-rose-300';
      pulse.className = 'w-3 h-3 rounded-full bg-rose-400';
      btnPause.classList.add('hidden');
      btnResume.classList.add('hidden');
      alertBox.classList.add('hidden');
    }

    // Render Recent Items Feed
    const feedList = document.getElementById('bulkLiveFeedList');
    if (job.recent_items && job.recent_items.length > 0) {
      feedList.innerHTML = job.recent_items.map(it => {
        const isSent = it.status === 'SENT';
        const badgeClass = isSent ? 'bg-emerald-100 text-emerald-800' : (it.status === 'FAILED' ? 'bg-rose-100 text-rose-800' : 'bg-slate-100 text-slate-700');
        const icon = isSent ? '✓' : (it.status === 'FAILED' ? '✕' : '⏳');
        return `
          <div class="px-3 py-2 flex items-center justify-between hover:bg-slate-50 transition">
            <div class="flex items-center gap-2">
              <span class="font-mono font-bold text-slate-900">📱 +91 ${it.phone}</span>
              <span class="text-[10px] text-slate-400">(${it.slip_type})</span>
            </div>
            <div class="flex items-center gap-2">
              ${it.error_message ? `<span class="text-[10px] text-rose-600 truncate max-w-[120px]" title="${it.error_message}">${it.error_message}</span>` : ''}
              <span class="px-2 py-0.5 rounded text-[10px] font-bold ${badgeClass}">${icon} ${it.status}</span>
            </div>
          </div>
        `;
      }).join('');
    }
  }

  window.pauseBulkJob = async function () {
    if (!currentJobId) return;
    try {
      const res = await fetch(`/api/dispatch/bulk/pause/${currentJobId}`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        window.showToast?.('⏸️ प्रसारण रोक दिया गया है।', 'info');
        fetchJobStatus();
      } else {
        window.showToast?.(data.error || 'रोकना विफल रहा', 'error');
      }
    } catch (e) {
      console.error(e);
    }
  };

  window.resumeBulkJob = async function () {
    if (!currentJobId) return;
    try {
      const res = await fetch(`/api/dispatch/bulk/resume/${currentJobId}`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        window.showToast?.('▶️ प्रसारण पुनः शुरू हो गया है।', 'success');
        startPollingBulkStatus();
      } else {
        window.showToast?.(data.error || 'पुनः शुरू करना विफल रहा', 'error');
      }
    } catch (e) {
      console.error(e);
    }
  };

  window.cancelBulkJob = async function () {
    if (!currentJobId) return;
    if (!confirm('क्या आप निश्चित रूप से इस प्रसारण को निरस्त करना चाहते हैं? अप्रयुक्त क्रेडिट वापस लौटा दिए जाएंगे।')) return;

    try {
      const res = await fetch(`/api/dispatch/bulk/cancel/${currentJobId}`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        window.showToast?.('🛑 प्रसारण निरस्त कर दिया गया है।', 'warning');
        fetchJobStatus();
      } else {
        window.showToast?.(data.error || 'निरस्त करना विफल रहा', 'error');
      }
    } catch (e) {
      console.error(e);
    }
  };

  async function checkActiveBulkJob() {
    try {
      const res = await fetch(`/api/dispatch/bulk/status?_t=${Date.now()}`);
      const payload = await res.json();
      if (payload.success && payload.data && payload.data.found) {
        const job = payload.data;
        if (['RUNNING', 'PAUSED'].includes(job.status)) {
          currentJobId = job.job_id;
          document.getElementById('bulkStepConfig').classList.add('hidden');
          document.getElementById('bulkStepEstimate').classList.add('hidden');
          document.getElementById('bulkStepMonitor').classList.remove('hidden');
          updateMonitorUI(job);
          if (job.status === 'RUNNING') {
            startPollingBulkStatus();
          }
        }
      }
    } catch (e) {
      console.warn('Active job check error:', e);
    }
  }

  // Auto-check on DOM ready (Admin only)
  document.addEventListener('DOMContentLoaded', function () {
    if (window.CURRENT_USER && !['admin', 'super_admin'].includes(window.CURRENT_USER.role)) {
      return;
    }

    // Check if there is an active job silently
    fetch(`/api/dispatch/bulk/status?_t=${Date.now()}`)
      .then(r => r.json())
      .then(payload => {
        if (payload.success && payload.data && payload.data.found && payload.data.status === 'RUNNING') {
          currentJobId = payload.data.job_id;
          // Display a small floating alert chip
          showFloatingActiveJobIndicator(payload.data);
        }
      })
      .catch(() => {});
  });

  function showFloatingActiveJobIndicator(job) {
    if (document.getElementById('floatingBulkIndicator')) return;
    const chip = document.createElement('div');
    chip.id = 'floatingBulkIndicator';
    chip.className = 'fixed bottom-4 left-4 z-40 bg-slate-900/90 text-white border border-emerald-500 rounded-full px-3.5 py-1.5 shadow-xl flex items-center gap-2 text-xs font-bold backdrop-blur-xs cursor-pointer hover:scale-105 transition';
    chip.innerHTML = `
      <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-ping"></span>
      <span>📢 पर्ची प्रसारण जारी है (${job.progress_pct}%)...</span>
    `;
    chip.onclick = window.openBulkSlipModal;
    document.body.appendChild(chip);
  }

})();
