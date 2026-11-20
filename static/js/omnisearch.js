/**
 * omnisearch.js - Smart Omnibar & Two-Stage Search Controller
 * Part of SmartBooth Platform
 */

(function () {
  'use strict';

  let searchTimeout = null;
  let currentSearchAbort = null;
  let currentSearchSeq = 0;
  const searchCache = new Map();
  let activeVoiceSession = null;
  let activeVoiceField = 'name'; // 'name' or 'relative'

  function getTargetSearchInput(field) {
    if (field === 'relative') {
      return document.getElementById('relativeSearchInput');
    }
    if (field === 'family') {
      return document.getElementById('familySearchInput');
    }
    return document.getElementById('nameSearchInput') || document.getElementById('omnisearchInput') || document.getElementById('searchName') || document.getElementById('familySearchInput');
  }

  function getTargetVoiceButton(field) {
    if (field === 'relative') {
      return document.getElementById('voiceRelativeSearchBtn');
    }
    if (field === 'family') {
      return document.getElementById('voiceFamilySearchBtn');
    }
    return document.getElementById('voiceSearchBtn') || document.getElementById('voiceFamilySearchBtn');
  }

  window.toggleVoiceSearch = function (field = 'name', evt = null) {
    if (evt && typeof evt.stopPropagation === 'function') {
      evt.stopPropagation();
    }

    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || 
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

    // 1. Check if browser supports SpeechRecognition
    const SpeechAPI = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechAPI) {
      if (window.showToast) {
        if (isIOS) {
          window.showToast('iPhone पर: Safari उपयोग करें व Settings > General > Keyboard में Dictation ऑन रखें', 'error', 5000);
        } else {
          window.showToast('वॉइस सर्च आपके ब्राउज़र में समर्थित नहीं है (Web Speech API not supported)', 'error', 3500);
        }
      }
      return;
    }

    // 2. Check for Secure Context (HTTPS or localhost)
    const isLocalhost = Boolean(
      window.location.hostname === 'localhost' ||
      window.location.hostname === '127.0.0.1' ||
      window.location.hostname === '[::1]'
    );
    if (!window.isSecureContext && !isLocalhost) {
      if (window.showToast) {
        window.showToast('⚠️ वॉइस सर्च के लिए HTTPS आवश्यक है (Please access via https://)', 'warning', 4000);
      }
      return;
    }

    // 3. If currently listening, stop session
    if (activeVoiceSession) {
      stopVoiceSearch();
      return;
    }

    // 4. Start fresh recognition session
    activeVoiceField = field;
    const targetInput = getTargetSearchInput(field);
    const micBtn = getTargetVoiceButton(field);
    const originalPlaceholder = targetInput ? targetInput.placeholder : '';

    let recognition;
    try {
      recognition = new SpeechAPI();
    } catch (err) {
      console.error('Failed to construct SpeechRecognition:', err);
      if (window.showToast) {
        window.showToast('वॉइस शुरू करने में त्रुटि: ' + (err.message || err), 'error', 3000);
      }
      return;
    }

    recognition.continuous = false;
    // On iOS WebKit, interimResults MUST be true to keep audio stream alive and buffer text
    recognition.interimResults = true;
    recognition.lang = 'hi-IN';
    recognition.maxAlternatives = 1;

    let capturedTranscript = '';
    let hasCommitted = false;

    function commitResult(text) {
      if (hasCommitted || !text) return;
      hasCommitted = true;
      const input = getTargetSearchInput(activeVoiceField);
      if (input) {
        input.value = text;
        input.removeAttribute('inputmode');
        input.readOnly = false;
        if (window.triggerHaptic) window.triggerHaptic('success');
        if (window.showToast) window.showToast(`🎙️ "${text}"`, 'info', 2500);
        if (typeof loadFamilies === 'function' && (activeVoiceField === 'family' || input.id === 'familySearchInput')) {
          loadFamilies();
        } else if (typeof triggerOmniSearch === 'function') {
          triggerOmniSearch(true);
        }
      }
    }

    recognition.onstart = function () {
      if (micBtn) {
        micBtn.classList.remove('text-slate-400');
        micBtn.classList.add('bg-rose-500', 'text-white', 'animate-pulse', 'scale-110');
      }
      if (targetInput) {
        targetInput.dataset.origPlaceholder = originalPlaceholder;
        targetInput.placeholder = '🔴 सुन रहे हैं... बोलिए (Listening...)';
        targetInput.classList.add('ring-2', 'ring-rose-500', 'bg-rose-50/30');
        targetInput.readOnly = true;
      }
      if (window.triggerHaptic) window.triggerHaptic('medium');
      if (window.showToast) window.showToast('🔴 बोलिए... (Listening)', 'info', 2000);
    };

    recognition.onresult = function (event) {
      if (!event || !event.results) return;
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        const res = event.results[i];
        if (res && res[0] && res[0].transcript) {
          const interimText = res[0].transcript.trim();
          if (interimText) {
            capturedTranscript = interimText;
            if (targetInput) {
              targetInput.value = interimText;
            }
          }
          if (res.isFinal) {
            commitResult(capturedTranscript);
            stopVoiceSearch();
            return;
          }
        }
      }
    };

    recognition.onerror = function (event) {
      const err = event ? event.error : 'unknown';
      if (window.triggerHaptic) window.triggerHaptic('warning');

      // If we already captured speech before silence/disconnect, commit it!
      if (capturedTranscript && !hasCommitted) {
        commitResult(capturedTranscript);
        stopVoiceSearch();
        return;
      }

      stopVoiceSearch();

      if (err === 'not-allowed' || err === 'service-not-allowed') {
        if (window.showToast) {
          if (isIOS) {
            window.showToast('iPhone पर: Safari उपयोग करें व Settings > General > Keyboard में Dictation ऑन रखें', 'error', 5000);
          } else {
            window.showToast('माइक्रोफ़ोन अनुमति दें या ब्राउज़र सेटिंग्स जांचें (Microphone permission denied)', 'error', 4500);
          }
        }
      } else if (err === 'no-speech') {
        if (window.showToast) {
          window.showToast('कोई आवाज़ नहीं सुनाई दी, कृपया दोबारा बोलें (No speech detected)', 'warning', 2500);
        }
      } else if (err === 'network') {
        if (window.showToast) {
          window.showToast('नेटवर्क त्रुटि: वॉइस सेवा से संपर्क नहीं हो सका (Network error)', 'warning', 3500);
        }
      } else if (err === 'aborted') {
        // Silent on intentional abort
      } else {
        if (window.showToast) {
          window.showToast(`वॉइस त्रुटि (${err}), कृपया दोबारा बोलें`, 'warning', 3000);
        }
      }
    };

    recognition.onend = function () {
      if (capturedTranscript && !hasCommitted) {
        commitResult(capturedTranscript);
      }
      stopVoiceSearch();
    };

    activeVoiceSession = recognition;
    try {
      // Synchronous start within user activation context
      recognition.start();

      // Softly dismiss software keyboard after starting recognition to prevent AVAudioSession interruption
      if (document.activeElement && document.activeElement === targetInput) {
        setTimeout(() => {
          try {
            if (targetInput) targetInput.blur();
          } catch (e) {}
        }, 50);
      }
    } catch (startErr) {
      console.error('Failed to start voice recognition:', startErr);
      stopVoiceSearch();
      if (window.showToast) {
        window.showToast('वॉइस शुरू करने में त्रुटि: ' + (startErr.message || startErr), 'error', 3000);
      }
    }
  };

  function stopVoiceSearch() {
    if (activeVoiceSession) {
      const session = activeVoiceSession;
      activeVoiceSession = null;
      try {
        session.stop();
      } catch (e) {
        try {
          session.abort();
        } catch (e2) {}
      }
    }
    ['name', 'relative', 'family'].forEach(f => {
      const btn = getTargetVoiceButton(f);
      if (btn) {
        btn.classList.remove('bg-rose-500', 'text-white', 'animate-pulse', 'scale-110');
        btn.classList.add('text-slate-400');
      }
      const inp = getTargetSearchInput(f);
      if (inp) {
        inp.removeAttribute('inputmode');
        inp.readOnly = false;
        if (inp.dataset.origPlaceholder) {
          inp.placeholder = inp.dataset.origPlaceholder;
          delete inp.dataset.origPlaceholder;
        }
        inp.classList.remove('ring-2', 'ring-rose-500', 'bg-rose-50/30');
      }
    });
  }

  let currentSearchPage = 1;
  let currentSearchPageSize = parseInt(localStorage.getItem('voter_page_size') || '50', 10);
  let currentSearchSort = localStorage.getItem('voter_sort_order') || 'serial';
  let totalMatchingVoters = 0;

  // Smart Omnibar Auto-Detect & Search Trigger
  window.triggerOmniSearch = function (resetPage = true) {
    if (resetPage) currentSearchPage = 1;
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      executeSearch();
    }, 220);
  };

  window.goToVoterPage = function (page) {
    const totalPages = Math.ceil(totalMatchingVoters / currentSearchPageSize) || 1;
    if (page < 1 || page > totalPages) return;
    currentSearchPage = page;
    if (window.triggerHaptic) window.triggerHaptic('selection');
    executeSearch();
    const container = document.getElementById('votersResultsContainer');
    if (container) {
      container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  window.setVoterPageSize = function (size) {
    currentSearchPageSize = parseInt(size, 10) || 50;
    localStorage.setItem('voter_page_size', String(currentSearchPageSize));
    currentSearchPage = 1;
    if (window.triggerHaptic) window.triggerHaptic('selection');
    executeSearch();
  };

  window.setVoterSortOrder = function (sort) {
    currentSearchSort = sort || 'serial';
    localStorage.setItem('voter_sort_order', currentSearchSort);
    currentSearchPage = 1;
    if (window.triggerHaptic) window.triggerHaptic('selection');
    executeSearch();
  };

  function updateResultsCount(voters, totalCount, totalPages, isSerialQuery, nameVal, partVal, gender, ageMin, ageMax, caste) {
    const countEl = document.getElementById('resultsCount');
    if (!countEl) return;

    const filterLabels = [];
    const partSelect = document.getElementById('partFilter');
    const isWard = partSelect && partSelect.dataset.filterKey === 'ward_no';
    if (partVal) filterLabels.push(isWard ? `वार्ड ${partVal}` : `भाग ${partVal}`);
    if (gender === 'M') filterLabels.push('पुरुष');
    if (gender === 'F') filterLabels.push('महिला');
    if (ageMin || ageMax) filterLabels.push(`आयु ${ageMin || 18}-${ageMax || 100}`);
    if (caste) filterLabels.push(`समुदाय: ${caste}`);

    const filterSummary = filterLabels.length > 0 ? ` [${filterLabels.join(' • ')}]` : '';
    const startIdx = totalCount === 0 ? 0 : (currentSearchPage - 1) * currentSearchPageSize + 1;
    const endIdx = Math.min(startIdx + voters.length - 1, totalCount);

    if (totalCount === 0) {
      countEl.textContent = `कोई मतदाता नहीं मिला${filterSummary} (No voters found)`;
    } else if (isSerialQuery && nameVal.length > 0) {
      countEl.textContent = `${voters.length} क्रम संख्या मतदाता मिले${filterSummary}`;
    } else {
      countEl.textContent = `${totalCount.toLocaleString('en-IN')} मतदाता मिले${filterSummary} · पृष्ठ ${currentSearchPage}/${totalPages} (${startIdx}–${endIdx})`;
    }
  }

  let urlParamsInitialized = false;

  window.initSearchFromUrlParams = function () {
    if (urlParamsInitialized) return;
    urlParamsInitialized = true;
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const q = urlParams.get('q') || urlParams.get('name') || urlParams.get('search');
      const relative = urlParams.get('relative') || urlParams.get('rel_name') || urlParams.get('father_name');
      const targetWard = urlParams.get('ward_no') || urlParams.get('ward');
      const targetPart = urlParams.get('part_no') || urlParams.get('part') || urlParams.get('booth');
      const caste = urlParams.get('caste');

      const nameInput = document.getElementById('nameSearchInput') || document.getElementById('omnisearchInput') || document.getElementById('searchName');
      if (nameInput && q) {
        nameInput.value = q;
      }

      const relInput = document.getElementById('relativeSearchInput');
      if (relInput && relative) {
        relInput.value = relative;
      }

      const casteInput = document.getElementById('casteFilter');
      if (casteInput && caste) {
        casteInput.value = caste;
      }

      const partSelect = document.getElementById('partFilter');
      if (partSelect && (targetWard || targetPart)) {
        let matchedOpt = null;
        if (targetWard) {
          const wStr = String(targetWard).trim();
          matchedOpt = Array.from(partSelect.options).find(opt => 
            opt.value === `ward:${wStr}` || 
            opt.value === wStr || 
            opt.value === `wards:${wStr}` ||
            opt.value.toLowerCase().endsWith(`_ward:${wStr}`) ||
            opt.value === `part:${wStr}`
          );
          if (!matchedOpt) {
            matchedOpt = Array.from(partSelect.options).find(opt => 
              new RegExp(`(?:वार्ड|Ward)\\s*${wStr}\\b`, 'i').test(opt.textContent)
            );
          }
        }
        if (!matchedOpt && targetPart) {
          const pStr = String(targetPart).trim();
          matchedOpt = Array.from(partSelect.options).find(opt => 
            opt.value === `part:${pStr}` || 
            opt.value === pStr || 
            opt.value === `parts:${pStr}` ||
            opt.value === `ward:${pStr}`
          );
          if (!matchedOpt) {
            matchedOpt = Array.from(partSelect.options).find(opt => 
              new RegExp(`(?:भाग|बूथ|Part|Booth)\\s*${pStr}\\b`, 'i').test(opt.textContent)
            );
          }
        }

        const labelEl = document.getElementById('selectedWardLabel');
        if (matchedOpt) {
          partSelect.value = matchedOpt.value;
          matchedOpt.selected = true;
          if (labelEl) {
            labelEl.textContent = matchedOpt.textContent.trim();
          }
        } else {
          const fallbackVal = targetWard ? (partSelect.dataset.filterKey === 'ward_no' ? `ward:${targetWard}` : targetWard) : (targetPart ? `part:${targetPart}` : '');
          const fallbackLabel = targetWard ? `वार्ड ${targetWard}` : `भाग ${targetPart}`;
          if (fallbackVal) {
            const newOpt = document.createElement('option');
            newOpt.value = fallbackVal;
            newOpt.textContent = fallbackLabel;
            newOpt.selected = true;
            partSelect.appendChild(newOpt);
            partSelect.value = fallbackVal;
            if (labelEl) labelEl.textContent = fallbackLabel;
          }
        }

        if (typeof window.commitWardSelection === 'function') {
          const modal = document.getElementById('partFilterModal');
          if (modal) {
            const optVal = partSelect.value;
            const optText = (partSelect.options[partSelect.selectedIndex] || {}).textContent || '';
            window.commitWardSelection('partFilterModal', 'partFilter', 'selectedWardLabel', optVal, labelEl ? labelEl.textContent : optText);
          }
        } else if (typeof window.syncAllWardPills === 'function') {
          window.syncAllWardPills();
        }
      }
    } catch (e) {
      console.warn('[omnisearch] Failed to parse URL search params:', e);
    }
  };

  async function executeSearch() {
    if (!urlParamsInitialized && typeof window.initSearchFromUrlParams === 'function') {
      window.initSearchFromUrlParams();
    }
    // Drop background search triggers while Voter Profile / Edit modal is open
    const vpModal = document.getElementById('voterProfileModal');
    if (vpModal && !vpModal.classList.contains('hidden')) {
      return;
    }

    if (window.isInFamilyFilterMode) {
      if (typeof window.exitFamilyFilterView === 'function') {
        window.exitFamilyFilterView(true);
      } else {
        window.isInFamilyFilterMode = false;
      }
    }

    const nameInput = document.getElementById('nameSearchInput') || document.getElementById('omnisearchInput') || document.getElementById('searchName');
    const nameVal = (nameInput ? nameInput.value : '').trim();

    const relInput = document.getElementById('relativeSearchInput');
    const relVal = (relInput ? relInput.value : '').trim();

    const partFilter = document.getElementById('partFilter');
    const rawPartVal = partFilter ? partFilter.value : '';
    let partKey = (partFilter && partFilter.dataset.filterKey) || 'part_no';
    let partVal = rawPartVal;
    let extraWardVal = null;
    let extraPartsVal = null;
    let extraGpVal = null;
    if (rawPartVal) {
      if (rawPartVal.startsWith('gp:')) {
        // e.g. gp:IRAS_ward:1 or gp:IRAS
        const m = rawPartVal.match(/^gp:([^_\-]+)(?:[_\- ,]+ward:?(\d+))?/i);
        if (m) {
          partKey = 'gp';
          partVal = m[1];
          extraGpVal = m[1];
          if (m[2]) {
            extraWardVal = m[2];
          }
        }
      } else if (rawPartVal.includes('_ward:')) {
        // Compound key: part:9_ward:1 or parts:14,15_ward:1
        const m = rawPartVal.match(/parts?:?([0-9, ]+)[_\- ,]+ward:?(\d+)/i);
        if (m) {
          if (m[1].includes(',')) {
            partKey = 'parts';
            extraPartsVal = m[1];
          } else {
            partKey = 'part_no';
          }
          partVal = m[1];
          extraWardVal = m[2];
        }
      } else if (rawPartVal.startsWith('parts:')) {
        // Multi-part key: parts:9,10
        partKey = 'parts';
        partVal = rawPartVal.replace(/^parts:/, '');
        extraPartsVal = partVal;
      } else if (rawPartVal.startsWith('ward:')) {
        partKey = 'ward_no';
        partVal = rawPartVal.substring(5);
      } else if (rawPartVal.startsWith('part:') || rawPartVal.startsWith('booth:')) {
        partKey = 'part_no';
        partVal = rawPartVal.replace(/^(part|booth):/, '');
      }
    }
    const ageMin = document.getElementById('ageMin')?.value || '';
    const ageMax = document.getElementById('ageMax')?.value || '';
    const gender = document.getElementById('genderFilter')?.value || '';
    const caste = document.getElementById('casteFilter')?.value || '';

    // Show loading spinner
    const spinner = document.getElementById('searchLoadingSpinner');
    if (spinner) spinner.classList.remove('hidden');

    // Cancel any active request
    if (currentSearchAbort) {
      currentSearchAbort.abort();
    }
    currentSearchAbort = new AbortController();
    const thisSeq = ++currentSearchSeq;

    try {
      // 1. Check if Name is Pure Serial or Range (e.g. "45" or "12-18" or "1,2,5")
      const isSerialQuery = /^\d+(\s*[-,\.]\s*\d+)*$/.test(nameVal) && nameVal.length <= 15 && !nameVal.includes('RJ');

      let endpoint = '';
      if (isSerialQuery && nameVal.length > 0) {
        endpoint = `/api/voters_by_serials?serials=${encodeURIComponent(nameVal)}`;
        if (partVal) endpoint += `&${partKey}=${encodeURIComponent(partVal)}`;
        if (extraWardVal) endpoint += `&ward_no=${encodeURIComponent(extraWardVal)}`;
        if (extraPartsVal) endpoint += `&parts=${encodeURIComponent(extraPartsVal)}`;
        if (extraGpVal) endpoint += `&gp=${encodeURIComponent(extraGpVal)}`;
        if (ageMin) endpoint += `&age_min=${encodeURIComponent(ageMin)}`;
        if (ageMax) endpoint += `&age_max=${encodeURIComponent(ageMax)}`;
        if (gender) endpoint += `&gender=${encodeURIComponent(gender)}`;
      } else {
        const offset = (currentSearchPage - 1) * currentSearchPageSize;
        const params = new URLSearchParams();
        if (nameVal) params.set('name', nameVal);
        if (relVal) params.set('relative', relVal);
        if (nameVal || relVal) {
          params.set('q', `${nameVal} ${relVal}`.trim());
        }
        if (partVal) params.set(partKey, partVal);
        if (extraWardVal) params.set('ward_no', extraWardVal);
        if (extraPartsVal) params.set('parts', extraPartsVal);
        if (extraGpVal) params.set('gp', extraGpVal);
        if (ageMin) params.set('age_min', ageMin);
        if (ageMax) params.set('age_max', ageMax);
        if (gender) params.set('gender', gender);
        if (caste) params.set('caste', caste);
        if (currentSearchSort) params.set('sort', currentSearchSort);
        params.set('limit', String(currentSearchPageSize));
        params.set('offset', String(offset));
        endpoint = `/api/search?${params.toString()}`;
      }

      const queryDisplay = [nameVal, relVal].filter(Boolean).join(' + ') || '';

      const cacheKey = endpoint;
      if (searchCache.has(cacheKey)) {
        const cached = searchCache.get(cacheKey);
        totalMatchingVoters = cached.meta?.total ?? cached.voters.length;
        if (typeof window.renderVoterResults === 'function') {
          window.renderVoterResults(cached.voters, queryDisplay, cached.meta);
        } else if (typeof renderVoterResults === 'function') {
          renderVoterResults(cached.voters, queryDisplay, cached.meta);
        }
        updateResultsCount(cached.voters, totalMatchingVoters, cached.meta?.totalPages || 1, isSerialQuery, nameVal, partVal, gender, ageMin, ageMax, caste);
        return;
      }

      // 0. Instant Offline Fast-Path (Execute in <2ms without waiting for network timeout)
      if (!navigator.onLine && window.SmartBoothOfflineDB) {
        try {
          const offlineRes = await window.SmartBoothOfflineDB.searchLocalVoters({
            name: nameVal,
            relative: relVal,
            part_no: partVal,
            ward_no: extraWardVal,
            gender: gender,
            caste: caste
          });

          if (offlineRes) {
            const votersList = offlineRes.voters || [];
            totalMatchingVoters = typeof offlineRes.total === 'number' ? offlineRes.total : votersList.length;
            const paginationMeta = {
              currentPage: 1,
              pageSize: votersList.length,
              total: totalMatchingVoters,
              totalPages: 1,
              isSerialQuery: isSerialQuery && nameVal.length > 0,
              isOffline: true
            };
            if (typeof window.renderVoterResults === 'function') {
              window.renderVoterResults(votersList, queryDisplay, paginationMeta);
            } else if (typeof renderVoterResults === 'function') {
              renderVoterResults(votersList, queryDisplay, paginationMeta);
            }
            updateResultsCount(votersList, totalMatchingVoters, 1, isSerialQuery, nameVal, partVal, gender, ageMin, ageMax, caste);
            return;
          }
        } catch (dbErr) {
          console.error('Instant offline search error:', dbErr);
        }
      }

      const res = await fetch(endpoint, { signal: currentSearchAbort.signal });
      if (res.status === 401) {
        if (typeof window.showToast === 'function') {
          window.showToast('लॉगिन सत्र समाप्त हो गया है। कृपया पुनः लॉगिन करें (Session expired. Redirecting...)', 'warning', 2500);
        }
        setTimeout(() => {
          const nextUrl = encodeURIComponent(window.location.pathname + window.location.search);
          window.location.href = '/login?next=' + nextUrl;
        }, 400);
        return;
      }
      if (!res.ok) {
        console.error('Search request failed with status:', res.status);
        return;
      }

      const data = await res.json();
      if (!data.success && data.error) {
        if (typeof window.showToast === 'function') {
          window.showToast(data.error, 'error', 3000);
        }
        return;
      }

      if (thisSeq !== currentSearchSeq) return; // Discard stale out-of-order response

      const voters = data.voters || data.results || [];
      totalMatchingVoters = typeof data.total === 'number' ? data.total : (data.total_count || voters.length);

      const totalPages = Math.ceil(totalMatchingVoters / currentSearchPageSize) || 1;
      const paginationMeta = {
        currentPage: currentSearchPage,
        pageSize: currentSearchPageSize,
        total: totalMatchingVoters,
        totalPages: totalPages,
        isSerialQuery: isSerialQuery && nameVal.length > 0
      };

      if (searchCache.size >= 100) {
        const firstKey = searchCache.keys().next().value;
        searchCache.delete(firstKey);
      }
      searchCache.set(cacheKey, { voters, meta: paginationMeta });

      if (typeof window.renderVoterResults === 'function') {
        window.renderVoterResults(voters, queryDisplay, paginationMeta);
        if (window.triggerHaptic && voters.length > 0) window.triggerHaptic('light');
      } else if (typeof renderVoterResults === 'function') {
        renderVoterResults(voters, queryDisplay, paginationMeta);
      }

      updateResultsCount(voters, totalMatchingVoters, totalPages, isSerialQuery, nameVal, partVal, gender, ageMin, ageMax, caste);
    } catch (err) {
      if (err.name === 'AbortError') return;
      if (err instanceof ReferenceError || err instanceof TypeError) {
        console.error('Fatal JavaScript runtime error in search:', err);
        return;
      }
      console.warn('Network search failed, checking offline IndexedDB cache...', err);

      // Offline Fallback from IndexedDB
      if (window.SmartBoothOfflineDB) {
        try {
          const offlineRes = await window.SmartBoothOfflineDB.searchLocalVoters({
            name: nameVal,
            relative: relVal,
            part_no: partVal,
            ward_no: extraWardVal,
            gender: gender,
            caste: caste
          });

          if (offlineRes && offlineRes.voters && offlineRes.voters.length > 0) {
            totalMatchingVoters = offlineRes.total;
            const paginationMeta = {
              currentPage: 1,
              pageSize: offlineRes.voters.length,
              total: offlineRes.total,
              totalPages: 1,
              isSerialQuery: isSerialQuery && nameVal.length > 0,
              isOffline: true
            };
            if (typeof window.renderVoterResults === 'function') {
              window.renderVoterResults(offlineRes.voters, queryDisplay, paginationMeta);
            }
            updateResultsCount(offlineRes.voters, offlineRes.total, 1, isSerialQuery, nameVal, partVal, gender, ageMin, ageMax, caste);
            return;
          }
        } catch (dbErr) {
          console.error('Offline DB query error:', dbErr);
        }
      }
    } finally {
      if (thisSeq === currentSearchSeq && spinner) {
        spinner.classList.add('hidden');
      }
    }
  }

  // Clear Individual Search Boxes
  window.clearNameSearch = function () {
    const el = document.getElementById('nameSearchInput') || document.getElementById('omnisearchInput');
    if (el) {
      el.value = '';
      el.blur();
    }
    if (document.activeElement && typeof document.activeElement.blur === 'function') {
      document.activeElement.blur();
    }
    executeSearch();
  };

  window.clearRelativeSearch = function () {
    const el = document.getElementById('relativeSearchInput');
    if (el) {
      el.value = '';
      el.blur();
    }
    if (document.activeElement && typeof document.activeElement.blur === 'function') {
      document.activeElement.blur();
    }
    executeSearch();
  };

  // Clear Search (clears both)
  window.clearOmniSearch = function () {
    const nameEl = document.getElementById('nameSearchInput') || document.getElementById('omnisearchInput');
    const relEl = document.getElementById('relativeSearchInput');
    if (nameEl) {
      nameEl.value = '';
      nameEl.blur();
    }
    if (relEl) {
      relEl.value = '';
      relEl.blur();
    }
    if (document.activeElement && typeof document.activeElement.blur === 'function') {
      document.activeElement.blur();
    }
    executeSearch();
  };

  // Keyboard shortcut '/' or 'Ctrl+K' to focus Omnibar
  document.addEventListener('keydown', (e) => {
    if ((e.key === '/' || (e.ctrlKey && e.key === 'k')) && document.activeElement.tagName !== 'INPUT') {
      e.preventDefault();
      const omniInput = document.getElementById('nameSearchInput') || document.getElementById('omnisearchInput') || document.getElementById('searchName') || document.getElementById('familySearchInput');
      if (omniInput) {
        omniInput.focus();
        omniInput.select();
      }
    }
  });

  /**
   * Invalidate the entire search cache.
   * Call this after any mutation that could affect search results
   * (e.g. mark-as-voted, category change, phone update).
   * The next search will re-fetch fresh results from the server.
   */
  window.invalidateSearchCache = function () {
    searchCache.clear();
  };

  /**
   * Patch a single voter's field in every cached result set and in
   * APP_STATE.currentResults — avoids a full cache wipe for single-voter mutations.
   * @param {number} voterId  - The voter's id / voter_id (integer)
   * @param {Object} patch    - Key-value pairs to merge into the voter object
   */
  window.patchVoterInCache = function (voterId, patch) {
    // 1. Patch every cached page
    searchCache.forEach((entry) => {
      const voter = (entry.voters || []).find(v => (v.id || v.voter_id) === voterId);
      if (voter) Object.assign(voter, patch);
    });
    // 2. Patch APP_STATE.currentResults so re-render shows correct state
    if (window.APP_STATE && Array.isArray(window.APP_STATE.currentResults)) {
      const voter = window.APP_STATE.currentResults.find(v => (v.id || v.voter_id) === voterId);
      if (voter) Object.assign(voter, patch);
    }
    // 3. Patch selectedVoters map too
    if (window.APP_STATE && window.APP_STATE.selectedVoters instanceof Map) {
      const voter = window.APP_STATE.selectedVoters.get(voterId);
      if (voter) Object.assign(voter, patch);
    }
  };

})();

