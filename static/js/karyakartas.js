/**
 * karyakartas.js - Karyakarta & Panna Pramukh Field Management
 * Part of SmartBooth (Mobile & Outdoor Polling Platform)
 */

(function () {
  'use strict';

  let cachedKaryakartas = [];

  window.karyakartaInit = function () {
    window.loadKaryakartas();
  };

  // Dedicated reactive renderer for Karyakarta cards and stats
  window.renderKaryakartasGrid = function (workers) {
    const list = workers || [];
    const grid = document.getElementById('karyakartaCardsGrid');

    // Summary Stats calculation
    let sumAssigned = 0;
    let sumVoted = 0;
    list.forEach(k => {
      sumAssigned += (k.assigned_voters || 0);
      sumVoted += (k.voted_count || 0);
    });

    const totW = document.getElementById('karyakartaTotalWorkers');
    const totA = document.getElementById('karyakartaTotalAssigned');
    const totV = document.getElementById('karyakartaTotalVoted');

    if (totW) totW.textContent = list.length;
    if (totA) totA.textContent = sumAssigned.toLocaleString('en-IN');
    if (totV) totV.textContent = `${sumVoted} (${sumAssigned > 0 ? Math.round((sumVoted / sumAssigned) * 100) : 0}%)`;

    if (!grid) return;

    if (list.length === 0) {
      grid.innerHTML = `
        <div class="col-span-full p-12 text-center bg-white rounded-2xl border-2 border-dashed border-slate-300 space-y-3">
          <div class="text-3xl">👥</div>
          <div class="text-sm font-bold text-slate-800">अभी तक कोई कार्यकर्ता नहीं जोड़ा गया है</div>
          <p class="text-xs text-slate-500">ऊपर <strong>"➕ नया कार्यकर्ता जोड़ें"</strong> बटन दबाकर कार्यकर्ताओं को दायित्व सौंपें</p>
          <button type="button" onclick="openAddKaryakartaModal()" class="bg-amber-500 hover:bg-amber-400 text-slate-950 font-black px-4 py-2 rounded-lg text-xs transition shadow-sm cursor-pointer">
            ➕ पहला कार्यकर्ता जोड़ें
          </button>
        </div>
      `;
      return;
    }

    grid.innerHTML = list.map(k => {
      const phone = (k.phone || '').trim();
      const phoneClean = phone.replace(/\D/g, '');
      const hasPhone = phoneClean.length >= 10;
      const pct = k.turnout_pct || 0;
      const barColor = pct >= 60 ? 'bg-emerald-500' : (pct >= 40 ? 'bg-amber-500' : 'bg-rose-500');

      return `
        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 space-y-3 hover:shadow-md transition" id="karyakartaCard_${k.id}">
          <div class="flex items-start justify-between gap-2 border-b border-slate-100 pb-2.5">
            <div>
              <div class="flex items-center gap-1.5 flex-wrap">
                <h4 class="text-sm font-black text-slate-900">${window.escapeHtml(k.name)}</h4>
                <button type="button" onclick="toggleKaryakartaActive(${k.id}, ${Boolean(k.is_active)})" class="text-[10px] font-bold px-2 py-0.5 rounded-full border cursor-pointer transition ${k.is_active ? 'bg-emerald-100 text-emerald-800 border-emerald-300' : 'bg-rose-100 text-rose-800 border-rose-300'}" title="स्थिति बदलें">
                  ${k.is_active ? '● सक्रिय' : '✕ निष्क्रिय'}
                </button>
              </div>
              <span class="text-[11px] text-slate-500 block font-semibold mt-0.5">
                ${k.booth_no ? (window.IS_WARD_ELECTION ? `वार्ड / केंद्र ${k.booth_no}` : `भाग ${k.booth_no}`) : 'समस्त क्षेत्र'} ${k.area ? `· ${window.escapeHtml(k.area)}` : ''}
              </span>
            </div>
            <div class="flex items-center gap-1">
              <button type="button" onclick="openAddKaryakartaModal(${k.id})" class="text-slate-400 hover:text-slate-600 p-1 text-xs cursor-pointer" title="Edit">✏️</button>
              <button type="button" onclick="deleteKaryakarta(event, ${k.id})" class="text-rose-400 hover:text-rose-600 p-1 text-xs cursor-pointer" title="Delete">🗑️</button>
            </div>
          </div>

          <!-- Direct Clean Communication Action Bar (Call + Send Link via API) -->
          <div class="grid ${hasPhone ? 'grid-cols-2' : 'grid-cols-1'} gap-2 pt-0.5">
            ${hasPhone ? `
              <a href="tel:${phoneClean}" onclick="if(window.triggerHaptic) window.triggerHaptic('selection');" class="min-h-[42px] bg-slate-50 hover:bg-slate-100 text-slate-800 border border-slate-300 font-bold px-3 rounded-xl text-xs flex items-center justify-center gap-1.5 transition active:scale-95 shadow-2xs cursor-pointer">
                <span class="text-base">📞</span>
                <span>Call</span>
                <span class="font-mono text-[11px] text-slate-500 font-bold">(${phone})</span>
              </a>
            ` : ''}
            <button type="button" onclick="sendKaryakartaMagicLinkMeta(${k.id}, this)" class="min-h-[42px] ${hasPhone ? '' : 'w-full'} bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 text-white font-black px-3.5 py-2 rounded-xl text-xs flex items-center justify-center gap-1.5 transition shadow-sm cursor-pointer">
              <span class="text-base">📲</span>
              <span>Send Link</span>
            </button>
          </div>

          <!-- Assignment & Turnout Metrics -->
          <div class="bg-slate-50 rounded-xl p-2.5 border border-slate-200 text-xs space-y-1.5">
            <div class="flex items-center justify-between font-semibold">
              <span class="text-slate-600">आवंटित मतदाता:</span>
              <strong class="text-slate-900 font-black">${k.assigned_voters || 0} मतदाता</strong>
            </div>
            <div class="flex items-center justify-between font-semibold">
              <span class="text-slate-600">वोट डाले / शेष:</span>
              <span class="text-emerald-700 font-bold">${k.voted_count || 0} Voted <span class="text-slate-400">/ ${k.pending_count || 0} Pending</span></span>
            </div>
            <div class="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
              <div class="${barColor} h-full rounded-full transition-all duration-300" style="width: ${pct}%"></div>
            </div>
          </div>

          <!-- Drawer Button -->
          <div class="pt-0.5 flex items-center gap-2">
            <button type="button" onclick="openKaryakartaDrawer(${k.id})" class="min-h-[42px] flex-1 bg-slate-900 hover:bg-slate-800 active:bg-slate-700 text-white font-black py-2 rounded-xl text-xs transition flex items-center justify-center gap-1.5 cursor-pointer shadow-sm">
              <span>📋 मतदाता सूची देखें (${k.assigned_voters || 0})</span>
            </button>
          </div>
        </div>
      `;
    }).join('');
  };

  window.loadKaryakartas = async function (silent = false) {
    const grid = document.getElementById('karyakartaCardsGrid');
    if (grid && !silent && cachedKaryakartas.length === 0) {
      grid.innerHTML = '<div class="col-span-full p-8 text-center bg-white rounded-xl border border-slate-200 text-slate-400 font-bold">कार्यकर्ता सूची लोड हो रही है...</div>';
    }

    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const params = new URLSearchParams();
      if (curTenant) params.set('tenant', curTenant);
      params.set('_t', Date.now());

      const wardSelect = document.getElementById('karyakartaWardFilter');
      if (wardSelect && wardSelect.value) {
        const val = wardSelect.value.trim();
        const filterKey = wardSelect.getAttribute('data-filter-key') || 'ward_no';
        params.set(filterKey, val);
      }

      const res = await fetch(`/api/karyakartas?${params.toString()}`, { cache: 'no-store' });
      const data = await res.json();
      if (!data.success) return;

      cachedKaryakartas = data.karyakartas || [];
      window.renderKaryakartasGrid(cachedKaryakartas);
    } catch (e) {
      console.error('loadKaryakartas error:', e);
    }
  };

  window.openAddKaryakartaModal = function (id) {
    if (window.triggerHaptic) window.triggerHaptic('selection');
    const modal = document.getElementById('karyakartaModal');
    if (!modal) {
      console.error('karyakartaModal element not found');
      return;
    }

    const idEl = document.getElementById('modalKaryakartaId');
    const nameEl = document.getElementById('modalKaryakartaName');
    const phoneEl = document.getElementById('modalKaryakartaPhone');
    const boothEl = document.getElementById('modalKaryakartaBooth');
    const areaEl = document.getElementById('modalKaryakartaArea');
    const notesEl = document.getElementById('modalKaryakartaNotes');

    if (idEl) idEl.value = '';
    if (nameEl) nameEl.value = '';
    if (phoneEl) phoneEl.value = '';
    if (boothEl) boothEl.value = '0';
    if (areaEl) areaEl.value = '';
    if (notesEl) notesEl.value = '';

    const titleEl = document.getElementById('karyakartaModalTitle');
    if (id) {
      const k = (cachedKaryakartas || []).find(x => x.id === id);
      if (k) {
        if (titleEl) titleEl.textContent = '✏️ कार्यकर्ता विवरण बदलें';
        if (idEl) idEl.value = k.id;
        if (nameEl) nameEl.value = k.name || '';
        if (phoneEl) phoneEl.value = k.phone || '';
        if (boothEl) {
          boothEl.value = String(k.booth_no || '0');
          if (!boothEl.value || boothEl.selectedIndex === -1) {
            boothEl.value = `ward:${k.booth_no}`;
          }
          if (!boothEl.value || boothEl.selectedIndex === -1) {
            boothEl.value = `part:${k.booth_no}`;
          }
          if (boothEl.selectedIndex === -1) boothEl.value = '0';
        }
        if (areaEl) areaEl.value = k.area || '';
        if (notesEl) notesEl.value = k.notes || '';
      }
    } else {
      if (titleEl) titleEl.textContent = '➕ नया कार्यकर्ता जोड़ें';
    }
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    if (nameEl) {
      setTimeout(() => nameEl.focus(), 60);
    }
  };

  window.closeKaryakartaModal = function () {
    const modal = document.getElementById('karyakartaModal');
    if (modal) {
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }
    if (window.triggerHaptic) window.triggerHaptic('selection');
  };

  window.submitKaryakartaForm = async function (e) {
    if (e) {
      if (typeof e.preventDefault === 'function') e.preventDefault();
      if (typeof e.stopPropagation === 'function') e.stopPropagation();
    }
    const id = document.getElementById('modalKaryakartaId')?.value || '';
    const name = (document.getElementById('modalKaryakartaName')?.value || '').trim();
    const phone = (document.getElementById('modalKaryakartaPhone')?.value || '').trim();
    const booth = document.getElementById('modalKaryakartaBooth')?.value || '0';
    const area = (document.getElementById('modalKaryakartaArea')?.value || '').trim();
    const notes = (document.getElementById('modalKaryakartaNotes')?.value || '').trim();

    if (!name) {
      if (typeof window.showToast === 'function') {
        window.showToast('कृपया कार्यकर्ता का नाम दर्ज करें', 'warning');
      } else {
        alert('कृपया कार्यकर्ता का नाम दर्ज करें');
      }
      document.getElementById('modalKaryakartaName')?.focus();
      return false;
    }

    const saveBtn = document.getElementById('modalKaryakartaSaveBtn');
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.innerHTML = '<span>⏳ सुरक्षित हो रहा है...</span>';
    }

    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const sep = curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : '';
      const res = await fetch(`/api/karyakartas/save${sep}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, name, phone, booth_no: booth, area, notes })
      });
      const data = await res.json();
      if (data.success) {
        if (window.triggerHaptic) window.triggerHaptic('success');
        if (typeof window.showToast === 'function') {
          window.showToast(`कार्यकर्ता "${name}" सुरक्षित कर लिया गया है`, 'success');
        }
        window.closeKaryakartaModal();

        // Optimistic UI Update: immediately show in grid without waiting or reloading
        const targetId = data.id || (id ? parseInt(id) : Date.now());
        const newOrUpdated = {
          id: targetId,
          name: name,
          phone: phone,
          booth_no: parseInt(booth) || 0,
          area: area,
          notes: notes,
          assigned_voters: 0,
          voted_count: 0,
          pending_count: 0,
          turnout_pct: 0
        };

        const existingIdx = cachedKaryakartas.findIndex(x => x.id === targetId);
        if (existingIdx >= 0) {
          cachedKaryakartas[existingIdx] = { ...cachedKaryakartas[existingIdx], ...newOrUpdated };
        } else {
          cachedKaryakartas.unshift(newOrUpdated);
        }
        window.renderKaryakartasGrid(cachedKaryakartas);

        // Silent background sync
        window.loadKaryakartas(true);
      } else {
        const errMsg = data.error || 'सुरक्षित करने में त्रुटि आई';
        if (typeof window.showToast === 'function') {
          window.showToast(errMsg, 'error');
        } else {
          alert(errMsg);
        }
      }
    } catch (err) {
      console.error('submitKaryakartaForm error:', err);
      if (typeof window.showToast === 'function') {
        window.showToast('सर्वर से संपर्क करने में समस्या आई। पुनः प्रयास करें।', 'error');
      } else {
        alert('सर्वर से संपर्क करने में समस्या आई। पुनः प्रयास करें।');
      }
    } finally {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.innerHTML = 'सुरक्षित करें (Save)';
      }
    }
    return false;
  };

  window.deleteKaryakarta = async function (e, id) {
    if (e) {
      if (typeof e.preventDefault === 'function') e.preventDefault();
      if (typeof e.stopPropagation === 'function') e.stopPropagation();
    }
    if (!id && typeof e === 'number') id = e;

    if (!confirm('क्या आप इस कार्यकर्ता को हटाना चाहते हैं?')) return false;

    // Optimistic UI Update: remove from screen immediately
    const backupList = [...cachedKaryakartas];
    cachedKaryakartas = cachedKaryakartas.filter(x => x.id !== id);
    window.renderKaryakartasGrid(cachedKaryakartas);
    if (window.triggerHaptic) window.triggerHaptic('medium');

    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const sep = curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : '';
      const res = await fetch(`/api/karyakartas/delete/${id}${sep}`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        if (typeof window.showToast === 'function') {
          window.showToast('कार्यकर्ता हटा दिया गया है', 'info');
        }
        window.loadKaryakartas(true); // silent sync
      } else {
        // Rollback on server error
        cachedKaryakartas = backupList;
        window.renderKaryakartasGrid(cachedKaryakartas);
        alert(data.error || 'हटाने में त्रुटि आई');
      }
    } catch (err) {
      console.error(err);
      cachedKaryakartas = backupList;
      window.renderKaryakartasGrid(cachedKaryakartas);
      alert('सर्वर से संपर्क करने में समस्या आई।');
    }
    return false;
  };

  // Karyakarta Assigned Voters Drawer Implementation
  let activeDrawerKaryakarta = null;
  let activeDrawerVoters = [];

  window.openKaryakartaDrawer = async function (id) {
    if (window.triggerHaptic) window.triggerHaptic('selection');
    const drawer = document.getElementById('karyakartaVotersDrawer');
    if (!drawer) return;

    drawer.classList.remove('hidden');
    const nameEl = document.getElementById('kDrawerWorkerName');
    const metaEl = document.getElementById('kDrawerWorkerMeta');
    const statsEl = document.getElementById('kDrawerStatsBar');
    const listEl = document.getElementById('kDrawerVotersList');

    if (nameEl) nameEl.textContent = 'कार्यकर्ता मतदाता सूची';
    if (metaEl) metaEl.textContent = 'लोड हो रहा है...';
    if (listEl) listEl.innerHTML = '<div class="p-8 text-center text-slate-400 font-bold">मतदाता विवरण लोड हो रहा है...</div>';

    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const params = new URLSearchParams();
      if (curTenant) params.set('tenant', curTenant);
      const wardSelect = document.getElementById('karyakartaWardFilter');
      if (wardSelect && wardSelect.value) {
        const filterKey = wardSelect.getAttribute('data-filter-key') || 'ward_no';
        params.set(filterKey, wardSelect.value.trim());
      }
      const res = await fetch(`/api/karyakartas/${id}/voters?${params.toString()}`);
      const data = await res.json();
      if (!data.success) {
        if (listEl) listEl.innerHTML = `<div class="p-8 text-center text-rose-500 font-bold">${data.error || 'लोड करने में विफल'}</div>`;
        return;
      }

      activeDrawerKaryakarta = data.karyakarta || {};
      activeDrawerVoters = data.voters || [];

      if (nameEl) nameEl.textContent = `${activeDrawerKaryakarta.name || 'कार्यकर्ता'} की मतदाता सूची`;
      if (metaEl) metaEl.textContent = `बूथ: ${activeDrawerKaryakarta.booth_no ? 'भाग ' + activeDrawerKaryakarta.booth_no : 'समस्त'} · क्षेत्र: ${activeDrawerKaryakarta.area || '-'} · 📞 ${activeDrawerKaryakarta.phone || '-'}`;

      const total = activeDrawerVoters.length;
      const voted = activeDrawerVoters.filter(v => v.is_voted).length;
      const pending = total - voted;

      if (statsEl) {
        statsEl.innerHTML = `
          <span>कुल आवंटित: <strong class="text-slate-900">${total}</strong></span>
          <span>वोट डाले: <strong class="text-emerald-700">${voted}</strong></span>
          <span>शेष: <strong class="text-amber-700">${pending}</strong></span>
        `;
      }

      if (total === 0) {
        if (listEl) listEl.innerHTML = '<div class="p-8 text-center text-slate-400 font-bold">इस कार्यकर्ता को अभी तक कोई मतदाता आवंटित नहीं किया गया है। मतदाता खोज से मतदाताओं को चुनें और "दायित्व" बटन दबाएं।</div>';
        return;
      }

      if (listEl) {
        listEl.innerHTML = activeDrawerVoters.map((v, idx) => {
          const ph = (v.phone || '').trim();
          const phClean = ph.replace(/\D/g, '');
          const votedBadge = v.is_voted
            ? '<span class="bg-emerald-100 text-emerald-800 border border-emerald-300 font-bold px-1.5 py-0.5 rounded text-[10px]">✓ Voted</span>'
            : '<span class="bg-slate-100 text-slate-600 font-bold px-1.5 py-0.5 rounded text-[10px]">Pending</span>';

          return `
            <div class="bg-slate-50 border border-slate-200 rounded-xl p-3 flex items-center justify-between gap-2.5 text-xs hover:bg-white transition">
              <div class="space-y-0.5 min-w-0">
                <div class="flex items-center gap-1.5 flex-wrap">
                  <span class="font-mono text-slate-400 text-[11px]">#${idx + 1}</span>
                  <strong class="text-slate-900 font-black truncate">${v.name}</strong>
                  <span class="bg-amber-100 text-amber-950 font-bold px-1.5 py-0.5 rounded text-[10px]">${(v.ward_no && v.ward_no !== v.part_no) ? `वार्ड ${v.ward_no} · भाग ${v.part_no}` : (window.IS_WARD_ELECTION ? `वार्ड ${v.ward_no || v.part_no}` : `भाग ${v.part_no}`)} · क्र. ${v.sl_no}</span>
                  ${votedBadge}
                </div>
                <div class="text-[11px] text-slate-500 truncate">
                  ${v.rel_name ? `सम्बन्ध: <span class="font-medium text-slate-700">${v.rel_name}</span>` : ''}
                  ${v.house_no && v.house_no !== '0' ? ` · म.नं. ${v.house_no}` : ''}
                  ${v.epic_no ? ` · EPIC: <span class="font-mono">${v.epic_no}</span>` : ''}
                </div>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                ${ph ? `
                  <a href="tel:${ph}" class="p-2 bg-blue-50 text-blue-700 hover:bg-blue-100 rounded-lg text-xs font-bold transition flex items-center gap-1" title="कॉल करें">
                    <span>📞</span><span class="hidden sm:inline font-mono">${ph}</span>
                  </a>
                  <a href="https://wa.me/91${phClean}" target="_blank" class="p-2 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 rounded-lg text-xs font-bold transition" title="WhatsApp भेजें">
                    <span>💬</span>
                  </a>
                ` : '<span class="text-[10px] text-slate-400 font-medium px-2 py-1 bg-slate-100 rounded">फोन नहीं</span>'}
              </div>
            </div>
          `;
        }).join('');
      }
    } catch (err) {
      console.error('openKaryakartaDrawer error:', err);
      if (listEl) listEl.innerHTML = '<div class="p-8 text-center text-rose-500 font-bold">सर्वर से डेटा लोड करने में त्रुटि आई</div>';
    }
  };

  window.closeKaryakartaDrawer = function () {
    const drawer = document.getElementById('karyakartaVotersDrawer');
    if (drawer) drawer.classList.add('hidden');
    if (window.triggerHaptic) window.triggerHaptic('selection');
  };

  window.sendKaryakartaMagicLinkMeta = async function (kid, btn) {
    if (window.triggerHaptic) window.triggerHaptic('selection');
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span>⏳</span><span>भेज रहे हैं...</span>';
    }
    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const sep = curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : '';
      const res = await fetch(`/api/karyakartas/${kid}/send_whatsapp${sep}`, { method: 'POST' });
      let data;
      try {
        data = await res.json();
      } catch (parseErr) {
        data = { success: false, error: `सर्वर त्रुटि (HTTP ${res.status})` };
      }
      if (data.success) {
        alert(`✅ कार्यकर्ता को Meta WhatsApp से 1-क्लिक लॉगिन लिंक सफलतापूर्वक भेज दिया गया!\n\nमोबाइल: ${data.phone || ''}\nलिंक: ${data.magic_link || ''}`);
      } else {
        alert(data.error || 'व्हाट्सएप संदेश भेजने में त्रुटि आई। कृपया कोटा या फोन नंबर जांचें।');
      }
    } catch (e) {
      console.error('sendKaryakartaMagicLinkMeta error:', e);
      alert('सर्वर से संपर्क करने में समस्या आई।');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }
    }
  };

  window.dispatchDrawerWorkerWhatsApp = async function () {
    if (!activeDrawerKaryakarta || !activeDrawerKaryakarta.id) return;
    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const res = await fetch(`/api/karyakartas/${activeDrawerKaryakarta.id}/send_whatsapp?tenant=${encodeURIComponent(curTenant)}`, {
        method: 'POST'
      });
      let data;
      try {
        data = await res.json();
      } catch (parseErr) {
        data = { success: false, error: `सर्वर त्रुटि (HTTP ${res.status})` };
      }
      if (data.success) {
        alert('कार्यकर्ता को व्हाट्सएप संदेश भेज दिया गया!');
      } else {
        alert(data.error || 'व्हाट्सएप भेजने में त्रुटि आई');
      }
    } catch (e) {
      console.error(e);
      alert('सर्वर त्रुटि');
    }
  };

  window.copyDrawerWorkerWhatsAppText = function () {
    if (!activeDrawerVoters || activeDrawerVoters.length === 0) {
      alert('कोई मतदाता नहीं हैं');
      return;
    }
    const lines = [
      `*${activeDrawerKaryakarta.name || 'कार्यकर्ता'} - आवंटित मतदाता सूची*`,
      `कुल मतदाता: ${activeDrawerVoters.length}`,
      '-------------------------'
    ];
    activeDrawerVoters.forEach((v, idx) => {
      const st = v.is_voted ? '✓ Voted' : 'Pending';
      lines.push(`${idx + 1}. ${v.name} (भाग ${v.part_no} क्र. ${v.sl_no}) म.नं. ${v.house_no || '-'} - ${v.phone || 'फोन नहीं'} [${st}]`);
    });
    navigator.clipboard.writeText(lines.join('\n')).then(() => {
      alert('सूची क्लिपबोर्ड पर कॉपी हो गई!');
    });
  };

  window.copyKaryakartaMagicLink = function (token) {
    if (!token || token === 'undefined' || token === 'null') {
      alert('इस कार्यकर्ता का लॉगिन लिंक उपलब्ध नहीं है। कृपया "रीसेट" बटन दबाएं।');
      return;
    }
    const origin = window.location.origin;
    const url = `${origin}/k/${token}`;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(() => {
        alert(`✅ कार्यकर्ता लॉगिन लिंक कॉपी हुआ!\n\n${url}\n\nयह लिंक सीधे व्हाट्सएप पर भेजें। कार्यकर्ता बिना किसी पासवर्ड के सीधे खुल जाएगा।`);
      }).catch(() => {
        prompt('कार्यकर्ता का लॉगिन लिंक कॉपी करें:', url);
      });
    } else {
      prompt('कार्यकर्ता का लॉगिन लिंक कॉपी करें:', url);
    }
  };

  window.rotateKaryakartaToken = async function (kid) {
    if (!confirm('क्या आप इस कार्यकर्ता का लॉगिन लिंक बदलना चाहते हैं?\nपुराना लिंक तुरंत काम करना बंद कर देगा!')) return;
    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const sep = curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : '';
      const res = await fetch(`/api/karyakartas/${kid}/rotate_token${sep}`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        alert('✅ नया लॉगिन लिंक सफलतापूर्वक जारी किया गया!');
        window.loadKaryakartas(true);
      } else {
        alert(data.error || 'टोकन बदलने में विफल');
      }
    } catch (e) {
      console.error(e);
      alert('नेटवर्क त्रुटि: लिंक रीसेट नहीं हो सका');
    }
  };

  window.toggleKaryakartaActive = async function (kid, currentStatus) {
    const actionText = currentStatus ? 'निष्क्रिय (Deactivate)' : 'सक्रिय (Activate)';
    if (!confirm(`क्या आप इस कार्यकर्ता को ${actionText} करना चाहते हैं?`)) return;
    try {
      const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
      const sep = curTenant ? `?tenant=${encodeURIComponent(curTenant)}` : '';
      const res = await fetch(`/api/karyakartas/${kid}/toggle_status${sep}`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        window.loadKaryakartas(true);
      } else {
        alert(data.error || 'स्थिति बदलने में विफल');
      }
    } catch (e) {
      console.error(e);
      alert('नेटवर्क त्रुटि: स्थिति नहीं बदली जा सकी');
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('karyakartaViewContainer');
    if (container) {
      window.karyakartaInit();
    }
  });

})();
