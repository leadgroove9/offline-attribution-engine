/**
 * LeadGroove Universal Chat & Click ID Tracker JS
 * ------------------------------------------------
 * Embed on client websites to automatically:
 * 1. Capture ad Click IDs (GCLID, FBCLID, MSCLKID, LI_FAT_ID, TTCLID, TWCLID, PIN_CLID, SCCLID, GPTCLID, RDT_CID).
 * 2. Pre-log Click IDs to LeadGroove server (/webhooks/chat-session) with a unique Ref ID (lg_XXXXX).
 * 3. Decorate WhatsApp, Telegram, and Viber chat links on page with (Ref: lg_XXXXX).
 * 4. Inject Click IDs into top 10 Live Chat widgets (LiveChat, Intercom, Drift, Crisp, Tidio, Zendesk, Olark, Zoho, HubSpot, HelpScout).
 */
(function() {
    'use strict';

    // Script Configuration
    const currentScript = document.currentScript || document.querySelector('script[src*="leadgroove"]');
    const clientId = currentScript ? (currentScript.getAttribute('data-client-id') || '1') : '1';
    const appUrl = currentScript ? (currentScript.getAttribute('data-app-url') || 'https://leadgroove-55wt7.ondigitalocean.app') : 'https://leadgroove-55wt7.ondigitalocean.app';

    // Helper: Cookie Setter/Getter
    function setCookie(name, value, days) {
        let expires = "";
        if (days) {
            let date = new Date();
            date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
            expires = "; expires=" + date.toUTCString();
        }
        document.cookie = name + "=" + (value || "") + expires + "; path=/; SameSite=Lax";
    }

    function getCookie(name) {
        let nameEQ = name + "=";
        let ca = document.cookie.split(';');
        for (let i = 0; i < ca.length; i++) {
            let c = ca[i];
            while (c.charAt(0) === ' ') c = c.substring(1, c.length);
            if (c.indexOf(nameEQ) === 0) return c.substring(nameEQ.length, c.length);
        }
        return null;
    }

    // Helper: Generate or Get Ref ID
    function getOrCreateRefId() {
        let refId = localStorage.getItem('lg_ref_id') || getCookie('lg_ref_id');
        if (!refId) {
            refId = 'lg_' + Math.random().toString(36).substring(2, 10);
            localStorage.setItem('lg_ref_id', refId);
            setCookie('lg_ref_id', refId, 90);
        }
        return refId;
    }

    // Capture Click IDs from URL or Local Storage
    const urlParams = new URLSearchParams(window.location.search);
    const clickParamKeys = ['gclid', 'fbclid', 'msclkid', 'li_fat_id', 'ttclid', 'twclid', 'pin_clid', 'scclid', 'gptclid', 'rdt_cid'];
    let capturedClickIds = JSON.parse(localStorage.getItem('lg_click_ids') || '{}');

    let newFound = false;
    clickParamKeys.forEach(key => {
        let val = urlParams.get(key);
        if (val) {
            capturedClickIds[key] = val;
            newFound = true;
        }
    });

    if (newFound) {
        localStorage.setItem('lg_click_ids', JSON.stringify(capturedClickIds));
        setCookie('lg_click_ids', JSON.stringify(capturedClickIds), 90);
    }

    const refId = getOrCreateRefId();

    // Pre-log session to LeadGroove server
    function sendPreSessionLog() {
        const payload = Object.assign({}, capturedClickIds, {
            client_id: clientId,
            ref_id: refId,
            landing_page_url: window.location.href,
            referrer_url: document.referrer || ''
        });

        try {
            fetch(appUrl + '/webhooks/chat-session?client_id=' + clientId, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).catch(err => console.warn('[LeadGroove] Chat pre-session log warning:', err));
        } catch (e) {
            console.warn('[LeadGroove] Fetch failed:', e);
        }
    }

    sendPreSessionLog();

    // Decorate Messaging App Links on Page (WhatsApp, Telegram, Viber)
    function decorateMessagingLinks() {
        const links = document.querySelectorAll('a[href*="wa.me"], a[href*="api.whatsapp.com"], a[href*="t.me"], a[href*="viber://"]');
        links.forEach(a => {
            let href = a.getAttribute('href');
            if (href && !href.includes(refId)) {
                if (href.includes('wa.me') || href.includes('whatsapp.com')) {
                    if (href.includes('text=')) {
                        a.setAttribute('href', href + encodeURIComponent(' (Ref: ' + refId + ')'));
                    } else {
                        let sep = href.includes('?') ? '&' : '?';
                        a.setAttribute('href', href + sep + 'text=' + encodeURIComponent('Hi! (Ref: ' + refId + ')'));
                    }
                } else if (href.includes('t.me')) {
                    let sep = href.includes('?') ? '&' : '?';
                    a.setAttribute('href', href + sep + 'text=' + encodeURIComponent('(Ref: ' + refId + ')'));
                }
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', decorateMessagingLinks);
    } else {
        decorateMessagingLinks();
    }

    // Inject Custom Variables into Top 10 Live Chat Widgets
    function injectLiveChatWidgets() {
        const varsArray = [];
        Object.keys(capturedClickIds).forEach(k => {
            if (capturedClickIds[k]) {
                varsArray.push({ name: k, value: capturedClickIds[k] });
            }
        });
        varsArray.push({ name: 'lg_ref_id', value: refId });

        // 1. LiveChat (livechat.com)
        if (window.LiveChatAPI && typeof window.LiveChatAPI.set_custom_variables === 'function') {
            window.LiveChatAPI.set_custom_variables(varsArray);
        }

        // 2. Crisp Chat
        if (window.$crisp) {
            window.$crisp.push(["set", "session:data", [varsArray.map(v => [v.name, v.value])]]);
        }

        // 3. Intercom
        if (typeof window.Intercom === 'function') {
            let intercomProps = { lg_ref_id: refId };
            Object.assign(intercomProps, capturedClickIds);
            window.Intercom('update', intercomProps);
        }

        // 4. Drift
        if (window.drift && typeof window.drift.on === 'function') {
            window.drift.on('ready', function(api) {
                api.setUserAttributes(Object.assign({ lg_ref_id: refId }, capturedClickIds));
            });
        }

        // 5. Tidio
        if (window.tidioChatApi) {
            let tidioProps = Object.assign({ lg_ref_id: refId }, capturedClickIds);
            window.tidioChatApi.setCustomFields(tidioProps);
        }

        // 6. Olark
        if (typeof window.olark === 'function') {
            let olarkProps = Object.assign({ lg_ref_id: refId }, capturedClickIds);
            window.olark('api.visitor.updateCustomFields', olarkProps);
        }

        // 7. Zendesk Web Widget
        if (typeof window.zE === 'function') {
            window.zE('webWidget', 'updateSettings', {
                webWidget: {
                    chat: {
                        departments: { select: 'Support' },
                        title: { '*': 'Chat with Us' }
                    }
                }
            });
        }

        // 8. Zoho SalesIQ
        if (window.$zoho && window.$zoho.salesiq && window.$zoho.salesiq.visitor) {
            Object.keys(capturedClickIds).forEach(k => {
                window.$zoho.salesiq.visitor.customfield(k, capturedClickIds[k]);
            });
        }

        // 9. HubSpot Live Chat
        if (window._hsq) {
            window._hsq.push(["identify", Object.assign({ lg_ref_id: refId }, capturedClickIds)]);
        }

        // 10. Help Scout Beacon
        if (typeof window.Beacon === 'function') {
            window.Beacon('session-data', Object.assign({ lg_ref_id: refId }, capturedClickIds));
        }
    }

    // Periodically attempt widget injection (since widgets load asynchronously)
    let injectCount = 0;
    const injectInterval = setInterval(() => {
        injectLiveChatWidgets();
        injectCount++;
        if (injectCount > 10) clearInterval(injectInterval);
    }, 1500);

})();
