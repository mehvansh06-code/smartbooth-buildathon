/**
 * slip_engine.js - Interactive Slip Generator & Meta WhatsApp Dispatcher
 * Part of SmartBooth Production Engine
 */

(function () {
  'use strict';

  let currentSlipVoters = [];
  let currentSlipText = '';
  let currentSlipMode = 'single'; // 'single' | 'family'
  let modalAllVoters = [];
  let modalPrimaryVoter = null;
  let textFetchAbort = null;

  // Helper: Format bilingual string
  function formatBilingual(en, hi) {
    let e = (en || '').replace(/\ufffd/g, '').trim();
    let h = (hi || '').replace(/\ufffd/g, '').trim();

    // Client-side quick repairs for known OCR dropouts and Rathore
    h = h.replace(/(?:^|\s)ानी(?=\s|राम|दास|लाल|चंद|चन्द|सिंह|देवी|$)/g, ' ज्ञानी')
         .replace(/(?:^|\s)ानीराम/g, ' ज्ञानीराम')
         .replace(/(?:^|\s)ारका\s*प्रसाद/g, ' द्वारका प्रसाद')
         .replace(/(?:^|\s)ूल(?=\s*सिंह)/g, ' मूल')
         .replace(/(?:^|\s)ूलचन्द/g, ' मूलचन्द')
         .replace(/(?:^|\s)ेम(?=\s*(?:देवी|घुमरिया|लता|सिंह))/g, ' प्रेम')
         .replace(/(?:^|\s)ान(?=\s+(?:प्रकाश|कंवर|सिंह|चंद))/g, ' ज्ञान')
         .replace(/(?:^|\s)ानचंद/g, ' ज्ञानचंद')
         .replace(/(?:^|\s)अा\s*राम(?=\s|$)/g, ' अम्रा राम')
         .replace(/(?:^|\s)ूसा(?:\s*राम|राम)/g, ' फूसा राम')
         .replace(/(?:^|\s)ुसाराम/g, ' फूसा राम')
         .replace(/(?:^|\s)ुसी\s*देवी/g, ' फुसी देवी')
         .replace(/(?:^|\s)ुसी(?=\s|$)/g, ' फुसी')
         .replace(/(?:^|\s)सुा\s*राम/g, ' सुग्रा राम')
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
         .replace(/(^|\s)[\u0901-\u0903\u093a-\u094f\u0962\u0963]+/g, '$1')
         .replace(/\u0947\u093e/g, '\u094b')
         .replace(/([\u093e-\u094c])\1+/g, '$1')
         .replace(/([\u093e-\u094c])[\u093e-\u094c]+/g, '$1')
         .replace(/([^\u0915-\u0939])\u093c/g, '$1')
         .replace(/\s*(Photo\s*Available|Photo\s*Not\s*Available|PhotoAvailable|Available|Photo)+$/gi, '')
         .replace(/\u094d+$/g, '')
         .replace(/\u094d\s+/g, '\u094d')
         .replace(/\s+/g, ' ').trim();

    e = e.replace(/\bA Ram\b/gi, 'Amra Ram')
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
         .replace(/\bJnan\b/gi, 'Gyan')
         .replace(/\bChaudhri\b/gi, 'Chaudhary')
         .replace(/\bChoudhri\b/gi, 'Chaudhary')
         .replace(/\bGahlot\b/gi, 'Gehlot')
         .replace(/\bJangir\b/gi, 'Jangid')
         .replace(/\bKumavat\b/gi, 'Kumawat')
         .replace(/\bSisodiya\b/gi, 'Sisodia')
         .replace(/\bMul Singh\b/gi, 'Mool Singh')
         .replace(/\bLa Singh\b/gi, 'Mool Singh')
         .replace(/\bMulchand\b/gi, 'Moolchand')
         .replace(/\bLachanda\b/gi, 'Moolchand')
         .replace(/\bSandip\b/gi, 'Sandeep')
         .replace(/\bKuldip\b/gi, 'Kuldeep')
         .replace(/\bRaka Prasad\b/gi, 'Dwarka Prasad')
         .replace(/\bMa Devi\b/gi, 'Prem Devi')
         .replace(/\s+/g, ' ').trim();

    if (e && h && e.toLowerCase() !== h.toLowerCase()) return `${e} / ${h}`;
    return e || h || 'मतदाता';
  }

  // Helper: Resolve booth name from dropdown or voter object
  function getBoothName(voter) {
    if (voter && voter.booth_name && voter.booth_name.trim()) {
      return voter.booth_name.trim();
    }
    const partNo = voter ? voter.part_no : null;
    if (partNo) {
      const select = document.getElementById('partFilter');
      // Only a booth dropdown is keyed by part_no. On a ward dropdown the option
      // values are ward numbers, so matching on part_no would read the wrong label.
      if (select && (select.dataset.filterKey || 'part_no') === 'part_no') {
        const opt = select.querySelector(`option[value="${partNo}"]`);
        if (opt && opt.textContent) {
          let txt = opt.textContent.trim();
          txt = txt.replace(/—\s*\d+\s*मतदाता.*$/, '').trim();
          const match = txt.match(/\(बूथ\s+([^)]+)\)/);
          if (match && match[1]) return match[1].trim();
          const parts = txt.split('—');
          if (parts.length > 1) return parts.slice(1).join('—').trim();
          return txt;
        }
      }
    }
    return 'राजकीय मतदान केंद्र';
  }

  // Helper: Extract unique 10-digit mobile numbers
  function extractPhones(voters) {
    const list = [];
    const seen = new Set();
    (voters || []).forEach(v => {
      const raw = (v.contact_phone || '').trim();
      if (!raw) return;
      const tokens = raw.split(/[,;\s/]+/);
      tokens.forEach(t => {
        let clean = t.replace(/\D/g, '');
        if (clean.startsWith('91') && clean.length === 12 && '6789'.includes(clean[2])) {
          clean = clean.slice(2);
        } else if (clean.startsWith('0') && clean.length === 11 && '6789'.includes(clean[1])) {
          clean = clean.slice(1);
        }
        if (clean.length === 10 && '6789'.includes(clean[0]) && !seen.has(clean)) {
          seen.add(clean);
          const name = (v.name_hi || v.name_en || '').trim();
          list.push({ phone: clean, name: name, sl_no: v.sl_no || v.serial_no });
        }
      });
    });
    return list;
  }

  // Helper: Check if voters belong to the same household
  function isSameHousehold(voters) {
    if (!voters || voters.length <= 1) return true;
    const clean = (val) => String(val || '').trim();
    const houses = voters.map(v => clean(v.house_no));
    if (houses.some(h => !h || h === '0' || h === '-' || h === 'None')) return false;
    if (new Set(houses).size !== 1) return false;

    const parts = voters.map(v => String(v.part_no || '')).filter(Boolean);
    if (new Set(parts).size > 1) return false;

    const isPanchayat = typeof window.isPanchayatCampaign === 'function' ? window.isPanchayatCampaign() : false;
    if (isPanchayat) {
      const wards = voters.map(v => String(v.ward_no || '')).filter(w => w !== '' && w !== '0' && w !== 'None');
      if (new Set(wards).size > 1) return false;
    }
    return true;
  }

  // Build Voter Summary Card HTML (Synchronous, 0ms)
  function buildVoterSummaryHtml(mode, voters, primaryVoter) {
    if ((mode === 'family' || mode === 'combined') && voters.length > 1) {
      const isFamily = mode === 'family' && isSameHousehold(voters);
      const primary = primaryVoter || voters[0];
      const boothName = getBoothName(primary);
      const houseNo = (primary.house_no && primary.house_no !== '0') ? primary.house_no : '-';
      const partNo = primary.part_no || 1;

      const isPanchayat = typeof window.isPanchayatCampaign === 'function' ? window.isPanchayatCampaign() : false;
      const uniqueWards = isPanchayat ? Array.from(new Set(voters.map(m => m.ward_no).filter(w => w && String(w).trim() !== '' && String(w) !== '0'))) : [];
      const uniqueParts = Array.from(new Set(voters.map(m => m.part_no).filter(Boolean)));

      return `
        <div class="bg-gradient-to-br from-white to-slate-50 border border-slate-200 rounded-2xl p-4 shadow-xs space-y-3">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              ${isFamily ? `
                <div class="flex items-center gap-1.5">
                  <span class="text-lg">👨‍👩‍👧</span>
                  <h4 class="font-black text-slate-900 text-sm sm:text-base leading-tight">
                    परिवार पर्ची (${voters.length} मतदाता)
                  </h4>
                </div>
                <div class="text-xs text-slate-600 mt-1">
                  <span class="font-bold text-slate-500">मुखिया / प्रथम:</span>
                  <span class="font-bold text-slate-900">${formatBilingual(primary.name_en, primary.name_hi)}</span>
                  <span class="text-slate-500 ml-2">🏠 मकान #${houseNo}</span>
                </div>
              ` : `
                <div class="flex items-center gap-1.5">
                  <span class="text-lg">📋</span>
                  <h4 class="font-black text-slate-900 text-sm sm:text-base leading-tight">
                    संयुक्त मतदाता पर्ची (${voters.length} मतदाता)
                  </h4>
                </div>
                <div class="text-xs text-slate-600 mt-1">
                  <span class="font-bold text-slate-500">चयनित:</span>
                  <span class="font-bold text-slate-900">${voters.length} मतदाता (विभिन्न मकान/परिवार)</span>
                </div>
              `}
            </div>
            <div class="flex items-center gap-1.5 flex-wrap justify-end shrink-0">
              ${uniqueWards.length > 0 ? `
                <span class="inline-flex items-center bg-indigo-100 text-indigo-950 font-black px-2.5 py-1 rounded-xl text-xs border border-indigo-300 shadow-2xs">
                  ${uniqueWards.length === 1 ? 'वार्ड ' + uniqueWards[0] : 'वार्ड ' + uniqueWards.join(', ')}
                </span>
              ` : ''}
              <span class="inline-flex items-center gap-1 bg-blue-100 text-blue-950 font-black px-2.5 py-1 rounded-xl text-xs border border-blue-300 shadow-2xs">
                <span>${uniqueParts.length > 1 ? uniqueParts.length + ' भाग' : 'भाग ' + (uniqueParts[0] || partNo)}</span>
                <span>·</span>
                <span>${voters.length} सदस्य</span>
              </span>
            </div>
          </div>

          <!-- Members compact list -->
          <div class="max-h-32 overflow-y-auto divide-y divide-slate-100 border border-slate-200 rounded-xl bg-white p-1">
            ${voters.map((m, idx) => `
              <div class="py-1 px-2 flex items-center justify-between text-xs hover:bg-slate-50 rounded">
                <div class="min-w-0 flex items-center gap-2 truncate mr-2">
                  <span class="font-bold text-slate-400 text-[10px] w-4">${idx + 1}.</span>
                  <span class="font-bold text-slate-900 truncate">${formatBilingual(m.name_en, m.name_hi)}</span>
                  <span class="text-[10px] text-slate-500">(${m.age || '-'} Y, ${m.gender === 'M' || m.gender === 'पुरुष' ? 'M' : 'F'})</span>
                </div>
                <div class="flex items-center gap-1.5 shrink-0">
                  ${(isPanchayat && m.ward_no && String(m.ward_no).trim() !== '' && String(m.ward_no) !== '0') ? `
                    <span class="text-[10px] bg-indigo-50 text-indigo-800 border border-indigo-200 px-1.5 py-0.5 rounded font-bold">वार्ड ${m.ward_no}</span>
                  ` : ''}
                  <span class="font-bold text-[10px] text-slate-600">क्र. ${m.sl_no || m.serial_no || '-'}</span>
                  ${m.contact_phone ? '<span class="text-emerald-600 text-xs" title="फोन उपलब्ध">📞</span>' : ''}
                </div>
              </div>
            `).join('')}
          </div>

          <!-- Polling Station -->
          ${uniqueParts.length > 1 ? `
            <div class="bg-blue-50/70 border border-blue-200/80 rounded-xl p-2.5 flex items-start gap-2">
              <span class="text-base shrink-0">🏢</span>
              <div class="min-w-0">
                <div class="text-[10px] uppercase font-black text-blue-900 tracking-wide">मतदान केंद्र विवरण (Polling Stations)</div>
                <div class="font-bold text-blue-950 text-xs mt-0.5 leading-snug">विभिन्न मतदान केंद्र (${uniqueParts.map(p => 'भाग ' + p).join(', ')})</div>
              </div>
            </div>
          ` : `
            <div class="bg-blue-50/70 border border-blue-200/80 rounded-xl p-2.5 flex items-start gap-2">
              <span class="text-base shrink-0">🏢</span>
              <div class="min-w-0">
                <div class="text-[10px] uppercase font-black text-blue-900 tracking-wide">मतदान केंद्र (Polling Station)</div>
                <div class="font-bold text-blue-950 text-xs mt-0.5 leading-snug">${boothName}</div>
              </div>
            </div>
          `}
        </div>
      `;
    }

    // Single Voter Mode
    const v = primaryVoter || (voters && voters[0]) || {};
    const nameDisplay = formatBilingual(v.name_en, v.name_hi);
    const relType = v.rel_type || v.relation_type || 'पिता/पति';
    const relDisplay = formatBilingual(v.rel_name_en || v.relative_name_en, v.rel_name_hi || v.relative_name_hi) || '-';
    const partNo = v.part_no || 1;
    const slNo = v.sl_no || v.serial_no || '-';
    const houseNo = (v.house_no && v.house_no !== '0') ? v.house_no : '-';
    const age = v.age ? `${v.age} वर्ष` : '-';
    const gender = (v.gender === 'M' || v.gender === 'पुरुष') ? 'पुरुष' : ((v.gender === 'F' || v.gender === 'महिला') ? 'महिला' : (v.gender || '-'));
    const epicNo = v.epic_no || '-';
    const boothName = getBoothName(v);
    const isPanchayat = typeof window.isPanchayatCampaign === 'function' ? window.isPanchayatCampaign() : false;
    const hasWard = Boolean(isPanchayat && v.ward_no && String(v.ward_no).trim() !== '' && String(v.ward_no) !== '0');

    return `
      <div class="bg-gradient-to-br from-white to-slate-50 border border-slate-200 rounded-2xl p-4 shadow-xs space-y-3">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h4 class="font-black text-slate-900 text-sm sm:text-base leading-tight">${nameDisplay}</h4>
            <div class="text-xs text-slate-600 mt-1 flex items-center gap-1.5 flex-wrap">
              <span class="font-bold text-slate-500">${relType}:</span>
              <span class="font-bold text-slate-800">${relDisplay}</span>
            </div>
          </div>
          <div class="flex items-center gap-1.5 flex-wrap justify-end shrink-0">
            ${hasWard ? `
              <span class="inline-flex items-center bg-indigo-100 text-indigo-950 font-black px-2.5 py-1 rounded-xl text-xs border border-indigo-300 shadow-2xs">
                वार्ड ${v.ward_no}
              </span>
            ` : ''}
            <span class="inline-flex items-center gap-1 bg-amber-100 text-amber-950 font-black px-2.5 py-1 rounded-xl text-xs border border-amber-300 shadow-2xs">
              <span>भाग ${partNo}</span>
              <span>·</span>
              <span>क्र. ${slNo}</span>
            </span>
          </div>
        </div>

        <div class="grid ${hasWard ? 'grid-cols-4' : 'grid-cols-3'} gap-2 pt-2 border-t border-slate-100 text-xs">
          ${hasWard ? `
            <div class="bg-white p-2 rounded-xl border border-indigo-100">
              <div class="text-[10px] uppercase font-bold text-indigo-600">वार्ड संख्या</div>
              <div class="font-black text-indigo-950 mt-0.5 truncate">वार्ड ${v.ward_no}</div>
            </div>
          ` : ''}
          <div class="bg-white p-2 rounded-xl border border-slate-100">
            <div class="text-[10px] uppercase font-bold text-slate-400">मकान नंबर</div>
            <div class="font-black text-slate-800 mt-0.5 truncate">${houseNo}</div>
          </div>
          <div class="bg-white p-2 rounded-xl border border-slate-100">
            <div class="text-[10px] uppercase font-bold text-slate-400">आयु / लिंग</div>
            <div class="font-black text-slate-800 mt-0.5 truncate">${age}, ${gender}</div>
          </div>
          <div class="bg-white p-2 rounded-xl border border-slate-100">
            <div class="text-[10px] uppercase font-bold text-slate-400">पहचान पत्र (EPIC)</div>
            <div class="font-mono font-black text-slate-900 mt-0.5 truncate">${epicNo}</div>
          </div>
        </div>

        <div class="bg-blue-50/70 border border-blue-200/80 rounded-xl p-2.5 flex items-start gap-2">
          <span class="text-base shrink-0">🏢</span>
          <div class="min-w-0">
            <div class="text-[10px] uppercase font-black text-blue-900 tracking-wide">मतदान केंद्र (Polling Station)</div>
            <div class="font-bold text-blue-950 text-xs mt-0.5 leading-snug">${boothName}</div>
          </div>
        </div>
      </div>
    `;
  }

  // Update phone pre-fill & clickable chips
  function updatePhoneSection(additionalPhones = []) {
    const phoneInput = document.getElementById('slipRecipientPhone');
    const phoneStatus = document.getElementById('slipPhoneStatus');
    const chipsContainer = document.getElementById('familyPhoneChipsContainer');
    const chipsList = document.getElementById('familyPhoneChipsList');

    // Collect phones from current mode voters
    let phones = extractPhones(currentSlipMode === 'single' ? [modalPrimaryVoter] : modalAllVoters);

    // Merge any additional phones discovered from server
    if (additionalPhones && additionalPhones.length > 0) {
      const existingSet = new Set(phones.map(p => p.phone));
      additionalPhones.forEach(ap => {
        const clean = String(ap.phone || ap).replace(/\D/g, '');
        if (clean.length === 10 && !existingSet.has(clean)) {
          existingSet.add(clean);
          phones.push({ phone: clean, name: ap.name || '', sl_no: ap.sl_no || '' });
        }
      });
    }

    // Pre-fill phone if input is empty
    if (phoneInput && (!phoneInput.value || phoneInput.value.trim() === '')) {
      if (phones.length > 0) {
        phoneInput.value = phones[0].phone;
        if (phoneStatus) {
          phoneStatus.textContent = `उपलब्ध नंबर स्वतः दर्ज (${phones[0].phone})`;
          phoneStatus.className = 'text-[11px] font-bold text-emerald-700';
        }
      } else {
        if (phoneStatus) phoneStatus.textContent = '';
      }
    }

    // Render chips
    if (chipsContainer && chipsList) {
      if (phones.length > 0) {
        chipsContainer.classList.remove('hidden');
        chipsList.innerHTML = phones.map(p => `
          <button 
            type="button" 
            onclick="selectSlipRecipientPhone('${p.phone}')" 
            class="inline-flex items-center gap-1.5 bg-white hover:bg-emerald-50 active:bg-emerald-100 border border-emerald-300 hover:border-emerald-500 text-emerald-950 font-mono font-bold text-xs px-2.5 py-1 rounded-lg shadow-2xs transition active:scale-95 cursor-pointer"
            title="${p.name ? p.name + ' का नंबर' : 'नंबर चुनें'}"
          >
            <span>📞</span>
            <span>${p.phone}</span>
            ${p.name ? `<span class="font-sans text-[10px] text-emerald-700 font-semibold truncate max-w-[120px]">(${p.name})</span>` : ''}
          </button>
        `).join('');
      } else {
        chipsContainer.classList.add('hidden');
        chipsList.innerHTML = '';
      }
    }
  }

  // Helper: Parse all valid 10-digit phone numbers from an input string
  function parseAllRecipientPhones(inputStr) {
    if (!inputStr) return [];
    const tokens = String(inputStr).split(/[,;\s\n/]+/);
    const phones = [];
    tokens.forEach(t => {
      let clean = t.replace(/\D/g, '');
      if (clean.startsWith('91') && clean.length === 12) clean = clean.slice(2);
      if (clean.startsWith('0') && clean.length === 11) clean = clean.slice(1);
      if (clean.length === 10 && '6789'.includes(clean[0]) && !phones.includes(clean)) {
        phones.push(clean);
      }
    });
    return phones;
  }

  // Tapping a phone chip toggles or appends slipRecipientPhone value
  window.selectSlipRecipientPhone = function (phone) {
    const input = document.getElementById('slipRecipientPhone');
    if (!input) return;
    let currentPhones = parseAllRecipientPhones(input.value);
    if (currentPhones.includes(phone)) {
      // Toggle off if already selected and more than 1
      currentPhones = currentPhones.filter(p => p !== phone);
    } else {
      currentPhones.push(phone);
    }
    input.value = currentPhones.join(', ');
    input.dispatchEvent(new Event('input'));

    const status = document.getElementById('slipPhoneStatus');
    if (status) {
      if (currentPhones.length > 1) {
        status.textContent = `चयनित: ${currentPhones.length} नंबर (${currentPhones.join(', ')})`;
        status.className = 'text-[11px] font-bold text-emerald-700';
      } else if (currentPhones.length === 1) {
        status.textContent = `चयनित: ${currentPhones[0]}`;
        status.className = 'text-[11px] font-bold text-emerald-700';
      } else {
        status.textContent = '';
      }
    }
    if (window.triggerHaptic) window.triggerHaptic('light');
  };

  // Wire real-time digits validation watcher on phone input
  function attachPhoneWatcher() {
    const input = document.getElementById('slipRecipientPhone');
    if (!input || input.dataset.watcherActive) return;
    input.dataset.watcherActive = 'true';

    input.addEventListener('input', () => {
      const parsed = parseAllRecipientPhones(input.value);
      const status = document.getElementById('slipPhoneStatus');
      if (status) {
        if (parsed.length > 1) {
          status.textContent = `✓ ${parsed.length} वैध नंबर तैयार`;
          status.className = 'text-[11px] font-bold text-emerald-700';
        } else if (parsed.length === 1) {
          status.textContent = `✓ 1 नंबर पूर्ण (${parsed[0]})`;
          status.className = 'text-[11px] font-bold text-emerald-700';
        } else {
          const rawDigits = input.value.replace(/\D/g, '');
          if (rawDigits.length > 0 && rawDigits.length < 10) {
            status.textContent = `${10 - rawDigits.length} अंक शेष`;
            status.className = 'text-[11px] font-bold text-amber-700';
          } else if (rawDigits.length >= 10 && !'6789'.includes(rawDigits[0])) {
            status.textContent = 'अमान्य मोबाइल नंबर (6, 7, 8 या 9 से शुरू होना चाहिए)';
            status.className = 'text-[11px] font-bold text-rose-600';
          } else {
            status.textContent = '';
          }
        }
      }
    });
  }

  // Fetch slip text in background for WhatsApp Web fallback
  function fetchSlipTextInBackground() {
    const vIds = currentSlipVoters.map(v => v.id || v.voter_id);
    if (vIds.length === 0) return;

    if (textFetchAbort) {
      textFetchAbort.abort();
    }
    textFetchAbort = new AbortController();

    const textUrl = vIds.length === 1 ? `/api/slip_text?id=${vIds[0]}` : `/api/slip_text?family_ids=${vIds.join(',')}`;

    fetch(textUrl, { signal: textFetchAbort.signal })
      .then(res => res.json())
      .then(data => {
        currentSlipText = data.slip_text || '';
        const preview = document.getElementById('slipTextPreview');
        if (preview) preview.textContent = currentSlipText;

        const serverVoters = data.voters || (data.voter ? [data.voter] : []);
        if (serverVoters.length > 0) {
          const vIdSet = new Set(vIds.map(x => String(x)));
          const matchedServerVoters = serverVoters.filter(v => vIdSet.has(String(v.id || v.voter_id)));
          if (matchedServerVoters.length > 0) {
            modalAllVoters = modalAllVoters.map(orig => {
              const oId = String(orig.id || orig.voter_id);
              const found = matchedServerVoters.find(sv => String(sv.id || sv.voter_id) === oId);
              return found || orig;
            });
            modalPrimaryVoter = modalAllVoters[0];
            currentSlipVoters = currentSlipMode === 'single' ? [modalPrimaryVoter] : modalAllVoters;
            const summaryCard = document.getElementById('slipVoterSummaryCard');
            if (summaryCard) {
              summaryCard.innerHTML = buildVoterSummaryHtml(currentSlipMode, modalAllVoters, modalPrimaryVoter);
            }
          }
        }
        const serverPhones = extractPhones(serverVoters);
        if (serverPhones.length > 0) {
          updatePhoneSection(serverPhones);
        }
      })
      .catch(err => {
        if (err.name !== 'AbortError') console.warn('Background slip text fetch:', err);
      });

    // Also check slip_card_info for contact phones
    const infoUrl = vIds.length === 1 ? `/api/slip_card_info?id=${vIds[0]}` : `/api/slip_card_info?family_ids=${vIds.join(',')}`;
    fetch(infoUrl)
      .then(res => res.json())
      .then(info => {
        if (info.family_phones && info.family_phones.length > 0) {
          updatePhoneSection(info.family_phones);
        }
      })
      .catch(err => {
        console.warn('Background card info fetch:', err);
      });
  }

  // Mode Toggle Controller
  window.setSlipModalMode = function (targetMode) {
    currentSlipMode = targetMode;
    const singleBtn = document.getElementById('slipModeSingleBtn');
    const familyBtn = document.getElementById('slipModeFamilyBtn');

    if (targetMode === 'single') {
      currentSlipMode = 'single';
      if (singleBtn) {
        singleBtn.className = 'flex-1 py-1.5 px-3 rounded-lg font-bold text-center transition flex items-center justify-center gap-1.5 cursor-pointer bg-white text-blue-700 shadow-xs';
      }
      if (familyBtn) {
        familyBtn.className = 'flex-1 py-1.5 px-3 rounded-lg font-bold text-center transition flex items-center justify-center gap-1.5 cursor-pointer text-slate-600 hover:text-slate-900';
      }
      currentSlipVoters = [modalPrimaryVoter];
    } else {
      const sameHouse = isSameHousehold(modalAllVoters);
      currentSlipMode = sameHouse ? 'family' : 'combined';
      if (familyBtn) {
        familyBtn.className = 'flex-1 py-1.5 px-3 rounded-lg font-bold text-center transition flex items-center justify-center gap-1.5 cursor-pointer bg-white text-blue-700 shadow-xs';
      }
      if (singleBtn) {
        singleBtn.className = 'flex-1 py-1.5 px-3 rounded-lg font-bold text-center transition flex items-center justify-center gap-1.5 cursor-pointer text-slate-600 hover:text-slate-900';
      }
      currentSlipVoters = modalAllVoters;
    }

    // Re-render summary card
    const summaryCard = document.getElementById('slipVoterSummaryCard');
    if (summaryCard) {
      summaryCard.innerHTML = buildVoterSummaryHtml(currentSlipMode, modalAllVoters, modalPrimaryVoter);
    }

    // Refresh phone numbers
    updatePhoneSection();

    // Trigger background text fetch
    fetchSlipTextInBackground();

    // Pre-warm slip for newly selected mode
    triggerSlipPrewarm(currentSlipVoters, currentSlipMode);
  };

  // Pre-warm digital card & Meta media in background (non-blocking)
  function triggerSlipPrewarm(voters, slipMode) {
    if (!voters || voters.length === 0) return;
    const vIds = voters.map(v => v.id || v.voter_id).filter(Boolean);
    if (vIds.length === 0) return;
    const tenant = (window.APP_STATE && window.APP_STATE.tenant) || new URLSearchParams(window.location.search).get('tenant') || '';
    const slipType = (slipMode === 'family') ? 'Family' : ((slipMode === 'combined') ? 'Combined' : 'Single');

    fetch('/api/prewarm_slip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        voter_ids: vIds,
        slip_type: slipType,
        tenant: tenant
      })
    }).then(res => res.json()).then(data => {
      if (data && data.prewarm && data.prewarm.status === 'started') {
        console.debug('⚡ [Prewarm] Slip card & Meta media pre-upload started');
      }
    }).catch(err => {
      // Non-blocking fire-and-forget
      console.debug('[Prewarm] Notice:', err);
    });
  }

  // Open Slip Modal Synchronously (0ms)
  window.openSlipModal = async function (voterIds, singleVoter = null) {
    const modal = document.getElementById('slipPreviewModal');
    if (!modal) return;

    // 1. Open modal immediately (0ms, no network blocking)
    modal.classList.remove('hidden');
    attachPhoneWatcher();

    // Reset phone input & status
    const phoneInput = document.getElementById('slipRecipientPhone');
    if (phoneInput) phoneInput.value = '';
    const phoneStatus = document.getElementById('slipPhoneStatus');
    if (phoneStatus) phoneStatus.textContent = '';

    // 2. Resolve voters from memory
    const ids = (voterIds || []).map(id => parseInt(id, 10)).filter(Boolean);
    let resolved = [];
    ids.forEach(id => {
      let v = (window.APP_STATE && window.APP_STATE.selectedVoters) ? window.APP_STATE.selectedVoters.get(id) : null;
      if (!v && singleVoter && (singleVoter.id === id || singleVoter.voter_id === id)) {
        v = singleVoter;
      }
      if (!v && window.APP_STATE && window.APP_STATE.currentResults) {
        v = window.APP_STATE.currentResults.find(x => (x.id || x.voter_id) === id);
      }
      if (!v && window.currentResults) {
        v = window.currentResults.find(x => (x.id || x.voter_id) === id);
      }
      if (v) resolved.push(v);
    });

    if (resolved.length === 0 && singleVoter) {
      resolved = [singleVoter];
    }
    if (resolved.length === 0) {
      resolved = ids.map(id => ({ id: id, sl_no: id, name_hi: 'मतदाता', part_no: 1 }));
    }

    // Struck-off electors (विलोपन) get no slip — sending them to a booth that will
    // turn them away is worse than saying so up front. /api/slip_text enforces this
    // server-side too; this is just the friendly early exit.
    const isStruck = (v) => String(v && v.status || '').trim().toLowerCase() === 'deleted';
    const struckOut = resolved.filter(isStruck);
    resolved = resolved.filter(v => !isStruck(v));
    if (resolved.length === 0) {
      modal.classList.add('hidden');
      if (window.showToast) {
        window.showToast(
          'यह मतदाता सूची से विलोपित है — पर्ची जारी नहीं की जा सकती (struck off the roll)',
          'error', 5000
        );
      }
      return;
    }
    if (struckOut.length && window.showToast) {
      window.showToast(
        `${struckOut.length} विलोपित मतदाता पर्ची से हटाए गए (${struckOut.length} struck-off excluded)`,
        'warning', 4000
      );
    }

    modalAllVoters = resolved;
    modalPrimaryVoter = singleVoter || resolved[0];

    // 3. Configure Mode Toggle
    const modeToggle = document.getElementById('slipModeToggleContainer');
    const familyLabel = document.getElementById('slipModeFamilyLabel');
    const sameHouse = isSameHousehold(resolved);
    if (resolved.length >= 2) {
      if (modeToggle) modeToggle.classList.remove('hidden');
      if (sameHouse) {
        if (familyLabel) familyLabel.textContent = `परिवार पर्ची (${resolved.length})`;
        currentSlipMode = singleVoter ? 'single' : 'family';
      } else {
        if (familyLabel) familyLabel.textContent = `संयुक्त पर्ची (${resolved.length})`;
        currentSlipMode = singleVoter ? 'single' : 'combined';
      }
    } else {
      if (modeToggle) modeToggle.classList.add('hidden');
      currentSlipMode = 'single';
    }

    // Update toggle button styles
    const singleBtn = document.getElementById('slipModeSingleBtn');
    const familyBtn = document.getElementById('slipModeFamilyBtn');
    if (currentSlipMode === 'single') {
      if (singleBtn) singleBtn.className = 'flex-1 py-1.5 px-3 rounded-lg font-bold text-center transition flex items-center justify-center gap-1.5 cursor-pointer bg-white text-blue-700 shadow-xs';
      if (familyBtn) familyBtn.className = 'flex-1 py-1.5 px-3 rounded-lg font-bold text-center transition flex items-center justify-center gap-1.5 cursor-pointer text-slate-600 hover:text-slate-900';
      currentSlipVoters = [modalPrimaryVoter];
    } else {
      if (familyBtn) familyBtn.className = 'flex-1 py-1.5 px-3 rounded-lg font-bold text-center transition flex items-center justify-center gap-1.5 cursor-pointer bg-white text-blue-700 shadow-xs';
      if (singleBtn) singleBtn.className = 'flex-1 py-1.5 px-3 rounded-lg font-bold text-center transition flex items-center justify-center gap-1.5 cursor-pointer text-slate-600 hover:text-slate-900';
      currentSlipVoters = modalAllVoters;
    }

    // 4. Render Summary Card Synchronously
    const summaryCard = document.getElementById('slipVoterSummaryCard');
    if (summaryCard) {
      summaryCard.innerHTML = buildVoterSummaryHtml(currentSlipMode, modalAllVoters, modalPrimaryVoter);
    }

    // 5. Pre-fill phone and chips
    updatePhoneSection();

    // 6. Fetch slip text in the background (non-blocking)
    fetchSlipTextInBackground();

    // 7. Pre-warm slip card & Meta media in background (non-blocking)
    triggerSlipPrewarm(currentSlipVoters, currentSlipMode);
  };

  window.openSlipModalForVoter = async function (voterId) {
    let voter = window.APP_STATE.selectedVoters.get(voterId) || null;
    if (!voter && window.APP_STATE.currentResults) {
      voter = window.APP_STATE.currentResults.find(v => (v.id || v.voter_id) === voterId);
    }
    if (voter && String(voter.status || '').trim().toLowerCase() === 'deleted') {
      if (typeof window.showToast === 'function') {
        window.showToast('विलोपित मतदाता — पर्ची जारी नहीं की जा सकती', 'warning');
      } else {
        alert('विलोपित मतदाता — पर्ची जारी नहीं की जा सकती');
      }
      return;
    }
    openSlipModal([voterId], voter);
  };

  window.openMultiSlipModal = function () {
    const ids = Array.from(window.APP_STATE.selectedVoters.keys());
    if (ids.length === 0) {
      window.showToast('कृपया पहले कम से कम एक मतदाता चुनें', 'warning');
      return;
    }
    openSlipModal(ids);
  };

  window.openSlipForSelected = function () {
    const ids = Array.from(window.APP_STATE.selectedVoters.keys());
    if (ids.length === 0) {
      window.showToast('कृपया पहले कम से कम एक मतदाता चुनें', 'warning');
      return;
    }
    openSlipModal(ids);
  };

  window.closeSlipModal = function () {
    const modal = document.getElementById('slipPreviewModal');
    if (modal) modal.classList.add('hidden');
    if (textFetchAbort) {
      textFetchAbort.abort();
    }
  };

  // Dispatch via Official Meta WhatsApp Cloud API
  window.dispatchSlipViaMeta = async function () {
    const phoneInput = document.getElementById('slipRecipientPhone');
    const rawVal = (phoneInput ? phoneInput.value : '').trim();
    const cleanPhones = parseAllRecipientPhones(rawVal);

    if (cleanPhones.length === 0) {
      window.showToast('कृपया कम से कम एक 10-अंकों का वैध मोबाइल नंबर दर्ज करें (उदा: 9829012345)', 'warning');
      if (phoneInput) phoneInput.focus();
      return;
    }

    const sendBtn = document.getElementById('btnMetaSendSlip');
    const originalBtnHtml = sendBtn ? sendBtn.innerHTML : '';
    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.innerHTML = `<span>⏳ भेजा जा रहा है (${cleanPhones.length} प्राप्तकर्ता)...</span>`;
    }

    try {
      const vIds = currentSlipVoters.map(v => v.id || v.voter_id);
      let slipType = 'Single';
      if (vIds.length > 1) {
        slipType = isSameHousehold(currentSlipVoters) ? 'Family' : 'Combined';
      }

      const res = await fetch('/api/send_openclaw', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: cleanPhones[0],
          phones: cleanPhones,
          voter_ids: vIds,
          slip_type: slipType
        })
      });

      const data = await res.json();
      if (data.success) {
        if (window.triggerHaptic) window.triggerHaptic('success');
        if (window.playAudioTone) window.playAudioTone(1200, 0.15);
        const countMsg = cleanPhones.length > 1 ? `${cleanPhones.length} नंबरों पर` : 'व्हाट्सएप पर्ची';
        window.showToast(`✅ ${countMsg} सफलतापूर्वक भेज दी गई! (Sent via Meta Cloud API)`, 'success', 3500);
        window.closeSlipModal();
        if (typeof window.clearVoterSelection === 'function') {
          window.clearVoterSelection();
        }
      } else {
        if (window.triggerHaptic) window.triggerHaptic('warning');
        window.showToast(`त्रुटि: ${data.error || 'पर्ची नहीं भेजी जा सकी'}`, 'error', 4000);
      }
    } catch (err) {
      console.warn('Network dispatch failed, checking offline outbox queue...', err);

      // Graceful Offline WhatsApp Queueing
      if (window.SmartBoothOfflineDB) {
        try {
          const vIds = currentSlipVoters.map(v => v.id || v.voter_id);
          let slipType = 'Single';
          if (vIds.length > 1) {
            slipType = isSameHousehold(currentSlipVoters) ? 'Family' : 'Combined';
          }

          const outboxItem = {
            id: 'wa_' + Date.now() + '_' + (vIds[0] || 'v'),
            type: 'WHATSAPP_DISPATCH',
            phones: cleanPhones,
            voter_ids: vIds,
            slip_type: slipType,
            timestamp: Date.now()
          };

          await window.SmartBoothOfflineDB.queueRawMutation(outboxItem);
          if (window.triggerHaptic) window.triggerHaptic('success');
          window.showToast?.('📲 इंटरनेट बंद है: पर्ची आउटबॉक्स में सुरक्षित है, नेटवर्क आते ही अपने आप WhatsApp पर भेज दी जाएगी!', 'info', 5000);
          window.closeSlipModal();
          if (typeof window.clearVoterSelection === 'function') {
            window.clearVoterSelection();
          }
          return;
        } catch (dbErr) {
          console.error('Failed to queue offline WhatsApp slip:', dbErr);
        }
      }

      window.showToast('नेटवर्क त्रुटि: इंटरनेट बंद है। आप ब्लूटूथ थर्मल प्रिंटर से पर्ची प्रिंट कर सकते हैं।', 'warning', 4500);
    } finally {
      if (sendBtn) {
        sendBtn.disabled = false;
        sendBtn.innerHTML = originalBtnHtml || `<span>🗳️</span><span>WhatsApp पर तुरंत भेजें (Auto Card)</span>`;
      }
    }
  };

  // Dispatch via 1-Click WhatsApp Link (Direct WhatsApp Web / App & Hybrid Image Sharing)
  window.dispatchSlipViaWaLink = async function () {
    const phoneInput = document.getElementById('slipRecipientPhone') || document.getElementById('mobileWaPhoneInput');
    const rawVal = (phoneInput ? phoneInput.value : '').trim();
    const cleanPhones = parseAllRecipientPhones(rawVal);
    const primaryPhone = cleanPhones.length > 0 ? cleanPhones[0] : '';

    if (!currentSlipText) {
      const vIds = (currentSlipVoters && currentSlipVoters.length > 0)
        ? currentSlipVoters.map(v => v.id || v.voter_id)
        : (window.APP_STATE && window.APP_STATE.selectedVoters)
          ? Array.from(window.APP_STATE.selectedVoters.keys())
          : [];
      if (vIds.length > 0) {
        try {
          const url = vIds.length === 1 ? `/api/slip_text?id=${vIds[0]}` : `/api/slip_text?family_ids=${vIds.join(',')}`;
          const res = await fetch(url);
          const data = await res.json();
          currentSlipText = data.slip_text || '';
        } catch (e) {
          console.warn('Error fetching slip text for WhatsApp Web:', e);
        }
      }
    }

    if (cleanPhones.length > 1) {
      if (window.showToast) {
        window.showToast(`WhatsApp Web प्रथम नंबर (${primaryPhone}) के लिए खोला जा रहा है`, 'info', 3000);
      }
    }

    // Resolve voter IDs and card image URL for hybrid sharing
    const vIds = (currentSlipVoters && currentSlipVoters.length > 0)
      ? currentSlipVoters.map(v => v.id || v.voter_id)
      : (window.APP_STATE && window.APP_STATE.selectedVoters)
        ? Array.from(window.APP_STATE.selectedVoters.keys())
        : [];
    const imgParam = vIds.length === 1 ? `id=${vIds[0]}` : `family_ids=${vIds.join(',')}&page=1`;
    const imgUrl = vIds.length > 0 ? `/api/slip_card_image?${imgParam}` : null;

    const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    const targetPhone = primaryPhone ? (primaryPhone.startsWith('91') ? primaryPhone : `91${primaryPhone}`) : '';
    const encodedText = encodeURIComponent(currentSlipText);

    // Hybrid Step 1: Mobile Web Share API (attaches actual image card + text)
    if (isMobile && navigator.canShare && imgUrl) {
      try {
        const imgBlob = await fetch(imgUrl).then(r => r.blob());
        const imgFile = new File([imgBlob], 'voter_slip.png', { type: 'image/png' });
        if (navigator.canShare({ files: [imgFile] })) {
          await navigator.share({
            files: [imgFile],
            title: 'मतदाता सूचना पर्ची',
            text: currentSlipText
          });
          window.closeSlipModal();
          window.closeMobileWhatsAppModal?.();
          return;
        }
      } catch (shareErr) {
        if (shareErr.name === 'AbortError') return;
        console.warn('Web Share API fallback:', shareErr);
      }
    }

    // Hybrid Step 2: Desktop Clipboard Copy (PNG image blob for 1-click Ctrl+V in WhatsApp Web)
    if (!isMobile && navigator.clipboard && window.ClipboardItem && imgUrl) {
      try {
        const imgBlob = await fetch(imgUrl).then(r => r.blob());
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': imgBlob })]);
        if (window.showToast) {
          window.showToast('📋 पर्ची कार्ड क्लिपबोर्ड में कॉपी हो गया! WhatsApp Web खुलने पर Ctrl+V दबाएं', 'info', 6000);
        }
      } catch (clipErr) {
        console.warn('Clipboard image write:', clipErr);
      }
    }

    // Hybrid Step 3: Direct WhatsApp URL (Desktop: web.whatsapp.com directly; Mobile: api.whatsapp.com)
    // Eliminates intermediate wa.me redirect that mangles Devanagari Hindi and 4-byte emojis
    let waUrl;
    if (isMobile) {
      waUrl = targetPhone 
        ? `https://api.whatsapp.com/send?phone=${targetPhone}&text=${encodedText}`
        : `https://api.whatsapp.com/send?text=${encodedText}`;
    } else {
      waUrl = targetPhone 
        ? `https://web.whatsapp.com/send?phone=${targetPhone}&text=${encodedText}`
        : `https://web.whatsapp.com/send?text=${encodedText}`;
    }

    window.open(waUrl, '_blank');
    window.closeSlipModal();
    window.closeMobileWhatsAppModal?.();
  };

  // Missing Modal Controller Handlers
  window.closeMobileWhatsAppModal = function () {
    const modal = document.getElementById('mobileWhatsAppModal');
    if (modal) modal.classList.add('hidden');
    document.body.style.overflow = '';
  };

  window.executeDirectWhatsAppWebFromModal = function () {
    const phoneInput = document.getElementById('mobileWaPhoneInput');
    const slipPhoneInput = document.getElementById('slipRecipientPhone');
    if (phoneInput && slipPhoneInput && phoneInput.value) {
      slipPhoneInput.value = phoneInput.value;
    }
    window.dispatchSlipViaWaLink();
  };

  window.executeMobileWhatsAppSend = function () {
    const phoneInput = document.getElementById('mobileWaPhoneInput');
    const slipPhoneInput = document.getElementById('slipRecipientPhone');
    if (phoneInput && slipPhoneInput && phoneInput.value) {
      slipPhoneInput.value = phoneInput.value;
    }
    window.dispatchSlipViaMeta();
  };

  window.openMobileMultiWhatsAppModal = function () {
    const modal = document.getElementById('mobileWhatsAppModal');
    if (modal) {
      modal.classList.remove('hidden');
      document.body.style.overflow = 'hidden';
      const phoneInput = document.getElementById('mobileWaPhoneInput');
      if (phoneInput) setTimeout(() => phoneInput.focus(), 100);
    }
  };

  // Alias for templates referencing dispatchSlipViaWaMe
  window.dispatchSlipViaWaMe = window.dispatchSlipViaWaLink;

  // Print Slip Card or Text
  // Print Slip Card: A4 10-Up Cut-Sheet Layout (100% Legal, No Candidate Branding)
  window.printSlipCard = function () {
    let vIds = [];
    if (currentSlipVoters && currentSlipVoters.length > 0) {
      vIds = currentSlipVoters.map(v => v.id || v.voter_id);
    } else if (modalPrimaryVoter) {
      vIds = [modalPrimaryVoter.id || modalPrimaryVoter.voter_id];
    } else if (modalAllVoters && modalAllVoters.length > 0) {
      vIds = modalAllVoters.map(v => v.id || v.voter_id);
    } else if (window.APP_STATE && window.APP_STATE.selectedVoters && window.APP_STATE.selectedVoters.size > 0) {
      vIds = Array.from(window.APP_STATE.selectedVoters.keys());
    }

    if (vIds.length > 0) {
      const url = `/api/print_slips_10up?voter_ids=${vIds.join(',')}`;
      const win = window.open(url, '_blank');
      if (win) {
        win.focus();
        return;
      }
    }
    if (currentSlipText) {
      const win = window.open('', '_blank');
      if (win) {
        win.document.write(`<html><head><title>मतदाता पर्ची प्रिंट</title><style>body{font-family:sans-serif;padding:24px;white-space:pre-wrap;font-size:14px;line-height:1.6;}</style></head><body><pre>${currentSlipText}</pre><script>window.print();window.close();<\/script></body></html>`);
        win.document.close();
        return;
      }
    }
    window.print();
  };
  // Expose current slip voters getter for thermal printer
  window.getCurrentSlipVoters = function () {
    if (currentSlipVoters && currentSlipVoters.length > 0) return currentSlipVoters;
    if (modalPrimaryVoter) return [modalPrimaryVoter];
    if (modalAllVoters && modalAllVoters.length > 0) return modalAllVoters;
    return [];
  };

  // 58mm Portable Thermal Printer Dispatcher (HOP-HL58)
  window.thermalPrintCurrentSlip = function () {
    let targets = window.getCurrentSlipVoters();
    if (!targets || targets.length === 0) {
      if (window.showToast) window.showToast('प्रिंट करने के लिए कोई मतदाता नहीं है', 'error');
      return;
    }

    const boothName = getBoothName(targets[0]);
    if (window.ThermalPrinter) {
      window.ThermalPrinter.print(targets, boothName);
    } else {
      console.warn('ThermalPrinter module not available');
    }
  };

  window.thermalPrintCurrentSlipBrowser = function () {
    let targets = currentSlipVoters;
    if (!targets || targets.length === 0) {
      if (modalPrimaryVoter) targets = [modalPrimaryVoter];
      else if (modalAllVoters && modalAllVoters.length > 0) targets = modalAllVoters;
    }
    if (!targets || targets.length === 0) return;
    const boothName = getBoothName(targets[0]);
    if (window.ThermalPrinter) {
      window.ThermalPrinter.printBrowserFallback(targets, boothName);
    }
  };

  // Copy Slip Text to Clipboard
  window.copySlipText = function () {
    if (!currentSlipText) return;
    navigator.clipboard.writeText(currentSlipText).then(() => {
      window.showToast('📋 पर्ची का संदेश कॉपी हो गया!', 'success');
    });
  };

  // Smart Family Discovery Modal
  let currentFamilyDirect = [];
  let currentFamilyRelated = [];
  let currentFamilyMembers = [];

  const openFamilySmartModalHandler = async function (voterId) {
    if (typeof window.filterByFamilyForSelected === 'function') {
      return window.filterByFamilyForSelected(voterId ? [voterId] : null);
    }
    if (!voterId) return;
    const modal = document.getElementById('familySmartModal');
    if (!modal) return;
    modal.classList.remove('hidden');

    const listContainer = document.getElementById('smartFamilyMembersList') || document.getElementById('familySmartList');
    if (listContainer) listContainer.innerHTML = '<p class="text-xs text-slate-400 py-6 text-center">परिवार के सदस्यों की खोज जारी है...</p>';

    currentFamilyDirect = [];
    currentFamilyRelated = [];
    currentFamilyMembers = [];

    try {
      const tenant = (window.APP_STATE && window.APP_STATE.tenant) || new URLSearchParams(window.location.search).get('tenant') || '';
      const url = `/api/family_smart?voter_id=${encodeURIComponent(voterId)}${tenant ? `&tenant=${encodeURIComponent(tenant)}` : ''}`;
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`Server returned ${res.status}: ${res.statusText}`);
      }
      const data = await res.json();
      const direct = data.direct || [];
      const related = data.related || [];

      currentFamilyDirect = direct;
      currentFamilyRelated = related;
      currentFamilyMembers = [...direct, ...related];

      let html = '';
      if (direct.length > 0) {
        const famName = direct.find(m => m.family_head_name)?.family_head_name;
        const famLabel = famName ? ` (${famName})` : '';
        html += `<h5 class="text-xs font-bold text-slate-700 uppercase mb-2">🏠 एक ही मकान / परिवार के सदस्य${famLabel} (${direct.length})</h5>`;
        html += `<div class="space-y-1.5 mb-4">${renderFamilyList(direct)}</div>`;
      }

      if (related.length > 0) {
        html += `<h5 class="text-xs font-bold text-amber-700 uppercase mb-2">🔗 संभावित नजदीकी शाखाएं (${related.length})</h5>`;
        html += `<div class="space-y-1.5">${renderFamilyList(related, true)}</div>`;
      }

      if (listContainer) listContainer.innerHTML = html || '<p class="text-xs text-slate-400 py-6 text-center">परिवार के कोई अन्य सदस्य नहीं मिले</p>';
    } catch (err) {
      console.error('Family smart error:', err);
      if (listContainer) listContainer.innerHTML = '<p class="text-xs text-rose-500 py-6 text-center">परिवार लोड करने में त्रुटि</p>';
    }
  };

  if (!window.openFamilyModalForVoter) {
    window.openFamilyModalForVoter = openFamilySmartModalHandler;
  }
  window.openFamilySmartModal = openFamilySmartModalHandler;

  function renderFamilyList(members, isRelated = false) {
    return members.map(m => `
      <div class="flex items-center justify-between p-2 rounded-lg bg-slate-50 border border-slate-200 text-xs">
        <div>
          <span class="font-bold text-slate-900">${m.name_en || m.name_hi}</span>
          <span class="text-slate-500">(${m.age} वर्ष · ${m.gender}) · क्र. ${m.sl_no}</span>
          ${isRelated ? `<span class="block text-[10px] text-amber-700 font-medium">${m.relation_reason || ''}</span>` : ''}
        </div>
        <button onclick="addFamilyMemberToSelection(${m.id || m.voter_id})" class="text-xs bg-blue-600 hover:bg-blue-500 text-white font-bold px-2 py-1 rounded cursor-pointer">
          + जोड़ें
        </button>
      </div>
    `).join('');
  }

  window.addFamilyMemberToSelection = function (voterId) {
    if (typeof window.toggleVoterSelection === 'function') {
      window.toggleVoterSelection(voterId, true);
      window.showToast('सदस्य चयन में जोड़ दिया गया', 'info', 1500);
    }
  };

  window.closeFamilySmartModal = window.closeFamilyModal = function () {
    const modal = document.getElementById('familySmartModal');
    if (modal) modal.classList.add('hidden');
  };

  window.sendFamilyCombinedSlip = function () {
    const directMembers = currentFamilyDirect || [];
    if (!directMembers || directMembers.length === 0) {
      window.showToast('परिवार के कोई प्रत्यक्ष सदस्य उपलब्ध नहीं हैं', 'warning');
      return;
    }
    const ids = directMembers.map(m => m.id || m.voter_id);
    window.closeFamilySmartModal();
    openSlipModal(ids);
  };

})();
