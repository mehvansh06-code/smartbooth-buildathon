/**
 * campaign_intel.js - Political Categorization, Karyakarta Assignment & Turnout
 * Part of SmartBooth Platform
 */

(function () {
  'use strict';

  // Bulk Categorization (100% Pakka, 50-50 Swing, Opposition)
  window.bulkSetCategory = async function (category) {
    const ids = Array.from(window.APP_STATE.selectedVoters.keys());
    if (ids.length === 0) {
      window.showToast('कृपया पहले कम से कम एक मतदाता चुनें', 'warning');
      return;
    }

    const labels = {
      '100': '🟢 100% पक्का समर्थक',
      '50_50': '🟡 50-50 संशय / स्विंग',
      'opposition': '🔴 विपक्षी मतदाता',
      'clear': '⚪ श्रेणी रीसेट'
    };

    try {
      const res = await fetch('/api/voter/bulk_category', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voter_ids: ids,
          category: category === 'clear' ? '0' : category
        })
      });

      const data = await res.json();
      if (data.success) {
        if (window.triggerHaptic) window.triggerHaptic('success');
        window.playAudioTone(1050, 0.1);
        window.showToast(`${ids.length} मतदाताओं को "${labels[category] || category}" के रूप में चिह्नित किया गया`, 'success');
        // Update in-memory selected state
        const targetCategory = category === 'clear' ? '0' : category;
        ids.forEach(id => {
          const v = window.APP_STATE.selectedVoters.get(id);
          if (v) v.voter_category = targetCategory;
        });
        // Reactively update rendered voter cards/table
        if (typeof window.updateVoterCategoryInResults === 'function') {
          window.updateVoterCategoryInResults(ids, targetCategory);
        } else if (typeof window.triggerOmniSearch === 'function') {
          window.triggerOmniSearch();
        }
      }
    } catch (err) {
      console.error('Category error:', err);
      window.showToast('श्रेणी अपडेट करने में त्रुटि', 'error');
    }
  };

  // Karyakarta Assignment Modal
  window.openAssignKaryakartaModal = function () {
    const ids = Array.from(window.APP_STATE.selectedVoters.keys());
    if (ids.length === 0) {
      window.showToast('कृपया पहले मतदाता चुनें', 'warning');
      return;
    }
    const modal = document.getElementById('assignKaryakartaModal');
    if (modal) {
      modal.classList.remove('hidden');
      const countSpan = document.getElementById('assignKaryaCount');
      if (countSpan) countSpan.textContent = ids.length;

      // If dropdown is empty, trigger loading
      const select = document.getElementById('assignKaryaSelect');
      if (select && select.options.length <= 1 && typeof window.loadKaryakartasDropdown === 'function') {
        window.loadKaryakartasDropdown();
      }
    }
  };

  window.closeAssignKaryakartaModal = function () {
    const modal = document.getElementById('assignKaryakartaModal');
    if (modal) modal.classList.add('hidden');
  };

  window.confirmAssignKaryakarta = async function () {
    const select = document.getElementById('assignKaryaSelect');
    const karyaId = select ? select.value : '';
    if (!karyaId) {
      window.showToast('कृपया कार्यकर्ता का चयन करें', 'warning');
      return;
    }

    const selectedOptionText = select.options[select.selectedIndex]?.text || '';
    const karyaName = selectedOptionText.split('(')[0]?.trim() || '';

    const ids = Array.from(window.APP_STATE.selectedVoters.keys());
    try {
      const res = await fetch('/api/karyakartas/assign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voter_ids: ids,
          karyakarta_id: parseInt(karyaId)
        })
      });
      const data = await res.json();
      if (data.success) {
        window.showToast(`${ids.length} मतदाताओं को कार्यकर्ता (${karyaName}) को सौंपा गया!`, 'success');
        window.closeAssignKaryakartaModal();

        // Update in-memory state
        ids.forEach(id => {
          const v = window.APP_STATE.selectedVoters.get(id);
          if (v) {
            v.assigned_karyakarta_id = parseInt(karyaId);
            v.assigned_karyakarta_name = karyaName;
          }
        });

        if (typeof window.updateVoterKaryakartaInResults === 'function') {
          window.updateVoterKaryakartaInResults(ids, parseInt(karyaId), karyaName);
        }
      } else {
        window.showToast(data.error || 'कार्यकर्ता सौंपने में त्रुटि', 'error');
      }
    } catch (err) {
      console.error('Assign error:', err);
      window.showToast('सर्वर त्रुटि: कृपया पुनः प्रयास करें', 'error');
    }
  };

  // Phone Number Quick Update (Strictly Individual)
  let activePhoneUpdateVoterId = null;

  window.openPhoneUpdateModal = function (voterId, voterName, currentPhone) {
    activePhoneUpdateVoterId = voterId;
    const modal = document.getElementById('quickPhoneModal');
    if (!modal) return;
    modal.classList.remove('hidden');

    const nameEl = document.getElementById('phoneModalVoterName');
    if (nameEl) {
      nameEl.textContent = voterName ? `मतदाता: ${voterName}` : 'व्यक्तिगत संपर्क नंबर';
    }

    const input = document.getElementById('quickPhoneInput');
    if (input) {
      input.value = currentPhone || '';
      input.focus();
    }
  };

  window.closePhoneUpdateModal = function () {
    const modal = document.getElementById('quickPhoneModal');
    if (modal) modal.classList.add('hidden');
    activePhoneUpdateVoterId = null;
  };

  window.saveVoterPhone = async function () {
    const input = document.getElementById('quickPhoneInput');
    const phone = (input ? input.value : '').trim();

    if (!activePhoneUpdateVoterId) return;

    try {
      const curTenant = window.CURRENT_TENANT || '';
      const res = await fetch(`/api/voter/update_phone?tenant=${encodeURIComponent(curTenant)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voter_id: activePhoneUpdateVoterId,
          phone: phone,
          apply_to_family: false
        })
      });
      const data = await res.json();
      if (data.success) {
        // Optimistically update only the target voter in local state
        if (window.APP_STATE && Array.isArray(window.APP_STATE.currentResults)) {
          const target = window.APP_STATE.currentResults.find(v => String(v.id || v.voter_id) === String(activePhoneUpdateVoterId));
          if (target) {
            target.contact_phone = phone;
          }
        }
        if (window.APP_STATE && window.APP_STATE.selectedVoters && window.APP_STATE.selectedVoters.has(activePhoneUpdateVoterId)) {
          const sel = window.APP_STATE.selectedVoters.get(activePhoneUpdateVoterId);
          if (sel) sel.contact_phone = phone;
        }

        if (typeof window.renderCurrentResults === 'function') {
          window.renderCurrentResults();
        }

        window.showToast?.(`मतदाता का मोबाइल नंबर ${phone || 'हटाया गया'} सफलतापूर्वक अपडेट किया गया!`, 'success');
        window.closePhoneUpdateModal();
        if (typeof window.triggerOmniSearch === 'function') window.triggerOmniSearch();
      } else {
        window.showToast?.(data.error || 'फोन अपडेट नहीं हो सका', 'error');
      }
    } catch (err) {
      console.error('Phone update error:', err);
      window.showToast?.('फोन अपडेट करने में सर्वर त्रुटि', 'error');
    }
  };

  // Aliases for quick phone modal
  window.openQuickPhoneModal = window.openPhoneUpdateModal;
  window.closeQuickPhoneModal = window.closePhoneUpdateModal;
  window.saveQuickPhone = window.saveVoterPhone;

  // Mark Outside Voter Modal
  let activeOutsideVoterId = null;

  window.openMarkOutsideModal = function (voterId, voterName) {
    activeOutsideVoterId = voterId || null;
    const modal = document.getElementById('markOutsideModal');
    if (!modal) return;
    modal.classList.remove('hidden');
    const title = document.getElementById('outsideModalTitle');
    if (title) {
      if (voterName) {
        title.textContent = `मतदाता: ${voterName}`;
      } else {
        const count = window.APP_STATE?.selectedVoters?.size || 0;
        title.textContent = count ? `चयनित ${count} मतदाता` : 'प्रवासी मतदाता चिह्नित करें';
      }
    }
  };

  window.closeMarkOutsideModal = function () {
    activeOutsideVoterId = null;
    const modal = document.getElementById('markOutsideModal');
    if (modal) modal.classList.add('hidden');
  };

  window.confirmMarkOutside = async function () {
    const city = (document.getElementById('outsideCityInput')?.value || '').trim();
    const state = (document.getElementById('outsideStateInput')?.value || '').trim();
    const travel = document.getElementById('outsideTravelCb')?.checked;
    const notes = (document.getElementById('outsideNotesInput')?.value || '').trim();
    const travelNote = travel ? 'वाहन सुविधा चाहिए (Needs Travel Assistance)' : '';
    const fullNotes = [city, state, travelNote, notes].filter(Boolean).join(', ');

    const ids = activeOutsideVoterId ? [activeOutsideVoterId] : Array.from(window.APP_STATE.selectedVoters.keys());
    if (ids.length === 0) {
      window.showToast('कृपया पहले मतदाता चुनें', 'warning');
      return;
    }

    try {
      const res = await fetch('/api/outside_voters/mark', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          voter_ids: ids,
          notes: fullNotes
        })
      });
      const data = await res.json();
      if (data.success) {
        window.showToast(`${ids.length} मतदाताओं को प्रवासी के रूप में चिह्नित किया गया!`, 'success');
        window.closeMarkOutsideModal();
        if (typeof window.updateVoterOutsideInResults === 'function') {
          window.updateVoterOutsideInResults(ids, true);
        }
      } else {
        window.showToast(data.error || 'प्रवासी चिह्नित करने में त्रुटि', 'error');
      }
    } catch (e) {
      console.error('Mark outside error:', e);
      window.showToast('सर्वर त्रुटि: कृपया पुनः प्रयास करें', 'error');
    }
  };

})();
