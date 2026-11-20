/**
 * admin_campaign_wizard.js
 * Streamlined Super Admin & Reseller Controller:
 * - Rajasthan SEC 2026 Master DB Voter Linker (Workspace 3)
 * - Safe Campaign Delete / Archive & Restore Modals
 * - Credential Clipboard Helpers
 */

(function() {
    'use strict';

    let masterSyncDistrictsLoaded = false;

    // Helper: Safely trigger admin toast
    function notify(msg, type = 'info') {
        if (typeof window.showAdminToast === 'function') {
            window.showAdminToast(msg, type);
        } else if (typeof window.showToast === 'function') {
            window.showToast(msg, type);
        } else {
            alert(msg);
        }
    }

    // --- RAJASTHAN SEC 2026 DROPDOWNS (WORKSPACE 3 MASTER SYNC) ---
    window.initMasterSyncDropdowns = async function() {
        if (masterSyncDistrictsLoaded) return;
        try {
            const res = await fetch('/api/admin/rajasthan/districts');
            const data = await res.json();
            if (res.ok && data.success && Array.isArray(data.districts)) {
                const sel = document.getElementById('masterSyncDistrictSelect');
                if (sel) {
                    sel.innerHTML = '<option value="">-- जिला चुनें (Select District) --</option>' +
                        data.districts.map(d => `<option value="${d.district}">${d.district} (${d.gp_count} GPs)</option>`).join('');
                }
                masterSyncDistrictsLoaded = true;
            }
        } catch (e) {
            console.error("Master sync districts load error:", e);
        }
    };

    window.onMasterSyncDistrictChange = async function(dist) {
        const blockSel = document.getElementById('masterSyncBlockSelect');
        const gpSel = document.getElementById('masterSyncGpSelect');
        if (!dist) {
            if (blockSel) { blockSel.innerHTML = '<option value="">-- पहले जिला चुनें --</option>'; blockSel.disabled = true; }
            if (gpSel) { gpSel.innerHTML = '<option value="">-- पहले समिति चुनें --</option>'; gpSel.disabled = true; }
            return;
        }

        try {
            blockSel.disabled = true;
            blockSel.innerHTML = '<option value="">लोड हो रहा है...</option>';
            const res = await fetch(`/api/admin/rajasthan/blocks?district=${encodeURIComponent(dist)}`);
            const data = await res.json();
            blockSel.innerHTML = '<option value="">-- पंचायत समिति चुनें --</option>' +
                (data.blocks || []).map(b => `<option value="${b.ps_name}">${b.ps_name} (${b.gp_count} GPs)</option>`).join('');
            blockSel.disabled = false;
        } catch (e) {
            console.error("Master sync blocks error:", e);
        }
    };

    window.onMasterSyncBlockChange = async function(block) {
        const dist = document.getElementById('masterSyncDistrictSelect')?.value || '';
        const gpSel = document.getElementById('masterSyncGpSelect');
        if (!block || !dist) {
            if (gpSel) { gpSel.innerHTML = '<option value="">-- पहले समिति चुनें --</option>'; gpSel.disabled = true; }
            return;
        }

        try {
            gpSel.disabled = true;
            gpSel.innerHTML = '<option value="">लोड हो रहा है...</option>';
            const res = await fetch(`/api/admin/rajasthan/gps?district=${encodeURIComponent(dist)}&block=${encodeURIComponent(block)}`);
            const data = await res.json();
            gpSel.innerHTML = '<option value="">-- ग्राम पंचायत चुनें --</option>' +
                (data.gps || []).map(g => `<option value="${g.gp_name}">${g.has_records ? '★ ' : ''}${g.gp_name}</option>`).join('');
            gpSel.disabled = false;
        } catch (e) {
            console.error("Master sync GPs error:", e);
        }
    };

    // --- 1-CLICK MASTER VOTER DATABASE SYNC ---
    window.executeMasterVoterSync = async function() {
        const slugSel = document.getElementById('masterSyncCampaignSelect');
        const distSel = document.getElementById('masterSyncDistrictSelect');
        const blockSel = document.getElementById('masterSyncBlockSelect');
        const gpSel = document.getElementById('masterSyncGpSelect');
        const resultBox = document.getElementById('masterSyncResultBox');
        const btn = document.getElementById('btnMasterSync');
        const btnText = document.getElementById('btnMasterSyncText');

        const slug = slugSel ? slugSel.value : '';
        const district = distSel ? distSel.value : '';
        const block = blockSel ? blockSel.value : '';
        const gp = gpSel ? gpSel.value : '';

        if (!slug) {
            notify("कृपया पहले लक्ष्य अभियान चुनें", "warning");
            slugSel?.focus();
            return;
        }

        if (btn) btn.disabled = true;
        if (btnText) btnText.innerText = "मास्टर डेटाबेस से लिंक किया जा रहा है...";
        if (resultBox) {
            resultBox.classList.remove('hidden');
            resultBox.innerHTML = `<div class="text-slate-600 animate-pulse">⏳ कनेक्टिंग: PostgreSQL 17 'rajasthan_voters.voters_master' (${slug})...</div>`;
        }

        try {
            const res = await fetch(`/api/admin/campaigns/${encodeURIComponent(slug)}/sync_master_voters`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ district, block, gp })
            });
            const data = await res.json().catch(() => ({}));

            if (res.ok && data.success !== false) {
                const countFormatted = (Number(data.voters_synced || data.synced_count || data.count) || 0).toLocaleString('en-IN');
                if (resultBox) {
                    resultBox.innerHTML = `
                        <div class="text-emerald-700 font-bold">✓ सफलतापूर्वक संपन्न (HTTP 200 OK)</div>
                        <div class="text-slate-800">लिंक रिकॉर्ड: <strong>${countFormatted}</strong> मतदाता</div>
                        <div class="text-slate-600">अभियान: <span class="font-mono text-indigo-700">${slug}</span> ${district ? `| क्षेत्र: ${district} / ${gp}` : ''}</div>
                    `;
                }
                notify(`✅ सफलतापूर्वक ${countFormatted} मतदाता रिकॉर्ड अभियान '${slug}' से लिंक हुए!`, 'success');
                if (typeof window.loadCampaigns === 'function') window.loadCampaigns();
                if (typeof window.loadPartitionHealth === 'function') window.loadPartitionHealth();
            } else {
                throw new Error(data.error || 'Master sync request failed');
            }
        } catch (err) {
            if (resultBox) {
                resultBox.innerHTML = `<div class="text-rose-700 font-bold">❌ लिंकिंग त्रुटि: ${err.message}</div>`;
            }
            notify(`❌ मास्टर डेटाबेस त्रुटि: ${err.message}`, 'error');
        } finally {
            if (btn) btn.disabled = false;
            if (btnText) btnText.innerText = "मास्टर डेटाबेस से मतदाता जोड़ें (Sync & Link Voters)";
        }
    };

    // --- CREDENTIALS COPY HELPER ---
    window.copyCreds = function(type, creds) {
        if (!creds) return;
        const slug = creds.campaign_slug || creds.tenant_slug || '';
        let u = '', p = '';
        if (type === 'admin') {
            u = creds.admin_username || `${slug}_admin`;
            p = creds.admin_password || 'Admin@2026';
        } else {
            u = creds.operator_username || slug;
            p = creds.operator_password || 'Operator@2026';
        }
        const txt = `Smart Booth Login (${type.toUpperCase()}):\nURL: ${window.location.origin}/?tenant=${slug}\nUsername: ${u}\nPassword: ${p}`;
        navigator.clipboard.writeText(txt).then(() => {
            notify(`📋 ${type.toUpperCase()} लॉगिन क्रेडेंशियल कॉपी किए गए`, 'success');
        }).catch(() => {
            alert(`Username: ${u}\nPassword: ${p}`);
        });
    };

    // --- DELETE / ARCHIVE CAMPAIGN CONTROLLER ---
    window.openDeleteCampaignModal = function(slug, candName, voterCount, status, isReseller = false) {
        const modal = document.getElementById('deleteCampaignModal');
        if (!modal) return;

        const permOption = document.getElementById('lblModePermanent');
        if (permOption) {
            if (isReseller) {
                permOption.classList.add('hidden');
            } else {
                permOption.classList.remove('hidden');
            }
        }

        const targetSlugEl = document.getElementById('delTargetSlug');
        const slugDispEl = document.getElementById('delModalSlugDisplay');
        const candEl = document.getElementById('delModalCandName');
        const countEl = document.getElementById('delModalVoterCount');
        const statusBadge = document.getElementById('delModalStatusBadge');
        const reqSlugText = document.getElementById('delRequiredSlugText');
        const confirmInput = document.getElementById('delConfirmInput');

        if (targetSlugEl) targetSlugEl.value = slug || '';
        if (slugDispEl) slugDispEl.innerText = slug || '';
        if (candEl) candEl.innerText = candName || slug || '';
        if (countEl) countEl.innerText = `${(Number(voterCount) || 0).toLocaleString('en-IN')} कुल मतदाता`;
        if (reqSlugText) reqSlugText.innerText = slug || '';
        if (confirmInput) confirmInput.value = '';

        const st = (status || 'ACTIVE').toUpperCase();
        if (statusBadge) {
            statusBadge.innerText = st;
            statusBadge.className = `px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wider ${
                st === 'ACTIVE' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' :
                st === 'ARCHIVED' ? 'bg-slate-100 text-slate-600 border border-slate-200' :
                'bg-amber-50 text-amber-700 border border-amber-200'
            }`;
        }

        // Reset to Archive mode by default
        const archiveRadio = document.querySelector('input[name="delCampaignMode"][value="archive"]');
        if (archiveRadio) archiveRadio.checked = true;
        window.onDelModeChange('archive');

        modal.classList.remove('hidden');
    };

    window.closeDeleteCampaignModal = function() {
        const modal = document.getElementById('deleteCampaignModal');
        if (modal) modal.classList.add('hidden');
    };

    window.onDelModeChange = function(mode) {
        const confirmBox = document.getElementById('delPermanentConfirmationBox');
        const btn = document.getElementById('btnConfirmDeleteCampaign');
        const btnText = document.getElementById('btnConfirmDeleteText');
        const lblArchive = document.getElementById('lblModeArchive');
        const lblPerm = document.getElementById('lblModePermanent');
        const slug = document.getElementById('delTargetSlug')?.value || '';
        const inputVal = document.getElementById('delConfirmInput')?.value || '';

        if (mode === 'permanent') {
            if (confirmBox) confirmBox.classList.remove('hidden');
            if (lblArchive) {
                lblArchive.classList.remove('border-2', 'border-indigo-500', 'bg-indigo-50/40');
                lblArchive.classList.add('border', 'border-slate-200', 'bg-slate-50/50');
            }
            if (lblPerm) {
                lblPerm.classList.remove('border-slate-200', 'bg-slate-50/50');
                lblPerm.classList.add('border-2', 'border-rose-500', 'bg-rose-50/40');
            }
            if (btn) {
                btn.className = "px-4 py-2.5 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-extrabold shadow-xs cursor-pointer transition flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed";
                btn.disabled = inputVal.trim() !== slug;
            }
            if (btnText) btnText.innerText = "हाँ, स्थायी रूप से हटाएं (Permanent Purge)";
        } else {
            if (confirmBox) confirmBox.classList.add('hidden');
            if (lblPerm) {
                lblPerm.classList.remove('border-2', 'border-rose-500', 'bg-rose-50/40');
                lblPerm.classList.add('border', 'border-slate-200', 'bg-slate-50/50');
            }
            if (lblArchive) {
                lblArchive.classList.remove('border', 'border-slate-200', 'bg-slate-50/50');
                lblArchive.classList.add('border-2', 'border-indigo-500', 'bg-indigo-50/40');
            }
            if (btn) {
                btn.className = "px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-extrabold shadow-xs cursor-pointer transition flex items-center gap-1.5";
                btn.disabled = false;
            }
            if (btnText) btnText.innerText = "सुरक्षित संग्रह करें (Archive)";
        }
    };

    window.onDelConfirmInputChange = function(val) {
        const slug = document.getElementById('delTargetSlug')?.value || '';
        const btn = document.getElementById('btnConfirmDeleteCampaign');
        const mode = document.querySelector('input[name="delCampaignMode"]:checked')?.value || 'archive';
        if (mode === 'permanent' && btn) {
            btn.disabled = (val || '').trim() !== slug.trim();
        }
    };

    window.executeDeleteCampaign = async function() {
        const slug = document.getElementById('delTargetSlug')?.value || '';
        const mode = document.querySelector('input[name="delCampaignMode"]:checked')?.value || 'archive';
        const confirmSlug = document.getElementById('delConfirmInput')?.value || '';
        const btn = document.getElementById('btnConfirmDeleteCampaign');
        const btnText = document.getElementById('btnConfirmDeleteText');

        if (!slug) return;
        if (mode === 'permanent' && confirmSlug.trim() !== slug.trim()) {
            notify("⚠️ पुष्टि के लिए कृपया अभियान का सही स्लग टाइप करें", "warning");
            return;
        }

        if (btn) btn.disabled = true;
        if (btnText) btnText.innerText = mode === 'permanent' ? "स्थायी रूप से हटाया जा रहा है..." : "संग्रह किया जा रहा है...";

        try {
            const res = await fetch(`/api/admin/campaigns/${encodeURIComponent(slug)}/delete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    mode: mode,
                    confirmation_slug: confirmSlug.trim()
                })
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok && data.success) {
                notify(data.message || (mode === 'permanent' ? `अभियान '${slug}' स्थायी रूप से हटा दिया गया` : `अभियान '${slug}' पुरालेख में स्थानांतरित`), 'success');
                window.closeDeleteCampaignModal();
                if (typeof window.loadCampaigns === 'function') {
                    sessionStorage.removeItem('admin_campaigns_cache');
                    window.loadCampaigns(true);
                } else {
                    window.location.reload();
                }
            } else {
                throw new Error(data.error || 'अभियान हटाने में विफलता');
            }
        } catch (err) {
            notify(`❌ त्रुटि: ${err.message}`, 'error');
        } finally {
            if (btn) btn.disabled = false;
            window.onDelModeChange(mode);
        }
    };

    window.restoreCampaign = async function(slug) {
        if (!slug) return;
        try {
            const res = await fetch(`/api/admin/campaigns/${encodeURIComponent(slug)}/restore`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok && data.success) {
                notify(`✅ अभियान '${slug}' पुनः सक्रिय हो गया है`, 'success');
                if (typeof window.loadCampaigns === 'function') {
                    sessionStorage.removeItem('admin_campaigns_cache');
                    window.loadCampaigns(true);
                } else {
                    window.location.reload();
                }
            } else {
                throw new Error(data.error || 'अभियान पुनः सक्रिय करने में विफलता');
            }
        } catch (err) {
            notify(`❌ त्रुटि: ${err.message}`, 'error');
        }
    };

})();
