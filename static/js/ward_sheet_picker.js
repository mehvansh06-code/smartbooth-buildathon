/**
 * SmartBooth Ward / Part Bottom Sheet Picker Engine
 * Ultra-clean, touch-first mobile picker that dynamically synchronizes
 * with the authoritative <select> element and provides instant, fault-tolerant rendering.
 *
 * Supports:
 * - Server-side pre-rendered options (instant, zero-flash)
 * - Dynamic fallback client hydration from <select>
 * - Multi-Gram Panchayat Collapsible Accordion (with active card auto-expansion)
 * - Single-Ward & Multi-Ward seamless part browsing
 */

(function () {
  'use strict';

  function formatCount(numStr) {
    if (!numStr) return '';
    var n = parseInt(String(numStr).replace(/,/g, ''), 10);
    return isNaN(n) ? numStr : n.toLocaleString('en-IN') + ' मत';
  }

  function parseOptionData(opt, isGlobal) {
    var rawText = (opt.textContent || '').trim();
    var val = opt.value;
    var count = '';
    var title = rawText;
    var subtitle = '';

    // Extract voter count: e.g. " (1158 मतदाता)" or " — कुल 3130 मतदाता"
    var countMatch = rawText.match(/(?:कुल\s*)?([0-9,]+)\s*(?:मतदाता|मत)/);
    if (countMatch) {
      count = formatCount(countMatch[1]);
    }

    if (!val || val === '') {
      // "All" option
      if (rawText.indexOf('समस्त भाग') !== -1 || rawText.indexOf('वार्ड') !== -1) {
        var wardMatch = rawText.match(/वार्ड\s*(\d+)/);
        title = wardMatch ? ('समस्त भाग (वार्ड ' + wardMatch[1] + ')') : 'सभी वार्ड';
        subtitle = 'समस्त मतदाता';
      } else if (rawText.indexOf('मतदान केंद्र') !== -1) {
        title = 'सभी मतदान केंद्र';
        subtitle = 'समस्त भाग';
      } else {
        title = 'सभी क्षेत्र (All Areas)';
        subtitle = 'संपूर्ण चुनाव क्षेत्र के सभी मतदाता';
      }
    } else if (val.indexOf('parts:') === 0 || (val.indexOf('gp:') === 0 && val.indexOf('_ward:') === -1)) {
      // Entire Gram Panchayat option e.g. "समस्त ईरास (सभी 7 वार्ड — 2,319 मतदाता)"
      var mG = rawText.match(/^([^—–\(\)]+)(?:[—–\(\)]+([^—–\(\)]+))?/);
      if (mG) {
        title = mG[1].trim();
        subtitle = mG[2] ? mG[2].trim() : 'समस्त वार्ड';
      } else {
        title = rawText.split(/[—–]/)[0].trim();
      }
    } else {
      // Specific ward, part, or booth
      var prefixMatch = rawText.match(/^([^—–\(]+?)(?:[—–\(]|$)/);
      if (prefixMatch) {
        title = prefixMatch[1].trim();
      } else {
        var dashParts = rawText.split(/[—–]/);
        title = dashParts[0].trim();
      }

      // Check for booth description in subtitle
      var boothMatch = rawText.match(/(बूथ\s*\d+[^—–\(\)]*)/);
      if (boothMatch) {
        subtitle = boothMatch[1].trim();
      }
    }

    return {
      value: val,
      title: title,
      subtitle: subtitle,
      count: count,
      fullText: rawText,
      selected: opt.selected
    };
  }

  function createOptionRow(opt, isGlobal, modalId, selectId, labelId) {
    var data = parseOptionData(opt, isGlobal);
    var displayLabel = data.title + (data.count ? ' (' + data.count + ')' : '');

    var row = document.createElement('div');
    row.setAttribute('data-ward-value', data.value);
    row.className = 'px-4 py-3 hover:bg-slate-50 flex items-center justify-between cursor-pointer transition select-none ' +
      (data.selected ? 'bg-blue-50/80 border-l-4 border-blue-600' : 'border-l-4 border-transparent hover:border-slate-300');

    var leftDiv = document.createElement('div');
    leftDiv.className = 'min-w-0 pr-2';
    var titleDiv = document.createElement('div');
    titleDiv.className = 'text-sm sm:text-base font-bold text-slate-900 truncate';
    titleDiv.textContent = data.title;
    leftDiv.appendChild(titleDiv);

    if (data.subtitle) {
      var subDiv = document.createElement('div');
      subDiv.className = 'text-xs text-blue-700 font-semibold mt-0.5 truncate';
      subDiv.textContent = data.subtitle;
      leftDiv.appendChild(subDiv);
    }

    var rightDiv = document.createElement('div');
    rightDiv.className = 'flex items-center gap-2 shrink-0';

    if (data.count) {
      var countBadge = document.createElement('span');
      countBadge.className = 'text-xs font-black ' +
        (data.selected ? 'bg-blue-600 text-white shadow-xs' : 'text-slate-700 bg-slate-100 border border-slate-200') +
        ' px-2.5 py-1 rounded-full';
      countBadge.textContent = data.count;
      rightDiv.appendChild(countBadge);
    }

    var checkSpan = document.createElement('span');
    checkSpan.className = 'ward-check text-blue-600 font-black text-base ' + (data.selected ? '' : 'hidden');
    checkSpan.textContent = '✓';
    rightDiv.appendChild(checkSpan);

    row.appendChild(leftDiv);
    row.appendChild(rightDiv);

    row.onclick = function (e) {
      e.stopPropagation();
      window.selectWardOption(modalId, selectId, labelId, data.value, displayLabel);
    };

    return row;
  }

  var _activeModalSets = {};

  // Synchronize visual selection highlight inside modal (supports single and multi-select)
  function syncModalSelection(modal, activeValue) {
    if (!modal) return;
    var targetVal = String(activeValue || '');
    var activeSet = new Set();
    if (targetVal.startsWith('wards:')) {
      targetVal.substring(6).split(',').forEach(function(w) {
        if (w.trim()) activeSet.add('ward:' + w.trim());
      });
    } else if (targetVal.startsWith('parts:')) {
      targetVal.substring(6).split(',').forEach(function(p) {
        if (p.trim()) {
          activeSet.add('part:' + p.trim());
          activeSet.add(p.trim());
        }
      });
    } else if (targetVal) {
      activeSet.add(targetVal);
    }
    _activeModalSets[modal.id] = activeSet;

    var items = modal.querySelectorAll('[data-ward-value]');
    var matchingCard = null;

    items.forEach(function (item) {
      var itemVal = String(item.getAttribute('data-ward-value') || '');
      var isSelected = (activeSet.size === 0 && itemVal === '') || activeSet.has(itemVal);
      var checkEl = item.querySelector('.ward-check');

      if (isSelected) {
        item.classList.add('bg-blue-50/80', 'border-l-4', 'border-blue-600');
        item.classList.remove('border-transparent');
        if (checkEl) checkEl.classList.remove('hidden');
        var card = item.closest('.gp-accordion-card');
        if (card) matchingCard = card;
      } else {
        if (!item.classList.contains('rounded-xl')) {
          item.classList.remove('bg-blue-50/80', 'border-blue-600');
          item.classList.add('border-transparent');
        }
        if (checkEl) checkEl.classList.add('hidden');
      }
    });

    var applyBtn = modal.querySelector('.ward-apply-btn') || document.getElementById(modal.id + '_apply_btn');
    if (applyBtn) {
      if (activeSet.size > 1) {
        applyBtn.textContent = 'लागू करें (' + activeSet.size + ')';
        applyBtn.classList.remove('hidden');
      } else {
        applyBtn.textContent = 'लागू करें';
        applyBtn.classList.add('hidden');
      }
    }

    // Auto-expand accordion card containing active item
    if (matchingCard) {
      var body = matchingCard.querySelector('.gp-accordion-body');
      var arrow = matchingCard.querySelector('.gp-accordion-arrow');
      var header = matchingCard.querySelector('.gp-accordion-header');
      if (body) body.style.display = 'block';
      if (arrow) arrow.textContent = '▼';
      if (header) {
        header.classList.add('bg-blue-50/60', 'border-blue-200/60');
        header.classList.remove('bg-slate-50', 'border-slate-100');
      }
    }
  }

  // Toggle GP Accordion Folder
  window.toggleGpAccordion = function (header) {
    if (!header) return;
    var card = header.closest('.gp-accordion-card');
    if (!card) return;
    var body = card.querySelector('.gp-accordion-body');
    var arrow = header.querySelector('.gp-accordion-arrow');
    if (!body) return;

    var isOpen = body.style.display !== 'none';
    if (isOpen) {
      body.style.display = 'none';
      if (arrow) arrow.textContent = '▶';
      header.classList.remove('bg-blue-50/60', 'border-blue-200/60');
      header.classList.add('bg-slate-50', 'border-slate-100');
    } else {
      body.style.display = 'block';
      if (arrow) arrow.textContent = '▼';
      header.classList.add('bg-blue-50/60', 'border-blue-200/60');
      header.classList.remove('bg-slate-50', 'border-slate-100');
    }
  };

  function hydrateModalFromSelect(modal, selectEl, labelId) {
    if (!modal || !selectEl) return;
    var listContainer = modal.querySelector('.ward-sheet-options');
    if (!listContainer) return;

    // If already pre-rendered by server Jinja, just sync selection state!
    if (listContainer.children && listContainer.children.length > 0) {
      syncModalSelection(modal, selectEl.value);
      return;
    }

    try {
      listContainer.innerHTML = '';
      var modalId = modal.id;
      var selectId = selectEl.id;

      var optgroups = selectEl.querySelectorAll('optgroup');
      var hasOptgroups = optgroups.length > 0;

      // Flat rendering for single optgroup or simple flat select
      if (!hasOptgroups || optgroups.length <= 1) {
        var options = selectEl.querySelectorAll('option');
        options.forEach(function (opt, idx) {
          var row = createOptionRow(opt, idx === 0, modalId, selectId, labelId);
          listContainer.appendChild(row);
        });
        syncModalSelection(modal, selectEl.value);
        return;
      }

      // --- ACCORDION RENDERING FOR MULTI-GP CAMPAIGNS (> 1 optgroup) ---
      // 1. Direct options ("All Areas")
      var directOptions = Array.from(selectEl.children).filter(function (child) {
        return child.tagName.toLowerCase() === 'option';
      });

      directOptions.forEach(function (opt) {
        var row = createOptionRow(opt, true, modalId, selectId, labelId);
        row.className += ' mb-2 bg-blue-50/70 border-l-4 border-blue-600 rounded-xl mx-2 shadow-2xs';
        listContainer.appendChild(row);
      });

      // 2. Render each <optgroup> as a Collapsible Card
      optgroups.forEach(function (group) {
        var groupLabel = (group.label || '').trim();
        var groupOptions = group.querySelectorAll('option');
        if (groupOptions.length === 0) return;

        var card = document.createElement('div');
        card.className = 'gp-accordion-card mb-2.5 mx-2 border border-slate-200/90 rounded-2xl overflow-hidden bg-white shadow-2xs transition-all';

        var countMatch = groupLabel.match(/([0-9,]+)\s*(?:मतदाता|मत)/);
        var groupCount = countMatch ? formatCount(countMatch[1]) : '';
        var cleanTitle = groupLabel.replace(/\([^\)]+\)/g, '').trim();

        // Header
        var header = document.createElement('div');
        header.className = 'gp-accordion-header px-4 py-3 bg-slate-50 hover:bg-slate-100 flex items-center justify-between cursor-pointer select-none transition border-b border-slate-100';

        var leftDiv = document.createElement('div');
        leftDiv.className = 'flex items-center gap-2 min-w-0';
        var iconSpan = document.createElement('span');
        iconSpan.className = 'text-base shrink-0';
        iconSpan.textContent = cleanTitle.indexOf('🏛️') !== -1 ? '' : '🏛️';
        var titleSpan = document.createElement('span');
        titleSpan.className = 'font-black text-slate-900 text-sm sm:text-base truncate';
        titleSpan.textContent = cleanTitle;
        leftDiv.appendChild(iconSpan);
        leftDiv.appendChild(titleSpan);

        var rightDiv = document.createElement('div');
        rightDiv.className = 'flex items-center gap-2 shrink-0 ml-2';
        if (groupCount) {
          var badge = document.createElement('span');
          badge.className = 'text-xs font-black bg-blue-100/80 text-blue-900 border border-blue-200/60 px-2.5 py-0.5 rounded-full';
          badge.textContent = groupCount;
          rightDiv.appendChild(badge);
        }
        var arrowSpan = document.createElement('span');
        arrowSpan.className = 'gp-accordion-arrow text-slate-400 font-black text-xs transition-transform duration-200';
        arrowSpan.textContent = '▼';
        rightDiv.appendChild(arrowSpan);

        header.appendChild(leftDiv);
        header.appendChild(rightDiv);

        header.onclick = function (e) {
          e.stopPropagation();
          window.toggleGpAccordion(header);
        };

        // Body
        var body = document.createElement('div');
        body.className = 'gp-accordion-body divide-y divide-slate-100 bg-white transition-all';

        groupOptions.forEach(function (opt) {
          var row = createOptionRow(opt, false, modalId, selectId, labelId);
          body.appendChild(row);
        });

        card.appendChild(header);
        card.appendChild(body);
        listContainer.appendChild(card);
      });

      syncModalSelection(modal, selectEl.value);
    } catch (err) {
      console.error('[WardSheetPicker] Hydration error:', err);
    }
  }

  window.openWardSheet = function (modalId, selectId, labelId) {
    var modal = document.getElementById(modalId);
    if (!modal) return;
    modal._openedAt = Date.now();

    var targetSelectId = selectId || modal.getAttribute('data-select-id') || 'partFilter';
    var targetLabelId = labelId || modal.getAttribute('data-label-id') || 'selectedWardLabel';
    var selectEl = document.getElementById(targetSelectId);

    if (selectEl) {
      hydrateModalFromSelect(modal, selectEl, targetLabelId);
      syncModalSelection(modal, selectEl.value);
    }

    modal.classList.remove('hidden');
    document.body.classList.add('overflow-hidden');
    if (window.triggerHaptic) window.triggerHaptic('light');
  };

  window.closeWardSheet = function (modalId) {
    var modal = document.getElementById(modalId);
    if (!modal) return;
    modal.classList.add('hidden');
    document.body.classList.remove('overflow-hidden');
  };

  window.selectWardOption = function (modalId, selectId, labelId, value, displayLabel) {
    if (window.triggerHaptic) window.triggerHaptic('selection');

    var modal = document.getElementById(modalId);
    var activeSet = _activeModalSets[modalId] || new Set();

    // If selecting "All", immediately commit and close
    if (!value || value === '') {
      activeSet.clear();
      _activeModalSets[modalId] = activeSet;
      commitWardSelection(modalId, selectId, labelId, '', displayLabel);
      return;
    }

    // Toggle multi-select
    if (activeSet.has(value)) {
      activeSet.delete(value);
    } else {
      activeSet.add(value);
    }
    _activeModalSets[modalId] = activeSet;

    // Update checkmarks in modal
    var items = modal ? modal.querySelectorAll('[data-ward-value]') : [];
    items.forEach(function (item) {
      var itemVal = String(item.getAttribute('data-ward-value') || '');
      var checkEl = item.querySelector('.ward-check');
      var isSel = (activeSet.size === 0 && itemVal === '') || activeSet.has(itemVal);
      if (isSel) {
        item.classList.add('bg-blue-50/80', 'border-l-4', 'border-blue-600');
        item.classList.remove('border-transparent');
        if (checkEl) checkEl.classList.remove('hidden');
      } else {
        if (!item.classList.contains('rounded-xl')) {
          item.classList.remove('bg-blue-50/80', 'border-blue-600');
          item.classList.add('border-transparent');
        }
        if (checkEl) checkEl.classList.add('hidden');
      }
    });

    var applyBtn = modal ? (modal.querySelector('.ward-apply-btn') || document.getElementById(modalId + '_apply_btn')) : null;
    if (applyBtn) {
      applyBtn.classList.remove('hidden');
      if (activeSet.size > 1) {
        applyBtn.textContent = 'लागू करें (' + activeSet.size + ')';
      } else if (activeSet.size === 1) {
        applyBtn.textContent = 'लागू करें';
      } else {
        applyBtn.textContent = 'सभी चुनें';
      }
    }
  };

  window.applyWardMultiSelection = function (modalId, selectId, labelId) {
    var activeSet = _activeModalSets[modalId] || new Set();
    if (activeSet.size === 0) {
      commitWardSelection(modalId, selectId, labelId, '', 'सभी क्षेत्र (All Areas)');
      return;
    }

    var values = Array.from(activeSet);
    if (values.length === 1) {
      var singleVal = values[0];
      var modal = document.getElementById(modalId);
      var singleEl = modal ? modal.querySelector('[data-ward-value="' + singleVal + '"]') : null;
      var singleLabel = singleVal;
      if (singleEl) {
        var titleEl = singleEl.querySelector('.font-bold');
        if (titleEl) singleLabel = titleEl.textContent.trim();
      }
      commitWardSelection(modalId, selectId, labelId, singleVal, singleLabel);
      return;
    }

    // Multiple values selected!
    var wardNums = [];
    var partNums = [];
    values.forEach(function(v) {
      if (v.startsWith('ward:')) {
        wardNums.push(parseInt(v.substring(5), 10));
      } else if (v.startsWith('part:')) {
        partNums.push(parseInt(v.substring(5), 10));
      } else if (/^\d+$/.test(v)) {
        partNums.push(parseInt(v, 10));
      }
    });

    wardNums = wardNums.filter(function(n) { return !isNaN(n); }).sort(function(a,b){ return a-b; });
    partNums = partNums.filter(function(n) { return !isNaN(n); }).sort(function(a,b){ return a-b; });

    var finalVal = '';
    var finalLabel = '';
    if (wardNums.length > 0) {
      finalVal = 'wards:' + wardNums.join(',');
      finalLabel = 'वार्ड ' + wardNums.join(', ');
    } else if (partNums.length > 0) {
      finalVal = 'parts:' + partNums.join(',');
      finalLabel = 'भाग ' + partNums.join(', ');
    } else {
      finalVal = values.join(',');
      finalLabel = values.join(', ');
    }

    commitWardSelection(modalId, selectId, labelId, finalVal, finalLabel);
  };

  function commitWardSelection(modalId, selectId, labelId, value, displayLabel) {
    var labelEl = document.getElementById(labelId);
    if (labelEl) {
      labelEl.textContent = displayLabel;
    }

    var selectEl = document.getElementById(selectId);
    if (selectEl) {
      var opt = selectEl.querySelector('option[value="' + value + '"]');
      if (!opt && value) {
        opt = document.createElement('option');
        opt.value = value;
        opt.textContent = displayLabel;
        selectEl.appendChild(opt);
      }
      selectEl.value = value;
      selectEl.dispatchEvent(new Event('input', { bubbles: true }));
      selectEl.dispatchEvent(new Event('change', { bubbles: true }));
      if (typeof selectEl.onchange === 'function') {
        try { selectEl.onchange(); } catch (_) {}
      }
    }

    var modal = document.getElementById(modalId);
    if (modal) {
      syncModalSelection(modal, value);
    }

    window.closeWardSheet(modalId);
  }

  // Sync initial pill label on page load
  function syncAllWardPills() {
    var triggers = document.querySelectorAll('[data-ward-trigger]');
    triggers.forEach(function (trigger) {
      var selectId = trigger.getAttribute('data-select-id');
      var labelId = trigger.getAttribute('data-label-id');
      var selectEl = document.getElementById(selectId);
      var labelEl = document.getElementById(labelId);
      if (!selectEl || !labelEl) return;

      var selOpt = selectEl.options[selectEl.selectedIndex];
      if (selOpt) {
        var parsed = parseOptionData(selOpt, selectEl.selectedIndex === 0);
        var display = parsed.title + (parsed.count ? ' (' + parsed.count + ')' : '');
        labelEl.textContent = display;
      }
    });
  }

  // Global listeners
  window.commitWardSelection = commitWardSelection;
  window.syncModalSelection = syncModalSelection;
  window.syncAllWardPills = syncAllWardPills;
  document.addEventListener('DOMContentLoaded', syncAllWardPills);

  document.addEventListener('click', function (e) {
    if (e.target && e.target.classList.contains('ward-sheet-backdrop')) {
      var modal = e.target.closest('.ward-sheet-modal');
      if (modal) {
        // Prevent 300ms mobile ghost-click dismissal right after opening
        if (modal._openedAt && (Date.now() - modal._openedAt < 350)) {
          return;
        }
        window.closeWardSheet(modal.id);
      }
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var openModals = document.querySelectorAll('.ward-sheet-modal:not(.hidden)');
      openModals.forEach(function (m) {
        window.closeWardSheet(m.id);
      });
    }
  });
})();
