/* ABA installer UI — vanilla JS, no framework.
 * Loads /api/status, routes to one of three pages, wires up the buttons.
 */
(() => {
  const app = document.getElementById('app');

  // ─── tiny page router ────────────────────────────────────────────────
  function renderPage(name) {
    const tpl = document.getElementById('page-' + name);
    if (!tpl) throw new Error('unknown page ' + name);
    app.innerHTML = '';
    app.appendChild(tpl.content.cloneNode(true));
  }

  async function fetchJSON(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      const t = await r.text();
      throw new Error(`${r.status} ${url}: ${t}`);
    }
    return r.json();
  }

  async function boot() {
    // Stop any per-page pollers before re-rendering.
    for (const k of ['_abaStripTimer', '_abaSetupTimer']) {
      if (window[k]) { clearInterval(window[k]); window[k] = null; }
    }
    let status;
    try {
      status = await fetchJSON('/api/status');
    } catch (e) {
      app.innerHTML = `<h1>ABA helper is offline.</h1>
        <p class="muted">Couldn't reach <code>/api/status</code>.
        Try restarting via <code>aba up</code>.</p>`;
      return;
    }
    // The install needs no credentials, so run it automatically in the
    // background the moment the UI loads — in parallel with the user signing
    // in. Idempotent server-side.
    if (!status.installed) {
      fetch('/api/install/auto', { method: 'POST' }).catch(() => {});
    }
    const authState = await fetchJSON('/api/auth/status');
    if (!authState.credentials) return mountWelcome();        // sign in (install runs behind it)
    if (!status.installed) return mountSetup();               // authed; install still finishing
    if (!status.backend_running) {                            // installed → start the backend once
      if (!window._abaAutoStarted) {
        window._abaAutoStarted = true;
        try { await fetchJSON('/api/start', { method: 'POST' }); } catch (e) {}
        return boot();
      }
    }
    return mountControl(status);
  }

  // ─── background-install progress (polled, shown on Welcome + Setup) ────
  async function pollAuto(onUpdate) {
    let s;
    try { s = await fetchJSON('/api/install/auto'); } catch (_) { return 'pending'; }
    onUpdate(s);
    return s.status;
  }

  function renderEvents(events, { current, line, stepsEl, logEl }, totalSteps) {
    if (stepsEl) stepsEl.innerHTML = '';
    const seen = new Map();
    let lastLine = '', started = 0, doneN = 0, activeTitle = '';
    for (const e of (events || [])) {
      const p = e.payload || {};
      if (e.event === 'step_start') {
        started++;
        activeTitle = p.title || p.step_id;
        if (stepsEl) {
          const li = document.createElement('li'); li.className = 'active';
          li.textContent = activeTitle; stepsEl.appendChild(li);
          seen.set(p.step_id, li);
        }
      } else if (e.event === 'step_end') {
        doneN++;
        const li = seen.get(p.step_id); if (li) li.className = p.ok ? 'ok' : 'fail';
      } else if (e.event === 'command_output' && p.line) {
        lastLine = p.line;
      } else if (e.event === 'repair' && p.message) {
        lastLine = '🔧 ' + p.message;   // Tier-0 agent repairing a failed step
      }
    }
    // No bar — env builds give no honest percentage. Show the current PHASE
    // title (with its ordinal) and the live command line below it.
    const total = totalSteps || started || 1;
    const running = started > doneN;
    if (current) current.textContent = running
      ? `Step ${Math.min(doneN + 1, total)} of ${total}: ${activeTitle}`
      : (doneN >= total ? 'Finishing…' : '');
    if (line) line.textContent = lastLine ? lastLine.slice(0, 140) : '';
    if (logEl) {
      logEl.textContent = (events || []).map(e =>
        e.event === 'command_output' ? (e.payload.line || '')
          : `[${e.event}] ${JSON.stringify(e.payload)}`).join('\n');
      logEl.scrollTop = logEl.scrollHeight;
    }
  }

  // ─── Welcome page ────────────────────────────────────────────────────
  function mountWelcome() {
    renderPage('welcome');
    const submit = document.getElementById('auth-submit');
    const errEl = document.getElementById('auth-error');
    const apikeyInput = document.getElementById('apikey-input');
    const oauthInput = document.getElementById('oauth-input');
    const tabApi = document.getElementById('tab-apikey');
    const tabOauth = document.getElementById('tab-oauth');
    const panelApi = document.getElementById('panel-apikey');
    const panelOauth = document.getElementById('panel-oauth');
    let mode = 'apikey';

    function setMode(m) {
      mode = m;
      tabApi.classList.toggle('active', m === 'apikey');
      tabOauth.classList.toggle('active', m === 'oauth');
      panelApi.hidden = m !== 'apikey';
      panelOauth.hidden = m !== 'oauth';
      // The bottom Continue button is the API-key action; OAuth uses its own
      // "Sign in" button (or the paste fallback's button).
      submit.hidden = m !== 'apikey';
      errEl.textContent = '';
      if (m === 'apikey') apikeyInput.focus();
    }
    tabApi.addEventListener('click', () => setMode('apikey'));
    tabOauth.addEventListener('click', () => setMode('oauth'));

    // Sign in with Claude.ai — browser OAuth. Opens claude.ai, then polls
    // until the helper's /callback completes the handshake.
    const signin = document.getElementById('oauth-signin');
    const oauthStatus = document.getElementById('oauth-status');
    let polling = null;
    signin.addEventListener('click', async () => {
      errEl.textContent = '';
      signin.disabled = true;
      oauthStatus.hidden = false;
      oauthStatus.textContent = 'Opening claude.ai… complete sign-in there, then come back.';
      try {
        const { authorize_url } = await fetchJSON('/api/auth/oauth/start', { method: 'POST' });
        window.open(authorize_url, '_blank');
        if (polling) clearInterval(polling);
        polling = setInterval(async () => {
          let s;
          try { s = await fetchJSON('/api/auth/oauth/poll'); } catch (_) { return; }
          if (s.status === 'done') {
            clearInterval(polling);
            oauthStatus.textContent = 'Signed in ✓';
            boot();
          } else if (s.status === 'error') {
            clearInterval(polling);
            signin.disabled = false;
            oauthStatus.hidden = true;
            errEl.textContent = s.error || 'Sign-in failed. Try again, or paste a token.';
          }
        }, 1500);
      } catch (e) {
        signin.disabled = false;
        oauthStatus.hidden = true;
        errEl.textContent = (e.message || '').replace(/^\d+\s\S+:\s*/, '');
      }
    });

    async function submitCreds(url, payload, btn) {
      errEl.textContent = '';
      btn.disabled = true;
      try {
        await fetchJSON(url, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        boot();
      } catch (e) {
        errEl.textContent = (e.message || '').replace(/^\d+\s\S+:\s*/, '');
        btn.disabled = false;
      }
    }

    submit.addEventListener('click', () => {
      const key = apikeyInput.value.trim();
      if (!key) { errEl.textContent = 'Paste a key first.'; return; }
      submitCreds('/api/auth/apikey', { key }, submit);
    });
    apikeyInput.addEventListener('keydown', e => { if (e.key === 'Enter') submit.click(); });

    const pasteBtn = document.getElementById('oauth-paste-submit');
    pasteBtn.addEventListener('click', () => {
      const token = oauthInput.value.trim();
      if (!token) { errEl.textContent = 'Paste a token first.'; return; }
      submitCreds('/api/auth/oauth', { token }, pasteBtn);
    });
    oauthInput.addEventListener('keydown', e => { if (e.key === 'Enter') pasteBtn.click(); });

    // Show the background install progress right here, so signing in doesn't
    // feel like nothing's happening.
    const strip = document.getElementById('setup-strip');
    const stripTimer = setInterval(() => pollAuto(s => {
      strip.hidden = false;
      if (s.status === 'running') {
        const last = lastOutputLine(s.events);
        strip.textContent = '⏳ Setting up ABA in the background…' + (last ? '  ' + last.slice(0, 70) : '');
      } else if (s.status === 'done') {
        strip.textContent = '✓ Setup ready — just finish signing in.';
        clearInterval(stripTimer);
      } else if (s.status === 'error') {
        strip.textContent = 'Setup hit a snag (details after you sign in).';
        clearInterval(stripTimer);
      } else { strip.hidden = true; }
    }), 2000);
    window._abaStripTimer = stripTimer;  // cleared when we navigate away via boot()

    setMode('apikey');
  }

  function lastOutputLine(events) {
    for (let i = (events || []).length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.event === 'command_output' && e.payload && e.payload.line) return e.payload.line;
    }
    return '';
  }

  // ─── Setup page (authed; the background install is finishing) ──────────
  function mountSetup() {
    renderPage('setup');
    fetchJSON('/api/status').then(s => {
      const el = document.getElementById('aba-home'); if (el) el.textContent = s.aba_home;
    }).catch(() => {});
    const els = {
      current: document.getElementById('setup-current'),
      line: document.getElementById('setup-line'),
      stepsEl: document.getElementById('setup-steps'),
      logEl: document.getElementById('setup-log'),
    };
    const errEl = document.getElementById('setup-error');
    // Make sure it's running (e.g. after an error → retry), then poll to done.
    fetch('/api/install/auto', { method: 'POST' }).catch(() => {});
    const timer = setInterval(async () => {
      const st = await pollAuto(s => renderEvents(s.events, els, s.total_steps));
      if (st === 'done') {
        clearInterval(timer);
        els.current.textContent = 'Starting ABA…';
        boot();  // installed → boot auto-starts the backend → Control
      } else if (st === 'error') {
        clearInterval(timer);
        errEl.textContent = 'Setup failed — see details. Retrying…';
        fetch('/api/install/auto', { method: 'POST' }).catch(() => {});
        setTimeout(mountSetup, 4000);
      }
    }, 1500);
    window._abaSetupTimer = timer;
  }

  // ─── Control page ─────────────────────────────────────────────────────
  function mountControl(status) {
    renderPage('control');
    refreshControl(status);
    document.getElementById('ctl-start').addEventListener('click', async () => {
      try { await fetchJSON('/api/start', { method: 'POST' }); } catch(e){}
      boot();
    });
    document.getElementById('ctl-stop').addEventListener('click', async () => {
      try { await fetchJSON('/api/stop', { method: 'POST' }); } catch(e){}
      boot();
    });
    document.getElementById('ctl-restart').addEventListener('click', async () => {
      try {
        await fetchJSON('/api/stop', { method: 'POST' });
        await fetchJSON('/api/start', { method: 'POST' });
      } catch(e){}
      boot();
    });
    document.getElementById('ctl-update').addEventListener('click', () => {
      const wrap = document.getElementById('update-progress');
      wrap.hidden = false;
      streamPlaybook('/api/update', {
        current: document.getElementById('update-current'),
        line: document.getElementById('update-line'),
        stepsEl: document.getElementById('update-steps'),
        onComplete: () => boot(),
      });
    });
    const diag = document.getElementById('diag');
    diag.addEventListener('toggle', () => { if (diag.open) loadDiag(); });
    document.getElementById('ctl-help').addEventListener('click', () => {
      // H8 (deferred) — for now, route the user to the GitHub issue tracker.
      window.open('https://github.com/kharchenkolab/aba/issues', '_blank');
    });
    document.getElementById('ctl-uninstall').addEventListener('click', async () => {
      if (!confirm('Uninstall ABA? Your projects (runtime dir) will be preserved.')) return;
      await fetchJSON('/api/uninstall', { method: 'POST' });
      alert('Uninstalled.');
      boot();
    });

    // Light poll for status changes so the UI reflects external `aba stop` etc.
    if (window._abaPollTimer) clearInterval(window._abaPollTimer);
    window._abaPollTimer = setInterval(async () => {
      try {
        const s = await fetchJSON('/api/status');
        refreshControl(s);
        const d = document.getElementById('diag');
        if (d && d.open) loadDiag();
      } catch(e){}
    }, 5000);
  }

  async function loadDiag() {
    try {
      const s = await fetchJSON('/api/status');
      const meta = document.getElementById('diag-meta');
      if (meta) meta.textContent = s.backend_running
        ? `Running · pid ${s.backend_pid} · http://localhost:8000 · ${s.aba_home}`
        : `Stopped · ${s.aba_home}`;
      const out = await fetchJSON('/api/logs?tail=200');
      const view = document.getElementById('logs-view');
      if (view) { view.textContent = (out.lines || []).join('\n'); view.scrollTop = view.scrollHeight; }
    } catch (e) {}
  }

  function refreshControl(s) {
    const status = document.getElementById('run-status');
    const meta = document.getElementById('run-meta');
    const start = document.getElementById('ctl-start');
    const stop = document.getElementById('ctl-stop');
    const restart = document.getElementById('ctl-restart');
    const running = !!s.backend_running;
    if (running) {
      status.textContent = '●  Running';
      status.style.color = 'var(--green)';
      meta.textContent = `pid ${s.backend_pid} · ${s.aba_home}`;
    } else {
      status.textContent = '○  Stopped';
      status.style.color = 'var(--muted)';
      meta.textContent = s.aba_home;
    }
    // Start only when stopped; Stop/Restart only when running.
    if (start) start.disabled = running;
    if (stop) stop.disabled = !running;
    if (restart) restart.disabled = !running;
    // "Open ABA" is a link, not a <button> — grey it out when stopped.
    const open = document.getElementById('open-aba');
    if (open) open.classList.toggle('disabled', !running);
  }

  // ─── playbook event stream (used by both Install + Update) ─────────────
  function streamPlaybook(url, { current, line, stepsEl, logEl, onComplete }) {
    // EventSource doesn't support POST; use fetch + ReadableStream
    fetch(url, { method: 'POST' }).then(async (r) => {
      if (!r.ok) {
        current.textContent = `Failed to start: HTTP ${r.status}`;
        return;
      }
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      const stepsSeen = new Map(); // id → <li>
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const lines = frame.split('\n');
          let ev = 'message', data = '';
          for (const ln of lines) {
            if (ln.startsWith('event: ')) ev = ln.slice(7).trim();
            else if (ln.startsWith('data: ')) data += ln.slice(6);
          }
          let payload = {};
          try { payload = JSON.parse(data); } catch(_) {}
          if (logEl) {
            logEl.textContent += (ev === 'command_output' ? (payload.line || '') : `[${ev}] ${data}`) + '\n';
            if (logEl.textContent.length > 24000) logEl.textContent = logEl.textContent.slice(-24000);
          }

          if (ev === 'command_output') {
            // Live line from the running command — beneath the phase title, so
            // long steps (the conda/pip build) read as alive, not hung.
            if (payload.line && line) line.textContent = payload.line.slice(0, 140);
          } else if (ev === 'repair') {
            if (payload.message && line) line.textContent = '🔧 ' + payload.message.slice(0, 140);
          } else if (ev === 'step_start') {
            const li = document.createElement('li');
            li.className = 'active';
            li.textContent = payload.title || payload.step_id;
            stepsEl.appendChild(li);
            stepsSeen.set(payload.step_id, li);
            current.textContent = payload.title || payload.step_id;   // phase title
            if (line) line.textContent = '';
          } else if (ev === 'step_end') {
            const li = stepsSeen.get(payload.step_id);
            if (li) li.className = payload.ok ? 'ok' : 'fail';
          } else if (ev === 'complete') {
            current.textContent = payload.ok ? 'Done.' : ('Failed: ' + (payload.error || ''));
            if (line) line.textContent = '';
            if (payload.ok && onComplete) onComplete();
          } else if (ev === 'error') {
            current.textContent = 'Error: ' + (payload.error || '');
          }
        }
      }
    });
  }

  boot();
})();
