/**
 * offline_db.js - High-Speed Client-Side IndexedDB Engine & Sync Outbox
 * Part of SmartBooth Platform
 *
 * Stores downloaded booth voters locally in the phone's browser database.
 * Enables 0-5ms searches and queues profile edits (phone, caste, category) when offline.
 */

(function () {
  'use strict';

  const DB_NAME = 'smartbooth_offline_v1';
  const DB_VERSION = 1;

  let dbPromise = null;

  // Open / Initialize IndexedDB
  function getDB() {
    if (dbPromise) return dbPromise;

    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);

      request.onupgradeneeded = (e) => {
        const db = e.target.result;

        // Store 1: voters
        if (!db.objectStoreNames.contains('voters')) {
          const voterStore = db.createObjectStore('voters', { keyPath: 'id' });
          voterStore.createIndex('by_sl', 'sl_no', { unique: false });
          voterStore.createIndex('by_part_sl', ['part_no', 'sl_no'], { unique: false });
          voterStore.createIndex('by_ward_sl', ['ward_no', 'sl_no'], { unique: false });
          voterStore.createIndex('by_epic', 'epic_no', { unique: false });
          voterStore.createIndex('by_name_hi', 'name_hi', { unique: false });
        }

        // Store 2: mutations outbox
        if (!db.objectStoreNames.contains('outbox')) {
          const outboxStore = db.createObjectStore('outbox', { keyPath: 'id' });
          outboxStore.createIndex('by_timestamp', 'timestamp', { unique: false });
        }

        // Store 3: cached metadata (active downloaded booth/ward info)
        if (!db.objectStoreNames.contains('meta')) {
          db.createObjectStore('meta', { keyPath: 'key' });
        }
      };

      request.onsuccess = (e) => resolve(e.target.result);
      request.onerror = (e) => {
        console.error('IndexedDB open error:', e.target.error);
        reject(e.target.error);
      };
    });

    return dbPromise;
  }

  // Save full downloaded voters list to local IndexedDB
  async function saveDownloadedBooth(boothIdentifier, votersList, label = '', version = '') {
    const db = await getDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(['voters', 'meta'], 'readwrite');
      const voterStore = tx.objectStore('voters');
      const metaStore = tx.objectStore('meta');

      // Clear existing cached voters to keep memory compact
      voterStore.clear();

      for (const v of votersList) {
        if (!v.id && v.voter_id) v.id = v.voter_id;
        voterStore.put(v);
      }

      metaStore.put({
        key: 'active_booth',
        identifier: boothIdentifier,
        label: label || `बूथ / वार्ड ${boothIdentifier}`,
        count: votersList.length,
        version: version || '',
        downloadedAt: Date.now()
      });

      tx.oncomplete = () => {
        if (typeof window.triggerHaptic === 'function') window.triggerHaptic('success');
        resolve(true);
      };
      tx.onerror = (e) => reject(e.target.error);
    });
  }

  // Get active cached booth metadata
  async function getCachedBoothMeta() {
    try {
      const db = await getDB();
      return new Promise((resolve) => {
        const tx = db.transaction(['meta'], 'readonly');
        const req = tx.objectStore('meta').get('active_booth');
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => resolve(null);
      });
    } catch (_) {
      return null;
    }
  }

  // Clear cached booth data from local phone
  async function clearCachedBooth() {
    const db = await getDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(['voters', 'meta'], 'readwrite');
      tx.objectStore('voters').clear();
      tx.objectStore('meta').delete('active_booth');
      tx.oncomplete = () => resolve(true);
      tx.onerror = (e) => reject(e.target.error);
    });
  }

  // Search voters from local IndexedDB in < 5ms with multi-word & phone/epic support
  async function searchLocalVoters(query = {}) {
    const db = await getDB();
    return new Promise((resolve) => {
      const tx = db.transaction(['voters'], 'readonly');
      const store = tx.objectStore('voters');
      const req = store.getAll();

      req.onsuccess = () => {
        const allVoters = req.result || [];
        if (!allVoters.length) {
          return resolve({ voters: [], total: 0 });
        }

        const rawQ = (query.name || query.q || '').trim();
        const nameQ = rawQ.toLowerCase();
        const relQ = (query.relative || '').trim().toLowerCase();
        
        // Distinguish serial numbers (1-4 digits) from phone numbers (5+ digits)
        const isPureDigits = /^\d+$/.test(nameQ);
        const isSerial = isPureDigits && nameQ.length <= 4;
        const serialNum = isSerial ? parseInt(nameQ, 10) : null;
        const isPhone = isPureDigits && nameQ.length >= 5;
        
        const partVal = query.part_no ? parseInt(query.part_no, 10) : null;
        const wardVal = query.ward_no ? String(query.ward_no).trim() : null;
        const gender = (query.gender || '').toUpperCase();
        const caste = (query.caste || '').trim();

        // Tokenize query words for multi-term search e.g. "सुमित दुनीवाल"
        const terms = nameQ.split(/\s+/).filter(Boolean);

        const filtered = allVoters.filter((v) => {
          // 1. Serial number match
          if (serialNum !== null) {
            if (parseInt(v.sl_no, 10) !== serialNum) return false;
          }

          // 2. Part / Ward filters
          if (partVal && parseInt(v.part_no, 10) !== partVal) return false;
          if (wardVal && String(v.ward_no || '').trim() !== wardVal) return false;

          // 3. Gender & Caste
          if (gender && String(v.gender || '').toUpperCase() !== gender) return false;
          if (caste && String(v.caste_tag || '').trim() !== caste) return false;

          // 4. Phone query
          if (isPhone) {
            const vPhone = String(v.contact_phone || '');
            if (!vPhone.includes(nameQ)) return false;
          }

          // 5. Name, Relative & Multi-word text match
          if (!serialNum && !isPhone && terms.length > 0) {
            const vNameHi = String(v.name_hi || '').toLowerCase();
            const vNameEn = String(v.name_en || '').toLowerCase();
            const vRelHi = String(v.rel_name_hi || '').toLowerCase();
            const vRelEn = String(v.rel_name_en || '').toLowerCase();
            const vEpic = String(v.epic_no || '').toLowerCase();
            const vHead = String(v.family_head_name || '').toLowerCase();
            const fullDoc = `${vNameHi} ${vNameEn} ${vRelHi} ${vRelEn} ${vEpic} ${vHead}`;

            // All search terms must be matched
            for (const t of terms) {
              if (!fullDoc.includes(t)) return false;
            }
          }

          // 6. Dedicated relative query
          if (relQ) {
            const vRelHi = String(v.rel_name_hi || '').toLowerCase();
            const vRelEn = String(v.rel_name_en || '').toLowerCase();
            if (!vRelHi.includes(relQ) && !vRelEn.includes(relQ)) return false;
          }

          return true;
        });

        // Sort by serial number ascending
        filtered.sort((a, b) => (parseInt(a.sl_no, 10) || 0) - (parseInt(b.sl_no, 10) || 0));

        resolve({
          voters: filtered.slice(0, 100),
          total: filtered.length
        });
      };

      req.onerror = () => resolve({ voters: [], total: 0 });
    });
  }

  // Queue an offline profile edit (phone, caste, category) into Outbox & update local voter cache
  async function queueOfflineMutation(voterId, payload) {
    const db = await getDB();
    const mutationId = 'mut_' + Date.now() + '_' + voterId;
    const item = {
      id: mutationId,
      voter_id: voterId,
      payload: payload,
      timestamp: Date.now()
    };

    return new Promise((resolve, reject) => {
      const tx = db.transaction(['outbox', 'voters'], 'readwrite');
      tx.objectStore('outbox').put(item);

      // Optimistically update local voter record
      const voterStore = tx.objectStore('voters');
      const getReq = voterStore.get(voterId);
      getReq.onsuccess = () => {
        if (getReq.result) {
          const updated = Object.assign({}, getReq.result, payload);
          voterStore.put(updated);
        }
      };

      tx.oncomplete = () => {
        updateOutboxBadge();
        resolve(item);
      };
      tx.onerror = (e) => reject(e.target.error);
    });
  }

  // Queue any raw mutation item (e.g. WHATSAPP_DISPATCH) directly into outbox
  async function queueRawMutation(rawItem) {
    const db = await getDB();
    if (!rawItem.id) {
      rawItem.id = 'raw_' + Date.now() + '_' + Math.random().toString(36).substring(2, 7);
    }
    if (!rawItem.timestamp) {
      rawItem.timestamp = Date.now();
    }
    return new Promise((resolve, reject) => {
      const tx = db.transaction(['outbox'], 'readwrite');
      tx.objectStore('outbox').put(rawItem);
      tx.oncomplete = () => {
        updateOutboxBadge();
        resolve(rawItem);
      };
      tx.onerror = (e) => reject(e.target.error);
    });
  }

  // Get count of pending outbox mutations
  async function getOutboxCount() {
    try {
      const db = await getDB();
      return new Promise((resolve) => {
        const tx = db.transaction(['outbox'], 'readonly');
        const req = tx.objectStore('outbox').count();
        req.onsuccess = () => resolve(req.result || 0);
        req.onerror = () => resolve(0);
      });
    } catch (_) {
      return 0;
    }
  }

  // Update outbox badge UI and notify listeners
  async function updateOutboxBadge() {
    try {
      const count = await getOutboxCount();
      const badge = document.getElementById('offlineOutboxBadge');
      if (badge) {
        if (count > 0) {
          badge.textContent = count;
          badge.classList.remove('hidden');
        } else {
          badge.classList.add('hidden');
        }
      }
      window.dispatchEvent(new CustomEvent('smartbooth:outbox-updated', { detail: { count } }));
    } catch (_) {}
  }

  // Trigger sync of all pending mutations to server
  async function syncOutboxToServer() {
    if (!navigator.onLine) return { synced: 0, pending: await getOutboxCount() };

    const db = await getDB();
    const mutations = await new Promise((resolve) => {
      const tx = db.transaction(['outbox'], 'readonly');
      const req = tx.objectStore('outbox').getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => resolve([]);
    });

    if (!mutations.length) return { synced: 0, pending: 0 };

    try {
      const curTenant = window.CURRENT_TENANT || '';
      const url = '/api/voter/offline_sync' + (curTenant ? '?tenant=' + encodeURIComponent(curTenant) : '');
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mutations: mutations })
      });

      if (!resp.ok) return { synced: 0, pending: mutations.length };

      const data = await resp.json();
      const syncedIds = data.synced_ids || [];

        if (syncedIds.length > 0) {
          // Delete acknowledged mutations from local outbox silently
          const delTx = db.transaction(['outbox'], 'readwrite');
          const store = delTx.objectStore('outbox');
          for (const id of syncedIds) {
            store.delete(id);
          }
          await new Promise((r) => {
            delTx.oncomplete = r;
          });
          updateOutboxBadge();
        }

        return { synced: syncedIds.length, pending: await getOutboxCount() };
      } catch (err) {
        console.warn('Sync outbox failed (will retry silently):', err);
        return { synced: 0, pending: mutations.length };
      }
    }

  // Request Persistent Storage (Prevents Safari/Chrome from ever auto-purging the booth data)
  if (navigator.storage && navigator.storage.persist) {
    navigator.storage.persist().then((persistent) => {
      if (persistent) {
        console.log('SmartBooth: Persistent storage granted.');
      }
    });
  }

  // Listen for online events to auto-sync queued mutations
  window.addEventListener('online', () => {
    syncOutboxToServer();
  });

  // When app returns to foreground (user unlocks phone or switches back to tab)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && navigator.onLine) {
      syncOutboxToServer();
    }
  });

  window.addEventListener('focus', () => {
    if (navigator.onLine) {
      syncOutboxToServer();
    }
  });

  // Background heartbeat sync every 30 seconds (catches spotty reconnects silently)
  setInterval(() => {
    if (navigator.onLine) {
      syncOutboxToServer();
    }
  }, 30000);


  // Automatic background offline voter sync (zero manual clicks required)
  async function autoSyncCampaignBundle(force = false) {
    if (!navigator.onLine) return;
    const curTenant = window.CURRENT_TENANT || new URLSearchParams(window.location.search).get('tenant') || '';
    if (!curTenant) return;

    try {
      const verUrl = '/api/voters/bundle_version?tenant=' + encodeURIComponent(curTenant);
      const verResp = await fetch(verUrl);
      if (!verResp.ok) return;
      const verData = await verResp.json();
      if (!verData.success || !verData.supports_full_cache) return;

      const db = await getDB();
      const cachedMeta = await new Promise((resolve) => {
        const tx = db.transaction(['meta'], 'readonly');
        const req = tx.objectStore('meta').get('active_bundle_' + curTenant);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => resolve(null);
      });

      if (!force && cachedMeta && cachedMeta.version === verData.version && cachedMeta.count > 0) {
        console.log('SmartBooth: Offline voter cache ready (' + cachedMeta.count + ' voters, ' + verData.version + ').');
        return;
      }

      console.log('SmartBooth: Automatically caching voter list for offline use (' + curTenant + ')...');
      const bundleUrl = '/api/voters/campaign_bundle?tenant=' + encodeURIComponent(curTenant);
      const bundleResp = await fetch(bundleUrl);
      if (!bundleResp.ok) return;
      const bundleData = await bundleResp.json();
      if (!bundleData.success || !Array.isArray(bundleData.voters)) return;

      const tx = db.transaction(['voters', 'meta'], 'readwrite');
      const voterStore = tx.objectStore('voters');
      const metaStore = tx.objectStore('meta');

      voterStore.clear();
      for (const v of bundleData.voters) {
        if (!v.id && v.voter_id) v.id = v.voter_id;
        voterStore.put(v);
      }

      const metaObj = {
        key: 'active_bundle_' + curTenant,
        tenant: curTenant,
        version: verData.version,
        count: bundleData.voters.length,
        downloadedAt: Date.now()
      };
      metaStore.put(metaObj);
      metaStore.put({ key: 'active_booth', count: bundleData.voters.length, downloadedAt: Date.now() });

      await new Promise((resolve, reject) => {
        tx.oncomplete = resolve;
        tx.onerror = reject;
      });

      console.log('SmartBooth: Offline voter search ready with ' + bundleData.voters.length + ' voters.');
    } catch (e) {
      console.warn('SmartBooth: Background offline sync notice:', e);
    }
  }

  // Auto-trigger background download 1.2s after page load so it never impacts UI rendering
  if (typeof window !== 'undefined') {
    window.addEventListener('load', () => {
      setTimeout(autoSyncCampaignBundle, 1200);
    });
    window.addEventListener('online', () => {
      autoSyncCampaignBundle();
    });
  }

  // Export to global window
  window.SmartBoothOfflineDB = {
    saveDownloadedBooth,
    autoSyncCampaignBundle,
    getCachedBoothMeta,
    clearCachedBooth,
    searchLocalVoters,
    queueOfflineMutation,
    queueRawMutation,
    getOutboxCount,
    syncOutboxToServer,
    updateOutboxBadge
  };

  document.addEventListener('DOMContentLoaded', () => {
    updateOutboxBadge();
  });
})();
