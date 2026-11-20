/**
 * voter_management_tagger.js - Interactive Voter Profile & Political Sentiment Tagging Engine
 * Unified Multi-Tenant Voter Platform (SmartBooth)
 */

(function () {
  'use strict';

  // Global active state for the tagger modal
  let currentActiveVoterId = null;
  let currentActiveVoterIds = [];
  let currentSelectedCategory = 0;

  /**
   * Disarm background search inputs to completely block browser autofill
   */
  function disarmBackgroundSearchInputs() {
    const nameInput = document.getElementById('nameSearchInput');
    const relInput = document.getElementById('relativeSearchInput');
    if (nameInput) {
      nameInput._preModalValue = nameInput.value;
      nameInput.readOnly = true;
    }
    if (relInput) {
      relInput._preModalValue = relInput.value;
      relInput.readOnly = true;
    }
  }

  /**
   * Restore background search inputs after modal closes
   */
  function restoreBackgroundSearchInputs() {
    const nameInput = document.getElementById('nameSearchInput');
    const relInput = document.getElementById('relativeSearchInput');
    if (nameInput) {
      nameInput.readOnly = false;
      if (nameInput._preModalValue !== undefined && nameInput.value !== nameInput._preModalValue) {
        nameInput.value = nameInput._preModalValue;
      }
    }
    if (relInput) {
      relInput.readOnly = false;
      if (relInput._preModalValue !== undefined && relInput.value !== relInput._preModalValue) {
        relInput.value = relInput._preModalValue;
      }
    }
  }

  /**
   * Helper: extract clean 10-digit phone number from voter object
   */
  window.extractCleanVoterPhone = function (voter) {
    if (!voter) return '';
    let raw = voter.contact_phone || voter.phone || voter.mobile || voter.alt_mobile || '';
    if (typeof raw !== 'string') raw = String(raw || '');
    let ph = raw.split(',')[0].trim().replace(/\D/g, '');
    if (ph.length === 12 && ph.startsWith('91')) ph = ph.slice(2);
    if (ph.length === 11 && ph.startsWith('0')) ph = ph.slice(1);
    return ph.length === 10 ? ph : '';
  };
  const extractCleanPhone = window.extractCleanVoterPhone;

  /**
   * Open the unified voter profile tagging modal for a single voter
   * @param {number|string} voterId
   */
  window.openVoterProfileModal = async function (voterId) {
    if (window.IS_VIEWER || (window.CURRENT_USER && (window.CURRENT_USER.role === 'viewer' || window.CURRENT_USER.isViewer))) {
      if (window.showToast) window.showToast('दर्शक (Viewer) केवल देखने के लिए अधिकृत है (Viewer role is read-only)', 'info');
      return;
    }
    if (!voterId) return;
    disarmBackgroundSearchInputs();
    currentActiveVoterId = voterId;
    currentActiveVoterIds = [voterId];

    const modal = document.getElementById('voterProfileModal');
    if (!modal) {
      console.warn("voterProfileModal element not found in DOM");
      return;
    }

    if (typeof window.restoreLocalCasteChips === 'function') {
      window.restoreLocalCasteChips();
    }

    if (window.triggerHaptic) window.triggerHaptic('light');

    // Reset bulk UI elements to single-voter mode
    const batchContainer = document.getElementById('vpmSelectedVotersContainer');
    if (batchContainer) batchContainer.classList.add('hidden');
    const saveBtn = document.getElementById('btnSaveVoterProfile');
    if (saveBtn) saveBtn.innerHTML = `<span>💾</span><span>सुरक्षित करें (Save)</span>`;

    // 1. Locate voter from APP_STATE.currentResults (most recent), APP_STATE.selectedVoters or fetch from server
    let voter = null;
    if (window.APP_STATE && Array.isArray(window.APP_STATE.currentResults)) {
      voter = window.APP_STATE.currentResults.find(v => String(v.id || v.voter_id) === String(voterId));
    }
    if (!voter && window.APP_STATE && window.APP_STATE.selectedVoters) {
      voter = window.APP_STATE.selectedVoters.get(voterId) || 
              window.APP_STATE.selectedVoters.get(Number(voterId)) || 
              window.APP_STATE.selectedVoters.get(String(voterId));
    }

    // Populate hidden ID immediately
    const idInput = document.getElementById('vpmVoterId');
    if (idInput) idInput.value = voterId;

    if (voter) {
      populateModalFields(voter);
    } else {
      // Show loading placeholder and fetch single voter
      setModalLoading(true);
      try {
        const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
        const res = await fetch(`/api/voter/${voterId}?tenant=${encodeURIComponent(curTenant)}`);
        if (res.ok) {
          const data = await res.json();
          voter = data.voter || data;
          populateModalFields(voter);
        } else {
          populateModalFields({ id: voterId, name_hi: `मतदाता #${voterId}` });
        }
      } catch (err) {
        console.error("Error fetching voter details:", err);
        populateModalFields({ id: voterId, name_hi: `मतदाता #${voterId}` });
      } finally {
        setModalLoading(false);
      }
    }

    // Open modal
    modal.classList.remove('hidden');
    modal.classList.add('flex');
  };

  /**
   * Open the unified modal in bulk mode for multiple selected voters
   * Sets the same mobile number, caste tag, karyakarta, and sentiment for all of them
   * @param {Array<number|string>} voterIds
   */
  window.openBulkProfileModal = function (voterIds, passedVoters = null) {
    if (window.IS_VIEWER || (window.CURRENT_USER && (window.CURRENT_USER.role === 'viewer' || window.CURRENT_USER.isViewer))) {
      if (window.showToast) window.showToast('दर्शक (Viewer) केवल देखने के लिए अधिकृत है (Viewer role is read-only)', 'info');
      return;
    }
    if (!Array.isArray(voterIds) || voterIds.length === 0) return;
    if (voterIds.length === 1) {
      return window.openVoterProfileModal(voterIds[0]);
    }

    disarmBackgroundSearchInputs();
    currentActiveVoterId = null;
    currentActiveVoterIds = voterIds;

    const modal = document.getElementById('voterProfileModal');
    if (!modal) return;

    if (window.triggerHaptic) window.triggerHaptic('light');

    // Gather voter objects (prefer passedVoters, then APP_STATE.currentResults, then selectedVoters)
    let passedMap = null;
    if (Array.isArray(passedVoters)) {
      passedMap = new Map();
      passedVoters.forEach(v => {
        if (v) passedMap.set(String(v.id || v.voter_id), v);
      });
    } else if (passedVoters instanceof Map) {
      passedMap = passedVoters;
    }

    const voters = voterIds.map(vid => {
      let v = passedMap ? passedMap.get(String(vid)) : null;
      if (!v && window.APP_STATE && Array.isArray(window.APP_STATE.currentResults)) {
        v = window.APP_STATE.currentResults.find(x => String(x.id || x.voter_id) === String(vid));
      }
      if (!v && window.APP_STATE && window.APP_STATE.selectedVoters) {
        v = window.APP_STATE.selectedVoters.get(vid) || 
            window.APP_STATE.selectedVoters.get(Number(vid)) || 
            window.APP_STATE.selectedVoters.get(String(vid));
      }
      return v || { id: vid, name_hi: `मतदाता #${vid}` };
    });

    // Populate header
    const nameEl = document.getElementById('vpmVoterName');
    const subEl = document.getElementById('vpmVoterSub');
    if (nameEl) {
      nameEl.innerHTML = `<span class="bg-indigo-600 text-white font-black text-xs px-2.5 py-0.5 rounded-full mr-1.5 shadow-xs">👥 ${voters.length} मतदाता</span> सामूहिक संपादन (Bulk Edit)`;
    }
    if (subEl) {
      const summaryNames = voters.slice(0, 4).map(v => v.name_hi || v.name_en || `#${v.id}`).join(' · ');
      const extra = voters.length > 4 ? ` +${voters.length - 4} अन्य` : '';
      subEl.textContent = `${summaryNames}${extra}`;
    }

    // Track Mukhiya selection in bulk edit
    let currentVotersList = voters;
    window._vpmCurrentVoters = voters;

    // Pick default Mukhiya: existing family head or highest age
    const existingHead = voters.find(v => Boolean(v.is_family_head || v.is_mukhiya));
    let defaultMukhiyaId = existingHead ? (existingHead.id || existingHead.voter_id) : null;
    if (!defaultMukhiyaId && voters.length > 0) {
      const sortedByAge = [...voters].sort((a, b) => {
        const ageA = parseInt(a.age, 10) || 0;
        const ageB = parseInt(b.age, 10) || 0;
        if (ageB !== ageA) return ageB - ageA;
        return (parseInt(a.sl_no || a.serial_no, 10) || 0) - (parseInt(b.sl_no || b.serial_no, 10) || 0);
      });
      defaultMukhiyaId = sortedByAge[0].id || sortedByAge[0].voter_id;
    }

    const mukhiyaHiddenInput = document.getElementById('vpmSelectedMukhiyaId');
    if (mukhiyaHiddenInput) {
      mukhiyaHiddenInput.value = defaultMukhiyaId ? String(defaultMukhiyaId) : '';
    }

    const consolidateCb = document.getElementById('vpmConsolidateFamily');
    if (consolidateCb) {
      consolidateCb.checked = true;
    }

    // Render chips with Mukhiya indicators and click handlers
    window.renderVpmChipsWithMukhiya(defaultMukhiyaId);

    // Pre-fill phone if any selected voter already has one
    const phoneInput = document.getElementById('vpmPhone');
    if (phoneInput) {
      const existingPhone = voters.map(v => extractCleanPhone(v)).find(Boolean) || '';
      phoneInput.value = existingPhone;
    }

    // Pre-fill Caste Tag
    const casteInput = document.getElementById('vpmCaste');
    if (casteInput) {
      const existingWithCaste = voters.find(v => v.caste_tag && v.caste_tag.trim());
      casteInput.value = existingWithCaste ? existingWithCaste.caste_tag : '';
    }

    // Pre-fill Assigned Karyakarta
    const karyaSelect = document.getElementById('vpmAssignedKaryakarta');
    if (karyaSelect) {
      const existingWithKarya = voters.find(v => v.assigned_karyakarta_id);
      loadVpmKaryakartas(existingWithKarya ? existingWithKarya.assigned_karyakarta_id : '');
    }

    // Pre-fill Outside status
    const outsideCb = document.getElementById('vpmIsOutside');
    if (outsideCb) {
      outsideCb.checked = voters.some(v => Boolean(v.is_outside));
    }

    // Pre-fill Deceased status
    const deceasedCb = document.getElementById('vpmIsDeceased');
    if (deceasedCb) {
      deceasedCb.checked = voters.some(v => v.survey_status === 'DECEASED');
    }

    // Pre-fill Family Head Name
    const famHeadInput = document.getElementById('vpmFamilyHeadName');
    if (famHeadInput) {
      const existingFam = voters.find(v => v.family_head_name && v.family_head_name.trim());
      famHeadInput.value = existingFam ? existingFam.family_head_name : '';
    }

    // Pre-fill Notes
    const notesInput = document.getElementById('vpmNotes');
    if (notesInput) {
      const existingWithNotes = voters.find(v => v.notes && v.notes.trim());
      notesInput.value = existingWithNotes ? existingWithNotes.notes : '';
    }

    // Sentiment Category
    const existingCat = voters.find(v => v.voter_category !== undefined && v.voter_category !== null && v.voter_category !== 0);
    window.setVpmSentiment(existingCat ? existingCat.voter_category : 0);

    // Save button label (short English)
    const saveBtn = document.getElementById('btnSaveVoterProfile');
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.innerHTML = `<span>💾</span><span>Save for All ${voters.length}</span>`;
    }

    // Update compact voter panel summary text
    const summaryText = document.getElementById('vpmPanelSummaryText');
    if (summaryText && voters.length > 0) {
      const first = voters[0].name_hi || voters[0].name_en || `#${voters[0].id}`;
      const rest = voters.length - 1;
      summaryText.textContent = rest > 0 ? `${first} + ${rest} other${rest > 1 ? 's' : ''}` : first;
    }

    // Auto-open Special Status accordion when flags are already set
    setTimeout(() => { if (typeof vpmOpenSpecialIfSet === 'function') vpmOpenSpecialIfSet(); }, 50);

    // Open modal
    modal.classList.remove('hidden');
    modal.classList.add('flex');
  };

  /**
   * Render voter chips in the bulk edit modal with active Mukhiya styling
   */
  window.renderVpmChipsWithMukhiya = function (selectedMukhiyaId) {
    const batchContainer = document.getElementById('vpmSelectedVotersContainer');
    const batchCount = document.getElementById('vpmSelectedBatchCount');
    const chipsList = document.getElementById('vpmSelectedChipsList');
    const badgeEl = document.getElementById('vpmCurrentMukhiyaBadge');
    const consolidateCb = document.getElementById('vpmConsolidateFamily');
    const isConsolidating = consolidateCb ? consolidateCb.checked : true;

    const voters = window._vpmCurrentVoters || [];
    if (batchContainer) batchContainer.classList.remove('hidden');
    if (batchCount) batchCount.textContent = `${voters.length} मतदाता`;

    if (chipsList) {
      const isPanchayat = typeof window.isPanchayatCampaign === 'function' ? window.isPanchayatCampaign() : false;
      const targetIdStr = selectedMukhiyaId ? String(selectedMukhiyaId) : '';

      chipsList.innerHTML = voters.map(v => {
        const vid = String(v.id || v.voter_id);
        const isMukhiya = isConsolidating && (vid === targetIdStr);
        const vName = v.name_hi || v.name_en || `ID #${v.id}`;
        const wardStr = (isPanchayat && v.ward_no && parseInt(v.ward_no, 10) > 0) ? `W.${v.ward_no} ` : '';
        const partSl = (v.part_no && (v.sl_no || v.serial_no)) ? ` (${wardStr}${v.part_no}/${v.sl_no || v.serial_no})` : '';
        const ageStr = v.age ? ` · ${v.age} वर्ष` : '';
        const hasPhone = v.contact_phone ? ` 📱` : '';

        if (isMukhiya) {
          return `<button type="button" onclick="window.selectVpmMukhiya('${vid}')" class="inline-flex items-center gap-1.5 bg-amber-500 text-white border-2 border-amber-600 font-black px-2.5 py-1 rounded-lg text-[11px] shadow-sm ring-2 ring-amber-300/60 scale-[1.02] transition-all cursor-pointer select-none" title="वर्तमान में परिवार का मुखिया चयनित">
            <span class="text-xs">👑</span>
            <span>${vName}${partSl}${ageStr}</span>
            <span class="bg-amber-700/90 text-amber-100 text-[9px] px-1.5 py-0.2 rounded font-black tracking-wide ml-0.5">मुखिया</span>
            ${hasPhone ? `<span title="${v.contact_phone}" class="text-[10px] text-white">✓📱</span>` : ''}
          </button>`;
        } else {
          return `<button type="button" onclick="window.selectVpmMukhiya('${vid}')" class="inline-flex items-center gap-1 bg-white hover:bg-amber-50 hover:border-amber-400 border border-indigo-200 text-indigo-950 hover:text-amber-950 font-bold px-2 py-0.5 rounded-md text-[11px] shadow-2xs transition-all cursor-pointer select-none" title="इन्हें परिवार का मुखिया (Head) बनाने के लिए क्लिक करें">
            <span>${vName}${partSl}${ageStr}</span>${hasPhone ? `<span title="${v.contact_phone}" class="text-[10px] text-emerald-600">✓📱</span>` : ''}
          </button>`;
        }
      }).join('');
    }

    if (badgeEl) {
      if (isConsolidating && selectedMukhiyaId) {
        const found = voters.find(v => String(v.id || v.voter_id) === String(selectedMukhiyaId));
        if (found) {
          badgeEl.textContent = `👑 मुखिया: ${found.name_hi || found.name_en || '#' + selectedMukhiyaId}`;
          badgeEl.classList.remove('hidden');
        } else {
          badgeEl.classList.add('hidden');
        }
      } else {
        badgeEl.classList.add('hidden');
      }
    }
  };

  /**
   * Select a specific voter as the Mukhiya during bulk edit
   */
  window.selectVpmMukhiya = function (voterId) {
    const consolidateCb = document.getElementById('vpmConsolidateFamily');
    if (consolidateCb && !consolidateCb.checked) {
      consolidateCb.checked = true;
    }
    const mukhiyaHiddenInput = document.getElementById('vpmSelectedMukhiyaId');
    if (mukhiyaHiddenInput) {
      mukhiyaHiddenInput.value = String(voterId);
    }
    if (window.triggerHaptic) window.triggerHaptic('selection');
    window.renderVpmChipsWithMukhiya(voterId);
  };

  /**
   * Toggle consolidate family checkbox listener
   */
  window.toggleVpmConsolidateFamily = function (isChecked) {
    const hintEl = document.getElementById('vpmMukhiyaHint');
    const mukhiyaHiddenInput = document.getElementById('vpmSelectedMukhiyaId');
    if (hintEl) {
      if (isChecked) {
        hintEl.classList.remove('opacity-40', 'pointer-events-none');
      } else {
        hintEl.classList.add('opacity-40', 'pointer-events-none');
      }
    }
    const curMukhiyaId = mukhiyaHiddenInput ? mukhiyaHiddenInput.value : null;
    window.renderVpmChipsWithMukhiya(curMukhiyaId);
  };

  /**
   * Close the voter profile modal
   */
  window.closeVoterProfileModal = function () {
    restoreBackgroundSearchInputs();
    if (typeof window.vpmToggleNewKaryakartaForm === 'function') {
      window.vpmToggleNewKaryakartaForm(false);
    }
    const modal = document.getElementById('voterProfileModal');
    if (modal) {
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }
    currentActiveVoterId = null;
    currentActiveVoterIds = [];
    window._vpmCurrentVoters = [];
  };

  /**
   * Open profile / edit modal for the currently selected voter(s) (from Floating Action Bar)
   * Automatically detects single vs multi-voter selection
   */
  window.openProfileForSelected = function (voterId) {
    if (voterId) {
      return window.openVoterProfileModal(voterId);
    }
    const selected = window.APP_STATE && window.APP_STATE.selectedVoters;
    if (!selected || selected.size === 0) {
      if (window.showToast) window.showToast('कृपया पहले किसी मतदाता को चुनें (Select a voter first)', 'warning');
      return;
    }
    const ids = Array.from(selected.keys());
    if (ids.length === 1) {
      return window.openVoterProfileModal(ids[0]);
    } else {
      return window.openBulkProfileModal(ids);
    }
  };


  /**
   * Select a sentiment category in the modal
   * @param {number} catVal - 100 (पक्का), 50 (50-50), -1 (विरोधी), 0 (सामान्य)
   */
  window.setVpmSentiment = function (catVal) {
    currentSelectedCategory = parseInt(catVal, 10);
    const modal = document.getElementById('voterProfileModal');
    if (modal) {
      modal.dataset.selectedCat = currentSelectedCategory;
    }

    if (window.triggerHaptic) window.triggerHaptic('selection');

    // Update button visual styles (supports both old pill and new hero layout)
    const buttons = document.querySelectorAll('.vpm-sentiment-btn');
    buttons.forEach(btn => {
      const btnCat = parseInt(btn.dataset.cat, 10);
      btn.classList.remove(
        'ring-2', 'ring-offset-1',
        'bg-emerald-600', 'border-emerald-600', 'border-emerald-400', 'ring-emerald-400', 'text-emerald-50',
        'bg-amber-500',  'border-amber-500',  'border-amber-400',  'ring-amber-400',  'text-amber-50',
        'bg-rose-600',   'border-rose-600',   'border-rose-400',   'ring-rose-400',   'text-rose-50',
        'bg-slate-700',  'border-slate-700',  'border-slate-400',  'ring-slate-400',
        'text-white', 'text-slate-950', 'shadow-xs'
      );
      btn.classList.add('bg-slate-50', 'text-slate-500', 'border-slate-200');

      if (btnCat === currentSelectedCategory) {
        btn.classList.remove('bg-slate-50', 'text-slate-500', 'border-slate-200');
        if (btnCat === 100) {
          btn.classList.add('bg-emerald-600', 'text-white', 'border-emerald-600', 'ring-2', 'ring-offset-1', 'ring-emerald-400', 'shadow-xs');
        } else if (btnCat === 50) {
          btn.classList.add('bg-amber-500', 'text-slate-950', 'border-amber-500', 'ring-2', 'ring-offset-1', 'ring-amber-400', 'shadow-xs');
        } else if (btnCat === -1) {
          btn.classList.add('bg-rose-600', 'text-white', 'border-rose-600', 'ring-2', 'ring-offset-1', 'ring-rose-400', 'shadow-xs');
        } else {
          btn.classList.add('bg-slate-700', 'text-white', 'border-slate-700', 'ring-2', 'ring-offset-1', 'ring-slate-400', 'shadow-xs');
        }
      }
    });
  };

  /**
   * Quick chip selection for caste tag
   */
  window.selectVpmCasteChip = function (caste) {
    const input = document.getElementById('vpmCaste');
    if (input) {
      input.value = caste;
      input.focus();
    }
    if (window.triggerHaptic) window.triggerHaptic('light');
  };

  /**
   * Dynamically ensure a caste chip exists in the DOM container and local storage
   */
  window.ensureVpmCasteChip = function (caste) {
    if (!caste || typeof caste !== 'string') return;
    const cleanTag = caste.trim();
    if (!cleanTag) return;

    const containers = document.querySelectorAll('#vpmCasteChips');
    containers.forEach(container => {
      const existing = Array.from(container.querySelectorAll('button')).find(btn => {
        const txt = btn.textContent.trim().toLowerCase();
        return txt === cleanTag.toLowerCase() ||
               txt.startsWith(cleanTag.toLowerCase() + ' ') ||
               (btn.getAttribute('onclick') || '').includes(`'${cleanTag}'`);
      });
      if (!existing) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.onclick = () => window.selectVpmCasteChip(cleanTag);
        btn.className = 'px-2 py-0.5 rounded-full bg-purple-100 hover:bg-purple-200 text-purple-900 font-bold text-[10px] border border-purple-300 cursor-pointer animate-in fade-in';
        btn.textContent = cleanTag;
        container.prepend(btn);
      }
    });

    try {
      const tenant = window.CURRENT_TENANT || 'default';
      const storageKey = 'custom_caste_chips_' + tenant;
      let saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
      if (!saved.some(t => t.toLowerCase() === cleanTag.toLowerCase())) {
        saved.unshift(cleanTag);
        if (saved.length > 10) saved = saved.slice(0, 10);
        localStorage.setItem(storageKey, JSON.stringify(saved));
      }
    } catch (e) {}
  };

  /**
   * Restore locally saved custom chips for this tenant
   */
  window.restoreLocalCasteChips = function () {
    try {
      const tenant = window.CURRENT_TENANT || 'default';
      const storageKey = 'custom_caste_chips_' + tenant;
      const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
      if (Array.isArray(saved)) {
        saved.forEach(tag => window.ensureVpmCasteChip(tag));
      }
    } catch (e) {}
  };

  /**
   * Save voter profile details to backend with optimistic UI updates
   */
  window.saveVoterProfileDetails = async function () {
    const isBulk = Array.isArray(currentActiveVoterIds) && currentActiveVoterIds.length > 1;
    let targetVoterIds = isBulk ? currentActiveVoterIds : [];
    if (!isBulk) {
      const singleId = currentActiveVoterId || document.getElementById('vpmVoterId')?.value;
      if (singleId) targetVoterIds = [singleId];
    }

    if (targetVoterIds.length === 0) {
      window.showToast?.('त्रुटि: कोई मतदाता पहचान संख्या उपलब्ध नहीं है', 'error');
      return;
    }

    const phoneInput = document.getElementById('vpmPhone');
    const casteInput = document.getElementById('vpmCaste');
    const outsideInput = document.getElementById('vpmIsOutside');
    const deceasedInput = document.getElementById('vpmIsDeceased');
    const famHeadInput = document.getElementById('vpmFamilyHeadName');
    const notesInput = document.getElementById('vpmNotes');
    const saveBtn = document.getElementById('btnSaveVoterProfile');

    let phone = (phoneInput?.value || '').trim();
    const caste = (casteInput?.value || '').trim();
    const isOutside = Boolean(outsideInput?.checked);
    const isDeceased = Boolean(deceasedInput?.checked);
    const surveyStatus = isDeceased ? 'DECEASED' : null;
    const familyHeadName = (famHeadInput?.value || '').trim();
    const notes = (notesInput?.value || '').trim();
    const category = currentSelectedCategory;

    // Validate phone number if entered
    if (phone) {
      phone = phone.replace(/[^0-9]/g, '');
      if (phone.length === 12 && phone.startsWith('91')) phone = phone.slice(2);
      if (phone.length === 11 && phone.startsWith('0')) phone = phone.slice(1);
      if (phone.length !== 10) {
        window.showToast?.('कृपया मान्य 10-अंकीय मोबाइल नंबर दर्ज करें (उदा: 9829012345)', 'warning');
        phoneInput?.focus();
        return;
      }
    }

    const karyaSelect = document.getElementById('vpmAssignedKaryakarta');
    const karyaId = karyaSelect ? (karyaSelect.value ? parseInt(karyaSelect.value, 10) : null) : null;
    const karyaName = karyaSelect && karyaSelect.selectedIndex > 0 ? karyaSelect.options[karyaSelect.selectedIndex].text.split('(')[0].trim() : null;

    // Save button loading state
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.innerHTML = `<span>⏳</span><span>सुरक्षित हो रहा है...</span>`;
    }

    const payload = {
      contact_phone: phone,
      caste_tag: caste,
      voter_category: category,
      is_outside: isOutside,
      survey_status: surveyStatus,
      assigned_karyakarta_id: karyaId,
      family_head_name: familyHeadName,
      notes: notes
    };

    let phoneTargetMode = 'missing_only';
    if (isBulk) {
      payload.phone_target_mode = phoneTargetMode;
      payload.voter_ids = targetVoterIds.map(Number);
      payload.consolidate_family = Boolean(document.getElementById('vpmConsolidateFamily')?.checked);
      if (payload.consolidate_family) {
        const mVal = document.getElementById('vpmSelectedMukhiyaId')?.value;
        if (mVal && !isNaN(parseInt(mVal, 10))) {
          payload.mukhiya_voter_id = parseInt(mVal, 10);
        }
      }
      if (!phone) {
        delete payload.contact_phone;
      }
    } else {
      payload.voter_id = parseInt(targetVoterIds[0], 10);
    }

    const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';

    try {
      // 1. Send update request
      let res = await fetch(`/api/voter/profile/update?tenant=${encodeURIComponent(curTenant)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || 'Server responded with error');
      }

      if (caste && typeof window.ensureVpmCasteChip === 'function') {
        window.ensureVpmCasteChip(caste);
      }

      // Helper function to check if this specific voter should have their phone updated
      const shouldUpdatePhoneForVoter = (v) => {
        if (!phone) {
          return !isBulk; // If clearing phone in single mode
        }
        if (!isBulk) return true;
        if (phoneTargetMode === 'all') return true;
        if (phoneTargetMode === 'mukhiya_only') {
          const mukhiyaId = payload.mukhiya_voter_id || (window._vpmCurrentVoters && window._vpmCurrentVoters[0]?.id);
          return String(v.id || v.voter_id) === String(mukhiyaId);
        }
        if (phoneTargetMode === 'missing_only') {
          // Only update if currently has no clean phone
          const curPh = extractCleanPhone(v);
          return !curPh;
        }
        return false;
      };

      // 2. Optimistic local state update in APP_STATE.currentResults
      if (window.APP_STATE && Array.isArray(window.APP_STATE.currentResults)) {
        targetVoterIds.forEach(vid => {
          const idx = window.APP_STATE.currentResults.findIndex(v => String(v.id || v.voter_id) === String(vid));
          if (idx !== -1) {
            const v = window.APP_STATE.currentResults[idx];
            if (shouldUpdatePhoneForVoter(v)) {
              v.contact_phone = phone;
            }
            v.caste_tag = caste;
            v.voter_category = category;
            v.is_outside = isOutside;
            v.survey_status = surveyStatus;
            if (karyaId !== null) {
              v.assigned_karyakarta_id = karyaId;
              v.assigned_karyakarta_name = karyaName;
            }
            if (familyHeadName || !isBulk) v.family_head_name = familyHeadName;
            v.notes = notes;
          }
        });
      }

      // 3. Optimistic local state update in APP_STATE.selectedVoters
      if (window.APP_STATE && window.APP_STATE.selectedVoters) {
        targetVoterIds.forEach(vid => {
          const v = window.APP_STATE.selectedVoters.get(vid) || 
                    window.APP_STATE.selectedVoters.get(Number(vid)) || 
                    window.APP_STATE.selectedVoters.get(String(vid));
          if (v) {
            if (shouldUpdatePhoneForVoter(v)) {
              v.contact_phone = phone;
            }
            v.caste_tag = caste;
            v.voter_category = category;
            v.is_outside = isOutside;
            v.survey_status = surveyStatus;
            if (karyaId !== null) {
              v.assigned_karyakarta_id = karyaId;
              v.assigned_karyakarta_name = karyaName;
            }
            if (familyHeadName || !isBulk) v.family_head_name = familyHeadName;
            v.notes = notes;
          }
        });
      }

      // 4. Update voter_cards.js currentResults closure and patch search cache
      targetVoterIds.forEach(vid => {
        let vObj = window.APP_STATE?.currentResults?.find(x => String(x.id || x.voter_id) === String(vid));
        if (!vObj && window.APP_STATE?.selectedVoters) {
          vObj = window.APP_STATE.selectedVoters.get(vid) || window.APP_STATE.selectedVoters.get(Number(vid));
        }
        const indPatch = {
          caste_tag: caste,
          voter_category: category,
          is_outside: isOutside ? 1 : 0,
          survey_status: surveyStatus,
          notes: notes
        };
        if (vObj && shouldUpdatePhoneForVoter(vObj)) {
          indPatch.contact_phone = phone;
        }
        if (karyaId !== null) {
          indPatch.assigned_karyakarta_id = karyaId;
          indPatch.assigned_karyakarta_name = karyaName;
        }
        if (familyHeadName || !isBulk) indPatch.family_head_name = familyHeadName;

        if (typeof window.patchVoterInCache === 'function') {
          window.patchVoterInCache(Number(vid), indPatch);
        }
        if (typeof window.updateVoterProfileInResults === 'function') {
          window.updateVoterProfileInResults([vid], indPatch);
        }
      });

      // Invalidate omnisearch cache so subsequent queries/pages also reflect new caste/phone
      if (typeof window.invalidateSearchCache === 'function') {
        window.invalidateSearchCache();
      }

      if (typeof window.renderVoterCards === 'function' && Array.isArray(window.APP_STATE?.currentResults)) {
        window.renderVoterCards(window.APP_STATE.currentResults);
      } else if (typeof window.renderCurrentResults === 'function') {
        window.renderCurrentResults();
      }

      if (typeof window.loadFamilies === 'function') {
        window.loadFamilies();
      }
      if (typeof window.onVoterProfileSaved === 'function') {
        window.onVoterProfileSaved(targetVoterIds, payload);
      }

      if (window.triggerHaptic) window.triggerHaptic('success');
      if (isBulk) {
        window.showToast?.(`✅ सभी ${targetVoterIds.length} मतदाताओं का विवरण सुरक्षित किया गया!`, 'success');
      } else {
        window.showToast?.('✅ मतदाता प्रोफाइल व विवरण सुरक्षित कर दिया गया!', 'success');
      }

      window.closeVoterProfileModal();

    } catch (err) {
      console.error("Error saving voter profile:", err);
      if (window.triggerHaptic) window.triggerHaptic('warning');
      window.showToast?.(`❌ सुरक्षित करने में त्रुटि: ${err.message}`, 'error');
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.innerHTML = `<span>💾</span><span>सुरक्षित करें (Save)</span>`;
      }
    }
  };

  /**
   * Bulk tag political sentiment for all currently selected voters
   * @param {number|string} catVal
   */
  window.bulkTagSelectedSentiment = async function (catVal) {
    if (!window.APP_STATE || !window.APP_STATE.selectedVoters || window.APP_STATE.selectedVoters.size === 0) {
      window.showToast?.('कृपया पहले मतदाता चुनें (Please select voters first)', 'warning');
      return;
    }

    const voterIds = Array.from(window.APP_STATE.selectedVoters.keys());
    const numericCat = parseInt(catVal, 10);
    const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';

    if (window.triggerHaptic) window.triggerHaptic('medium');

    try {
      const res = await fetch(`/api/voter/bulk_category?tenant=${encodeURIComponent(curTenant)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voter_ids: voterIds,
          category: numericCat
        })
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false) {
        throw new Error(data.error || 'Failed to update category');
      }

      // Optimistically update all selected in APP_STATE.currentResults
      if (window.APP_STATE && Array.isArray(window.APP_STATE.currentResults)) {
        window.APP_STATE.currentResults.forEach(v => {
          const vid = v.id || v.voter_id;
          if (window.APP_STATE.selectedVoters.has(vid)) {
            v.voter_category = numericCat;
          }
        });
      }

      // Re-render UI
      if (typeof window.renderCurrentResults === 'function') {
        window.renderCurrentResults();
      }

      const catLabels = {
        100: '🟢 100% पक्का',
        50: '🟡 50-50',
        '-1': '🔴 विरोधी',
        0: '⚪ सामान्य'
      };

      if (window.triggerHaptic) window.triggerHaptic('success');
      window.showToast?.(`✅ ${voterIds.length} मतदाताओं को '${catLabels[numericCat] || numericCat}' टैग दिया गया!`, 'success');

    } catch (err) {
      console.error("Bulk categorization error:", err);
      if (window.triggerHaptic) window.triggerHaptic('warning');
      window.showToast?.(`❌ टैग करने में त्रुटि: ${err.message}`, 'error');
    }
  };

  /**
   * Compatibility alias for bulk categorization from bottom bars
   */
  window.bulkSetSelectedCategory = function (catStr) {
    let numeric = 0;
    if (catStr === '100') numeric = 100;
    else if (catStr === '50_50' || catStr === '50') numeric = 50;
    else if (catStr === 'opposition' || catStr === '-1') numeric = -1;
    else if (catStr === 'clear' || catStr === '0') numeric = 0;
    window.bulkTagSelectedSentiment(numeric);
  };

  /**
   * Helper: Populate modal UI fields with voter data
   */
  function populateModalFields(voter) {
    window._vpmCurrentVoters = [voter];
    const nameEl = document.getElementById('vpmVoterName');
    const subEl = document.getElementById('vpmVoterSub');
    const phoneInput = document.getElementById('vpmPhone');
    const casteInput = document.getElementById('vpmCaste');
    const outsideCb = document.getElementById('vpmIsOutside');
    const deceasedCb = document.getElementById('vpmIsDeceased');
    const famHeadInput = document.getElementById('vpmFamilyHeadName');
    const notesInput = document.getElementById('vpmNotes');

    const nameHi = voter.name_hi || '';
    const nameEn = voter.name_en || '';
    const nameDisplay = nameHi ? `${nameHi} ${nameEn ? '(' + nameEn + ')' : ''}` : (nameEn || 'मतदाता प्रोफाइल');

    if (nameEl) nameEl.textContent = nameDisplay;
    if (subEl) {
      const isPanchayat = typeof window.isPanchayatCampaign === 'function' ? window.isPanchayatCampaign() : false;
      const wardPrefix = (isPanchayat && voter.ward_no && parseInt(voter.ward_no, 10) > 0) ? `वार्ड ${voter.ward_no} · ` : '';
      const part = voter.part_no || '-';
      const sl = voter.sl_no || voter.serial_no || '-';
      const epic = voter.epic_no || '-';
      const house = voter.house_no && voter.house_no !== '0' ? ` · मकान: ${voter.house_no}` : '';
      subEl.textContent = `${wardPrefix}भाग ${part} · क्र. ${sl} · पहचान पत्र: ${epic}${house}`;
    }

    let ph = extractCleanPhone(voter);
    if (phoneInput) {
      phoneInput.value = ph;
    }

    if (casteInput) casteInput.value = voter.caste_tag || '';
    if (outsideCb) outsideCb.checked = Boolean(voter.is_outside);
    if (deceasedCb) deceasedCb.checked = (voter.survey_status === 'DECEASED');
    if (famHeadInput) famHeadInput.value = voter.family_head_name || '';
    if (notesInput) notesInput.value = voter.notes || voter.calling_tag || '';

    // Auto-open Special Status accordion if voter is flagged
    setTimeout(() => { if (typeof vpmOpenSpecialIfSet === 'function') vpmOpenSpecialIfSet(); }, 50);

    // Populate Karyakarta Select Dropdown
    const karyaSel = document.getElementById('vpmAssignedKaryakarta');
    if (karyaSel) {
      loadVpmKaryakartas(voter.assigned_karyakarta_id);
    }

    // Set sentiment category button states
    let cat = voter.voter_category;
    if (cat === '50_50') cat = 50;
    else if (cat === 'opposition') cat = -1;
    const numericCat = parseInt(cat, 10) || 0;
    window.setVpmSentiment(numericCat);
  }


  let cachedVpmKaryakartas = null;
  let cachedVpmTenant = null;

  window.invalidateVpmKaryakartasCache = function () {
    cachedVpmKaryakartas = null;
    cachedVpmTenant = null;
  };

  function populateVpmKaryakartaSelect(selectedId) {
    const sel = document.getElementById('vpmAssignedKaryakarta');
    if (!sel) return;
    const items = cachedVpmKaryakartas || [];
    let html = '<option value="">-- कोई कार्यकर्ता नहीं (Unassigned) --</option>';
    html += '<option value="__NEW__" class="font-bold text-indigo-700 bg-indigo-50">➕ नया कार्यकर्ता जोड़ें... (+ Add New)</option>';
    html += items.map(k => `<option value="${k.id}" ${String(k.id) === String(selectedId) ? 'selected' : ''}>${k.name} (बूथ ${k.booth_no || '-'})</option>`).join('');
    sel.innerHTML = html;
    if (selectedId && selectedId !== '__NEW__') sel.value = String(selectedId);
  }

  async function loadVpmKaryakartas(selectedId) {
    const sel = document.getElementById('vpmAssignedKaryakarta');
    if (!sel) return;
    const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';

    if (!cachedVpmKaryakartas || cachedVpmTenant !== curTenant) {
      sel.innerHTML = '<option value="">कार्यकर्ता लोड हो रहे हैं... (Loading workers...)</option>';
      try {
        const res = await fetch(`/api/karyakartas/options?tenant=${encodeURIComponent(curTenant)}`);
        const data = await res.json();
        if (data && data.success && Array.isArray(data.karyakartas)) {
          cachedVpmKaryakartas = data.karyakartas;
          cachedVpmTenant = curTenant;
        } else {
          const fbRes = await fetch(`/api/karyakartas?tenant=${encodeURIComponent(curTenant)}`);
          const fbData = await fbRes.json();
          cachedVpmKaryakartas = (fbData && fbData.karyakartas) || [];
          cachedVpmTenant = curTenant;
        }
      } catch (e) {
        try {
          const fbRes = await fetch(`/api/karyakartas?tenant=${encodeURIComponent(curTenant)}`);
          const fbData = await fbRes.json();
          cachedVpmKaryakartas = (fbData && fbData.karyakartas) || [];
          cachedVpmTenant = curTenant;
        } catch (err2) {
          cachedVpmKaryakartas = [];
        }
      }
    }
    populateVpmKaryakartaSelect(selectedId);
  }

  window.vpmToggleNewKaryakartaForm = function (forceState) {
    const box = document.getElementById('vpmNewKaryakartaBox');
    const toggleBtn = document.getElementById('vpmToggleNewKaryaText');
    const errBox = document.getElementById('vpmNewKaryaError');
    if (!box) return;

    const willShow = typeof forceState === 'boolean' ? forceState : box.classList.contains('hidden');
    if (errBox) { errBox.classList.add('hidden'); errBox.textContent = ''; }

    if (willShow) {
      box.classList.remove('hidden');
      if (toggleBtn) toggleBtn.textContent = 'बंद करें (Close)';

      // Auto-focus name field
      const nameInput = document.getElementById('vpmNewKaryaName');
      setTimeout(() => nameInput?.focus(), 50);

      // Smart pre-fill booth/ward based on active voter
      const boothSelect = document.getElementById('vpmNewKaryaBooth');
      if (boothSelect) {
        // If booth select has only 1 option (General), populate from window.BOOTHS_DATA
        if (boothSelect.options.length <= 1 && window.BOOTHS_DATA && typeof window.BOOTHS_DATA === 'object') {
          const boothKeys = Object.keys(window.BOOTHS_DATA);
          if (boothKeys.length > 0) {
            boothKeys.forEach(p => {
              const bInfo = window.BOOTHS_DATA[p] || {};
              const opt = document.createElement('option');
              opt.value = p;
              opt.textContent = `भाग ${p}${bInfo.name ? ' — ' + bInfo.name : ''}`;
              boothSelect.appendChild(opt);
            });
          }
        }

        const voter = (window._vpmCurrentVoters && window._vpmCurrentVoters[0]) || null;
        if (voter) {
          const targetWard = voter.ward_no;
          const targetPart = voter.part_no || voter.booth_no;
          let matched = false;
          if (targetWard) {
            for (let i = 0; i < boothSelect.options.length; i++) {
              if (boothSelect.options[i].value === `ward:${targetWard}`) {
                boothSelect.selectedIndex = i;
                matched = true;
                break;
              }
            }
          }
          if (!matched && targetPart) {
            for (let i = 0; i < boothSelect.options.length; i++) {
              const val = boothSelect.options[i].value;
              if (val === `part:${targetPart}` || val === String(targetPart)) {
                boothSelect.selectedIndex = i;
                matched = true;
                break;
              }
            }
          }
        }
      }
    } else {
      box.classList.add('hidden');
      if (toggleBtn) toggleBtn.textContent = 'नया कार्यकर्ता';
    }
  };

  window.vpmHandleKaryakartaSelectChange = function (sel) {
    if (!sel) return;
    if (sel.value === '__NEW__') {
      window.vpmToggleNewKaryakartaForm(true);
      sel.value = '';
    }
    if (typeof vpmUpdateDetailsBadge === 'function') {
      vpmUpdateDetailsBadge();
    }
  };

  window.vpmSaveNewKaryakarta = async function () {
    const nameEl = document.getElementById('vpmNewKaryaName');
    const phoneEl = document.getElementById('vpmNewKaryaPhone');
    const boothEl = document.getElementById('vpmNewKaryaBooth');
    const areaEl = document.getElementById('vpmNewKaryaArea');
    const errBox = document.getElementById('vpmNewKaryaError');
    const saveBtn = document.getElementById('vpmBtnSaveNewKarya');
    const saveBtnText = document.getElementById('vpmBtnSaveNewKaryaText');

    const name = nameEl?.value?.trim() || '';
    let phone = phoneEl?.value?.trim() || '';
    const booth_no = boothEl?.value || '0';
    const area = areaEl?.value?.trim() || '';

    const showError = (msg) => {
      if (errBox) {
        errBox.textContent = msg;
        errBox.classList.remove('hidden');
      } else {
        window.showToast?.(msg, 'warning');
      }
    };

    if (errBox) { errBox.classList.add('hidden'); errBox.textContent = ''; }

    if (!name) {
      showError('कृपया कार्यकर्ता का नाम दर्ज करें (Name is required)');
      nameEl?.focus();
      return;
    }

    if (!phone) {
      showError('कृपया 10-अंकीय मोबाइल नंबर दर्ज करें (Phone is required)');
      phoneEl?.focus();
      return;
    }

    phone = phone.replace(/[^0-9]/g, '');
    if (phone.length === 12 && phone.startsWith('91')) phone = phone.slice(2);
    if (phone.length === 11 && phone.startsWith('0')) phone = phone.slice(1);
    if (phone.length !== 10) {
      showError('कृपया मान्य 10-अंकीय मोबाइल नंबर दर्ज करें (10-digit mobile required)');
      phoneEl?.focus();
      return;
    }

    if (saveBtn) saveBtn.disabled = true;
    if (saveBtnText) saveBtnText.textContent = 'जोड़ रहे हैं... (Saving...)';

    const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';

    try {
      const res = await fetch(`/api/karyakartas/save?tenant=${encodeURIComponent(curTenant)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          phone: phone,
          booth_no: booth_no,
          area: area
        })
      });

      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || 'कार्यकर्ता जोड़ने में विफल (Failed to save)');
      }

      const newId = data.id;
      const cleanBooth = (booth_no && !booth_no.startsWith('0')) ? booth_no.replace('ward:', 'वार्ड ').replace('part:', 'भाग ') : '-';
      const newKarya = {
        id: newId,
        name: name,
        phone: phone,
        booth_no: cleanBooth,
        area: area
      };

      if (!Array.isArray(cachedVpmKaryakartas)) {
        cachedVpmKaryakartas = [];
      }
      cachedVpmKaryakartas.push(newKarya);

      // Re-populate select and auto-select new karyakarta
      populateVpmKaryakartaSelect(newId);

      // Update badge
      if (typeof vpmUpdateDetailsBadge === 'function') {
        vpmUpdateDetailsBadge();
      }

      // Reset form & close
      if (nameEl) nameEl.value = '';
      if (phoneEl) phoneEl.value = '';
      if (areaEl) areaEl.value = '';
      window.vpmToggleNewKaryakartaForm(false);

      window.showToast?.(`कार्यकर्ता '${name}' सफलतापूर्वक जोड़ा गया व चुना गया!`, 'success');
    } catch (err) {
      showError(err.message || 'त्रुटि: पुनः प्रयास करें');
    } finally {
      if (saveBtn) saveBtn.disabled = false;
      if (saveBtnText) saveBtnText.textContent = 'जोड़ें व चुनें (Save & Assign)';
    }
  };

  function setModalLoading(isLoading) {
    const nameEl = document.getElementById('vpmVoterName');
    if (nameEl && isLoading) {
      nameEl.textContent = "विवरण लोड हो रहा है...";
    }
  }

})();
