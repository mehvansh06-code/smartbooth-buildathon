/**
 * pwa_manager.js - PWA Service Worker, Mobile Native Bridge & Hardware Interceptor
 * Part of SmartBooth PWA
 *
 * Capabilities:
 * 1. PWA Service Worker lifecycle & update detection toast
 * 2. Android Hardware Back Button Interception (Modals & Sidebar)
 * 3. Virtual Keyboard Sentinel (prevents layout jumping on mobile)
 * 4. iOS Multi-Touch & Pinch Zoom Elimination
 * 5. PWA Install Prompt Handler
 */

(function () {
  'use strict';

  // -------------------------------------------------------------
  // 1. Service Worker Lifecycle & Boundary-Safe Background Updates
  // -------------------------------------------------------------
  let swRefreshing = false;
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (swRefreshing) return;
      swRefreshing = true;
      console.log('PWA: Service worker controller changed. Refreshing page for latest UI updates...');
      window.location.reload();
    });
  }

  function handleServiceWorkerUpdate(registration) {
    if (!registration || !registration.waiting) return;

    // Check if user is currently interacting with an open modal or active input
    const activeModal = typeof getActiveOpenModal === 'function' ? getActiveOpenModal() : null;
    const activeInput = document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);

    if (!activeModal && !activeInput) {
      // Safe boundary: activate new worker cleanly
      registration.waiting.postMessage({ type: 'SKIP_WAITING' });
    } else {
      // Defer update activation until natural page transition so user input is never interrupted
      window.addEventListener('pagehide', () => {
        if (registration && registration.waiting) {
          registration.waiting.postMessage({ type: 'SKIP_WAITING' });
        }
      }, { once: true });
    }
  }

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').then((registration) => {
        // Proactively check for service worker byte-level updates immediately
        try { registration.update(); } catch (_) {}

        if (registration.waiting) {
          handleServiceWorkerUpdate(registration);
        }

        registration.addEventListener('updatefound', () => {
          const installingWorker = registration.installing;
          if (!installingWorker) return;
          installingWorker.addEventListener('statechange', () => {
            if (installingWorker.state === 'installed' && navigator.serviceWorker.controller) {
              handleServiceWorkerUpdate(registration);
            }
          });
        });
      }).catch((err) => {
        console.warn('PWA Service Worker registration notice:', err);
      });
    });

    // Proactively check for updates on mobile when user switches back or unlocks screen
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        navigator.serviceWorker.getRegistration().then((reg) => {
          if (reg) {
            try { reg.update(); } catch (_) {}
          }
        }).catch(() => {});
      }
    });
  }

  // -------------------------------------------------------------
  // 2. Android Hardware Back Button Interceptor (History & Modals)
  // -------------------------------------------------------------
  let hasModalHistoryState = false;
  let programmaticBack = false;

  // Reset any leftover modal state on initial page entry
  try {
    if (window.history.state && window.history.state.modal) {
      window.history.replaceState({ modal: false }, '');
    }
  } catch (_) {}

  // Helper to discover the topmost active open modal or drawer
  function getActiveOpenModal() {
    // A. Mobile sidebar navigation
    const sidebar = document.getElementById('appSidebar');
    if (sidebar && sidebar.classList.contains('mobile-open')) {
      return {
        type: 'sidebar',
        close: () => {
          if (typeof window.closeMobileSidebar === 'function') {
            window.closeMobileSidebar();
          } else {
            sidebar.classList.remove('mobile-open');
            const bd = document.getElementById('sidebarBackdrop');
            if (bd) bd.classList.add('hidden');
          }
        }
      };
    }

    // B. Known modal elements in application
    const modalDefinitions = [
      { id: 'mobileWhatsAppModal', closeFn: 'closeMobileWhatsAppModal' },
      { id: 'slipPreviewModal', closeFn: 'closeSlipModal' },
      { id: 'markOutsideModal', closeFn: 'closeMarkOutsideModal' },
      { id: 'outsideVotersModal', closeFn: 'closeOutsideVotersModal' },
      { id: 'voterProfileModal', closeFn: 'closeVoterProfileModal' },
      { id: 'printPhoneModal', closeFn: 'closePrintModal' },
      { id: 'historyModal', closeFn: 'closeHistoryModal' },
      { id: 'boothModal', closeFn: 'closeBoothModal' },
      { id: 'editFamilyIdModal', closeFn: 'closeEditFamilyIdModal' },
      { id: 'editHistoryModal', closeFn: 'closeEditHistoryModal' },
      { id: 'karyakartaModal', closeFn: 'closeKaryakartaModal' },
      { id: 'assignKaryakartaModal', closeFn: 'closeAssignKaryakartaModal' }
    ];

    for (const m of modalDefinitions) {
      const el = document.getElementById(m.id);
      if (el && !el.classList.contains('hidden') && el.offsetParent !== null) {
        return {
          type: m.id,
          close: () => {
            if (m.closeFn && typeof window[m.closeFn] === 'function') {
              window[m.closeFn]();
            } else {
              el.classList.add('hidden');
            }
          }
        };
      }
    }

    // C. Fallback for generic bottom sheets or dialogs
    const genericModals = document.querySelectorAll('.bottom-sheet-modal:not(.hidden), [id$="Modal"]:not(.hidden)');
    for (const el of genericModals) {
      if (el.id !== 'appSidebar' && !el.classList.contains('hidden') && el.offsetParent !== null) {
        return {
          type: el.id || 'genericModal',
          close: () => el.classList.add('hidden')
        };
      }
    }

    return null;
  }

  // Push modal history state safely
  function pushModalStateIfNeeded() {
    if (!hasModalHistoryState) {
      window.history.pushState({ modal: true }, '');
      hasModalHistoryState = true;
    }
  }
  window.pushModalHistoryState = pushModalStateIfNeeded;

  // Watch DOM mutations to automatically track modal visibility changes
  const modalObserver = new MutationObserver(() => {
    const activeModal = getActiveOpenModal();
    if (activeModal) {
      if (!hasModalHistoryState) {
        pushModalStateIfNeeded();
      }
    } else {
      if (hasModalHistoryState) {
        // Modals were closed via UI buttons; unwind history stack cleanly
        hasModalHistoryState = false;
        programmaticBack = true;
        window.history.back();
      }
    }
  });

  // Start observing once DOM is ready
  if (document.body) {
    modalObserver.observe(document.body, {
      attributes: true,
      attributeFilter: ['class', 'style'],
      subtree: true
    });
  } else {
    document.addEventListener('DOMContentLoaded', () => {
      modalObserver.observe(document.body, {
        attributes: true,
        attributeFilter: ['class', 'style'],
        subtree: true
      });
    });
  }

  // Intercept Popstate (Hardware back button on Android or browser back)
  window.addEventListener('popstate', (e) => {
    if (programmaticBack) {
      programmaticBack = false;
      return;
    }

    const activeModal = getActiveOpenModal();
    if (activeModal) {
      // Prevent exiting the app/page: close the modal instead
      hasModalHistoryState = false;
      activeModal.close();
    } else {
      hasModalHistoryState = false;
    }
  });

  // -------------------------------------------------------------
  // 3. Virtual Keyboard Sentinel
  // -------------------------------------------------------------
  let keyboardBlurTimer = null;

  document.addEventListener('focusin', (e) => {
    const tag = e.target && e.target.tagName ? e.target.tagName.toLowerCase() : '';
    if (['input', 'select', 'textarea'].includes(tag) && window.innerWidth <= 768) {
      if (keyboardBlurTimer) {
        clearTimeout(keyboardBlurTimer);
        keyboardBlurTimer = null;
      }
      document.body.classList.add('keyboard-open');

      // Scroll focused input into center view smoothly
      setTimeout(() => {
        try {
          e.target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } catch (_) {}
      }, 300);
    }
  });

  document.addEventListener('focusout', (e) => {
    const tag = e.target && e.target.tagName ? e.target.tagName.toLowerCase() : '';
    if (['input', 'select', 'textarea'].includes(tag)) {
      keyboardBlurTimer = setTimeout(() => {
        document.body.classList.remove('keyboard-open');
      }, 150);
    }
  });

  // Dual sentinel using VisualViewport API on supporting mobile browsers
  if (window.visualViewport) {
    const screenHeight = window.innerHeight;
    window.visualViewport.addEventListener('resize', () => {
      if (window.innerWidth <= 768) {
        const vpHeight = window.visualViewport.height;
        if (vpHeight < screenHeight * 0.75) {
          document.body.classList.add('keyboard-open');
        } else if (!document.activeElement || !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
          document.body.classList.remove('keyboard-open');
        }
      }
    });
  }

  // -------------------------------------------------------------
  // 4. iOS Multi-Touch Gesture Interception (Anti-Zoom)
  // -------------------------------------------------------------
  // Prevent iOS Safari pinch-to-zoom gestures via native gesture events without interfering with scrolling
  document.addEventListener('gesturestart', (e) => e.preventDefault(), { passive: false });
  document.addEventListener('gesturechange', (e) => e.preventDefault(), { passive: false });
  document.addEventListener('gestureend', (e) => e.preventDefault(), { passive: false });

  // -------------------------------------------------------------
  // 5. PWA Install Prompt Handler & Mobile Native Bridge
  // -------------------------------------------------------------
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  let deferredPrompt = null;

  function isIosDevice() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent) || 
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }

  function hideInstallElements() {
    const banner = document.getElementById('pwaInstallBanner');
    if (banner) {
      banner.classList.add('hidden');
      banner.classList.remove('flex');
    }
    const loginSection = document.getElementById('loginPwaInstallSection');
    if (loginSection) {
      loginSection.classList.add('hidden');
    }
    const modal = document.getElementById('pwaGuideModal');
    if (modal) {
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }
  }

  function showPwaGuideModal() {
    const modal = document.getElementById('pwaGuideModal');
    if (!modal) return;

    const iosGuide = document.getElementById('pwaIosGuide');
    const androidGuide = document.getElementById('pwaAndroidGuide');

    if (isIosDevice()) {
      if (iosGuide) iosGuide.classList.remove('hidden');
      if (androidGuide) androidGuide.classList.add('hidden');
    } else {
      if (androidGuide) androidGuide.classList.remove('hidden');
      if (iosGuide) iosGuide.classList.add('hidden');
    }

    modal.classList.remove('hidden');
    modal.classList.add('flex');
  }

  window.closePwaGuideModal = function () {
    const modal = document.getElementById('pwaGuideModal');
    if (modal) {
      modal.classList.add('hidden');
      modal.classList.remove('flex');
    }
  };

  async function triggerPwaInstall() {
    if (window.triggerHaptic) window.triggerHaptic('light');

    if (deferredPrompt) {
      try {
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        if (outcome === 'accepted') {
          try { localStorage.setItem('pwa_install_dismissed', 'true'); } catch (_) {}
          hideInstallElements();
        }
      } catch (err) {
        console.warn('Install prompt error:', err);
      }
      deferredPrompt = null;
    } else {
      // Either iOS Safari or browser where prompt wasn't triggered: show visual steps
      showPwaGuideModal();
    }
  }
  window.triggerPwaInstall = triggerPwaInstall;

  window.dismissPwaInstallBanner = function () {
    try {
      localStorage.setItem('pwa_install_dismissed', 'true');
    } catch (_) {}
    hideInstallElements();
  };

  window.maybeShowPwaInstallPrompt = function () {
    if (isStandalone || !deferredPrompt) return;
    try {
      if (localStorage.getItem('pwa_install_dismissed') === 'true') return;
      if (localStorage.getItem('pwa_install_prompt_shown') === 'true') return;
      localStorage.setItem('pwa_install_prompt_shown', 'true');
    } catch (_) {}

    const banner = document.getElementById('pwaInstallBanner');
    if (banner) {
      banner.classList.remove('hidden');
      banner.classList.add('flex');
    }
  };

  function setupInstallTriggers() {
    // If already installed in standalone mode, hide all prompts
    if (isStandalone) {
      hideInstallElements();
      return;
    }

    // Login page install button
    const loginBtn = document.getElementById('loginPwaInstallBtn');
    if (loginBtn && !loginBtn._bound) {
      loginBtn._bound = true;
      loginBtn.addEventListener('click', triggerPwaInstall);
    }

    // Base header install banner button
    const actionBtn = document.getElementById('pwaInstallActionBtn');
    if (actionBtn && !actionBtn._bound) {
      actionBtn._bound = true;
      actionBtn.addEventListener('click', triggerPwaInstall);
    }

    // Close guide modal if clicking backdrop
    const modal = document.getElementById('pwaGuideModal');
    if (modal && !modal._boundBackdrop) {
      modal._boundBackdrop = true;
      modal.addEventListener('click', (e) => {
        if (e.target === modal) {
          window.closePwaGuideModal();
        }
      });
    }
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    // If already running as an installed standalone app, suppress
    if (isStandalone) return;

    e.preventDefault();
    deferredPrompt = e;

    setupInstallTriggers();

    // Check if user previously dismissed or installed prompt
    let dismissed = false;
    try {
      dismissed = localStorage.getItem('pwa_install_dismissed') === 'true';
    } catch (_) {}

    // Show only once if not previously dismissed; never nag on every reload
    if (!dismissed) {
      let shown = false;
      try {
        shown = localStorage.getItem('pwa_install_prompt_shown') === 'true';
      } catch (_) {}

      if (!shown) {
        try { localStorage.setItem('pwa_install_prompt_shown', 'true'); } catch (_) {}
        const banner = document.getElementById('pwaInstallBanner');
        if (banner) {
          banner.classList.remove('hidden');
          banner.classList.add('flex');
        }
      }
    }
  });

  window.addEventListener('appinstalled', () => {
    try { localStorage.setItem('pwa_install_dismissed', 'true'); } catch (_) {}
    hideInstallElements();
    console.log('Smart Booth PWA installed successfully');
  });

  // Initialize triggers when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupInstallTriggers);
  } else {
    setupInstallTriggers();
  }

})();

