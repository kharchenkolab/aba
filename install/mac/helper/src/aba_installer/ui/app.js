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
    let status;
    try {
      status = await fetchJSON('/api/status');
    } catch (e) {
      app.innerHTML = `<h1>ABA helper is offline.</h1>
        <p class="muted">Couldn't reach <code>/api/status</code>.
        Try restarting via <code>aba up</code>.</p>`;
      return;
    }
    // Race the heavy, credential-free env download against the user entering
    // credentials: kick it off the moment the UI loads (idempotent server-side).
    if (!status.installed) {
      fetch('/api/install/prewarm', { method: 'POST' }).catch(() => {});
    }
    const authState = await fetchJSON('/api/auth/status');
    if (!authState.credentials) return mountWelcome();
    if (!status.installed) return mountInstall(status);
    return mountControl(status);
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

    setMode('apikey');
  }

  // ─── Install page ─────────────────────────────────────────────────────
  function lastOutputLine(events) {
    for (let i = (events || []).length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.event === 'command_output' && e.payload && e.payload.line) return e.payload.line;
    }
    return '';
  }

  function mountInstall(status) {
    renderPage('install');
    document.getElementById('aba-home').textContent = status.aba_home;
    const go = document.getElementById('install-go');
    const progress = document.getElementById('install-progress');
    const log = document.getElementById('install-log');
    const note = document.getElementById('prewarm-note');

    // Reflect the background env download (started in boot) so the user sees
    // it's already underway — and that Install will be quick once it's done.
    let prewarmTimer = setInterval(async () => {
      let s;
      try { s = await fetchJSON('/api/install/prewarm'); } catch (_) { return; }
      note.hidden = false;
      if (s.status === 'running') {
        const last = lastOutputLine(s.events);
        note.textContent = '⏳ Downloading scientific packages in the background…'
          + (last ? '  ' + last.slice(0, 80) : '');
      } else if (s.status === 'done') {
        note.textContent = '✓ Scientific packages ready — Install will be quick.';
        clearInterval(prewarmTimer);
      } else if (s.status === 'error') {
        note.textContent = 'Background prep hit a snag — Install will retry it.';
        clearInterval(prewarmTimer);
      } else {
        note.hidden = true;
      }
    }, 1500);

    go.addEventListener('click', () => {
      go.disabled = true;
      clearInterval(prewarmTimer);
      note.hidden = true;
      progress.hidden = false;
      streamPlaybook('/api/install', {
        bar: document.getElementById('install-bar'),
        current: document.getElementById('install-current'),
        stepsEl: document.getElementById('install-steps'),
        logEl: log,
        onComplete: () => boot(),
      });
    });
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
        bar: document.getElementById('update-bar'),
        current: document.getElementById('update-current'),
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
  function streamPlaybook(url, { bar, current, stepsEl, logEl, onComplete }) {
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
      let stepsCount = 0;
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
            // Live line from the running command — shows long steps (the conda
            // env build) are alive, not hung.
            if (payload.line) current.textContent = payload.line.slice(0, 100);
          } else if (ev === 'step_start') {
            stepsCount++;
            const li = document.createElement('li');
            li.className = 'active';
            li.textContent = payload.title || payload.step_id;
            stepsEl.appendChild(li);
            stepsSeen.set(payload.step_id, li);
            current.textContent = payload.title || payload.step_id;
          } else if (ev === 'step_end') {
            const li = stepsSeen.get(payload.step_id);
            if (li) li.className = payload.ok ? 'ok' : 'fail';
          } else if (ev === 'complete') {
            bar.value = bar.max;
            current.textContent = payload.ok ? 'Done.' : ('Failed: ' + (payload.error || ''));
            if (payload.ok && onComplete) onComplete();
          } else if (ev === 'error') {
            current.textContent = 'Error: ' + (payload.error || '');
          }
          // Coarse progress: percentage of steps finished so far
          const done = Array.from(stepsEl.children).filter(c => c.className === 'ok' || c.className === 'fail').length;
          if (stepsCount) bar.value = Math.round((done / stepsCount) * 100);
        }
      }
    });
  }

  boot();
})();
