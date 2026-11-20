/**
 * app_state.js - Central Reactive State & UI Shell Manager
 * Part of SmartBooth Platform
 */

(function () {
  'use strict';

  // Centralized XSS-Safe HTML Escaping Utility
  window.escapeHtml = function (str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };

  // Canonical Sentiment Badge Component
  window.renderSentimentBadge = function (category) {
    const s = String(category !== undefined && category !== null ? category : '0').trim();
    if (s === '100') {
      return '<span class="text-[10px] bg-emerald-50 text-emerald-800 border border-emerald-300 px-2 py-0.5 rounded-md font-bold inline-flex items-center gap-1 shadow-2xs">🟢 100% पक्का</span>';
    }
    if (s === '50' || s === '50_50') {
      return '<span class="text-[10px] bg-amber-50 text-amber-800 border border-amber-300 px-2 py-0.5 rounded-md font-bold inline-flex items-center gap-1 shadow-2xs">🟡 50-50</span>';
    }
    if (s === '-1' || s === 'opposition') {
      return '<span class="text-[10px] bg-rose-50 text-rose-800 border border-rose-300 px-2 py-0.5 rounded-md font-bold inline-flex items-center gap-1 shadow-2xs">🔴 विरोधी</span>';
    }
    return '';
  };

  // Canonical Empty State Component
  window.renderEmptyState = function (icon = '🔍', title = 'कोई जानकारी नहीं मिली', subtitle = '') {
    const safeIcon = window.escapeHtml ? window.escapeHtml(icon || '🔍') : (icon || '🔍');
    const safeTitle = window.escapeHtml ? window.escapeHtml(title || '') : (title || '');
    const safeSubtitle = subtitle ? (window.escapeHtml ? window.escapeHtml(subtitle) : subtitle) : '';
    return `
      <div class="text-center py-16 px-4 bg-white rounded-2xl border border-slate-200/80 shadow-xs">
        <div class="text-4xl mb-3">${safeIcon}</div>
        <h3 class="text-sm font-bold text-slate-700 mb-1">${safeTitle}</h3>
        ${safeSubtitle ? `<p class="text-xs text-slate-400 max-w-sm mx-auto">${safeSubtitle}</p>` : ''}
      </div>
    `;
  };

  // Global Reactive State
  window.APP_STATE = {
    mode: 'slip', // 'slip' (Volunteer Fast Dispatch) | 'intel' (Campaign Strategist)
    selectedVoters: new Map(), // voter_id -> voter object
    currentVoter: null,
    combinedVoters: [],
    audioMuted: localStorage.getItem('voter_audio_muted') === 'true',
    activeView: 'search', // 'search' | 'history' | 'outside'
    searchQuery: '',
    activeFilters: {
      part: '',
      ageMin: '',
      ageMax: '',
      gender: '',
      caste: '',
      category: ''
    }
  };

  // Toast Notification System
  window.showToast = function (message, type = 'info', duration = 3000) {
    let container = document.getElementById('toastContainer');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toastContainer';
      container.className = 'fixed bottom-20 right-4 z-50 flex flex-col gap-2 pointer-events-none max-w-sm w-full';
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    const bgColors = {
      success: 'bg-emerald-700 text-white border-emerald-600',
      error: 'bg-rose-700 text-white border-rose-600',
      warning: 'bg-amber-600 text-white border-amber-500',
      info: 'bg-slate-900 text-white border-slate-700'
    };

    const icons = {
      success: '✓',
      error: '✕',
      warning: '⚠',
      info: 'ℹ'
    };

    toast.className = `${bgColors[type] || bgColors.info} px-4 py-3 rounded-xl shadow-xl border text-xs font-semibold flex items-center gap-2.5 transform transition-all duration-300 translate-y-2 opacity-0 pointer-events-auto`;
    toast.innerHTML = `<span class="font-bold text-sm shrink-0">${icons[type] || '•'}</span><span class="flex-1 leading-snug">${message}</span>`;

    container.appendChild(toast);

    requestAnimationFrame(() => {
      toast.classList.remove('translate-y-2', 'opacity-0');
      toast.classList.add('translate-y-0', 'opacity-100');
    });

    setTimeout(() => {
      toast.classList.add('opacity-0', 'translate-y-2');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  };

  // Dual-Mode Switching Controller
  window.setAppMode = function (targetMode) {
    if (!['slip', 'intel'].includes(targetMode)) targetMode = 'slip';
    window.APP_STATE.mode = targetMode;
    localStorage.setItem('voter_app_mode', targetMode);

    // Update Mode Buttons in Header
    const btnSlip = document.getElementById('btnModeSlip');
    const btnIntel = document.getElementById('btnModeIntel');
    const intelElements = document.querySelectorAll('.intel-only');

    if (targetMode === 'intel') {
      if (btnSlip) {
        btnSlip.classList.remove('bg-blue-600', 'text-white', 'shadow-xs');
        btnSlip.classList.add('text-slate-300', 'hover:text-white');
      }
      if (btnIntel) {
        btnIntel.classList.add('bg-amber-600', 'text-white', 'shadow-xs');
        btnIntel.classList.remove('text-slate-300', 'hover:text-white');
      }
      intelElements.forEach(el => el.classList.remove('hidden'));
    } else {
      if (btnSlip) {
        btnSlip.classList.add('bg-blue-600', 'text-white', 'shadow-xs');
        btnSlip.classList.remove('text-slate-300', 'hover:text-white');
      }
      if (btnIntel) {
        btnIntel.classList.remove('bg-amber-600', 'text-white', 'shadow-xs');
        btnIntel.classList.add('text-slate-300', 'hover:text-white');
      }
      intelElements.forEach(el => el.classList.add('hidden'));
    }

    // Refresh UI elements
    if (typeof window.renderSelectedVotersBasket === 'function') {
      window.renderSelectedVotersBasket();
    }
    if (typeof window.renderCurrentResults === 'function') {
      window.renderCurrentResults();
    }
    if (typeof window.updateFloatingActionIsland === 'function') {
      window.updateFloatingActionIsland();
    }
  };

  // Sound Feedback Engine
  const audioCtx = window.AudioContext || window.webkitAudioContext;
  let ctxInstance = null;

  window.playAudioTone = function (freq = 880, duration = 0.1, type = 'sine') {
    if (window.APP_STATE.audioMuted || !audioCtx) return;
    try {
      if (!ctxInstance) ctxInstance = new audioCtx();
      if (ctxInstance.state === 'suspended') ctxInstance.resume();
      const osc = ctxInstance.createOscillator();
      const gain = ctxInstance.createGain();
      osc.type = type;
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.08, ctxInstance.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctxInstance.currentTime + duration);
      osc.connect(gain);
      gain.connect(ctxInstance.destination);
      osc.start();
      osc.stop(ctxInstance.currentTime + duration);
    } catch (e) {
      // Audio autoplay policy fallback
    }
  };

  let hasUserInteracted = false;
  ['click', 'keydown', 'touchstart', 'pointerdown'].forEach(evt => {
    window.addEventListener(evt, () => { hasUserInteracted = true; }, { once: true, passive: true });
  });

  // Tactile Haptic Feedback Engine
  window.triggerHaptic = function (type = 'light') {
    if (!hasUserInteracted || !('vibrate' in navigator)) return;
    try {
      if (type === 'light' || type === 'selection') {
        navigator.vibrate(12);
      } else if (type === 'medium') {
        navigator.vibrate(25);
      } else if (type === 'success') {
        navigator.vibrate([25, 50, 25]);
      } else if (type === 'warning') {
        navigator.vibrate([40, 40]);
      }
    } catch (e) {}
  };

  // Initialize Mode on Load
  document.addEventListener('DOMContentLoaded', () => {
    const urlParams = new URLSearchParams(window.location.search);
    const modeParam = urlParams.get('mode');
    const savedMode = modeParam || localStorage.getItem('voter_app_mode') || 'slip';
    window.setAppMode(savedMode);
  });

})();

