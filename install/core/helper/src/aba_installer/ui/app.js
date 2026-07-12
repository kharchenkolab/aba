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
    // Credential setup is DEFERRED to the running app (Settings → Agent; the
    // FirstRunGate prompts on first load). The installer no longer gates on auth —
    // it installs, starts the backend CREDENTIAL-LESS, and opens the app, which works
    // for any provider (Anthropic/OpenAI). See misc/lazy_env_init.md. (mountWelcome +
    // the page-welcome template are now dead code — delete in a cleanup pass.)
    if (!status.installed) return mountSetup();               // install still finishing
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

  // Build (or rebuild) the fixed checklist from the server's planned step list.
  // Idempotent — safe to call every poll. Returns step_id → <li> for later updates.
  function populateChecklist(stepsEl, plannedSteps) {
    const liById = new Map();
    if (!stepsEl) return liById;
    stepsEl.innerHTML = '';
    for (const s of (plannedSteps || [])) {
      const li = document.createElement('li');
      li.className = 'pending';
      li.textContent = s.title || s.id;
      li.dataset.stepId = s.id;
      stepsEl.appendChild(li);
      liById.set(s.id, li);
    }
    return liById;
  }

  function renderEvents(events, { current, line, stepsEl, logEl }, totalSteps, plannedSteps, stepStatus) {
    // Prefer the planned list passed in; fall back to scraping step_planned out
    // of the event stream (for SSE callers that don't receive a separate list).
    let planned = plannedSteps;
    if (!planned || !planned.length) {
      for (const e of (events || [])) {
        if (e.event === 'step_planned' && e.payload && e.payload.steps) {
          planned = e.payload.steps; break;
        }
      }
    }
    const liById = populateChecklist(stepsEl, planned);

    // Authoritative step state comes from the server's step_status map (which
    // survives event-buffer eviction). Fall back to scanning events for SSE
    // callers that haven't been wired to expose it.
    const status = Object.assign({}, stepStatus || {});
    if (!stepStatus) {
      for (const e of (events || [])) {
        const p = e.payload || {};
        if (e.event === 'step_start' && p.step_id) status[p.step_id] = 'active';
        else if (e.event === 'step_end' && p.step_id) status[p.step_id] = p.ok ? 'ok' : 'fail';
      }
    }
    // Apply status to checklist <li>s. Anything without a status stays pending.
    let doneN = 0, activeTitle = '', activeStepId = null;
    for (const s of (planned || [])) {
      const st = status[s.id];
      const li = liById.get(s.id);
      if (st && li) li.className = st;
      if (st === 'ok' || st === 'fail') doneN++;
      if (st === 'active') { activeStepId = s.id; activeTitle = s.title || s.id; }
    }

    // Live command line — pull the most recent command_output / repair message.
    let lastLine = '';
    for (const e of (events || [])) {
      const p = e.payload || {};
      if (e.event === 'command_output' && p.line) lastLine = p.line;
      else if (e.event === 'repair' && p.message) lastLine = '🔧 ' + p.message;
    }

    const total = totalSteps || (planned || []).length || 1;
    const running = !!activeStepId;
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
        // Pre-agent regime: no agent yet, so the strip IS the help — show the
        // failed step's remediation right here, then note that signing in lets
        // ABA attempt an automatic fix (works when the cause is agent-fixable).
        const { failed } = extractFailure(s);
        strip.textContent = '';
        const t = document.createElement('strong');
        t.textContent = (failed ? failed.title : 'Setup') + ' hit a snag.';
        strip.appendChild(t);
        const rem = failed && (failed.remediation || failed.error);
        if (rem) {
          const r = document.createElement('span');
          r.style.display = 'block'; r.style.whiteSpace = 'pre-line'; r.style.marginTop = '4px';
          r.textContent = failed.remediation || failed.error.slice(0, 400);
          strip.appendChild(r);
        }
        const hint = document.createElement('span');
        hint.style.display = 'block'; hint.style.marginTop = '4px';
        hint.textContent = 'Sign in and ABA can try to fix this automatically.';
        strip.appendChild(hint);
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

  // On a terminal install failure, surface the failed step's remediation (and
  // the repair agent's diagnosis, if it ran) as a readable message + an explicit
  // retry — instead of a generic "Setup failed" that loops forever. In the
  // pre-agent regime this message is the ONLY help the user gets, so it must be
  // legible, not buried in the raw event log.
  // Pull the failed step (title + remediation + error) and the agent's closing
  // diagnosis out of the auto-install event stream. Shared by the Setup-page
  // failure panel and the pre-sign-in Welcome strip.
  function extractFailure(auto) {
    const titleById = {};
    ((auto && auto.steps) || []).forEach(s => { titleById[s.id] = s.title || s.id; });
    let failed = null, agentMsg = '';
    for (const e of ((auto && auto.events) || [])) {
      const p = e.payload || {};
      if (e.event === 'step_end' && p.ok === false)
        failed = { id: p.step_id, title: titleById[p.step_id] || p.step_id,
                   error: p.error || '', remediation: p.remediation || '' };
      if (e.event === 'repair' && p.message && (p.phase === 'done' || p.phase === 'skip'))
        agentMsg = p.message;
    }
    return { failed, agentMsg };
  }

  function renderSetupFailure(errEl, auto) {
    const { failed, agentMsg } = extractFailure(auto);
    errEl.textContent = '';
    const head = document.createElement('strong');
    head.textContent = (failed ? failed.title : 'Setup') + ' failed.';
    errEl.appendChild(head);
    const detail = failed && (failed.remediation || failed.error);
    if (detail) {
      const d = document.createElement('p');
      d.style.whiteSpace = 'pre-line'; d.style.marginTop = '6px';
      d.textContent = failed.remediation || failed.error.slice(0, 500);
      errEl.appendChild(d);
    }
    // Show the agent's own conclusion when it ran but couldn't fix it (skip the
    // "not signed in" gate message — that's noise, not guidance).
    if (agentMsg && !/sign.?in|signed in/i.test(agentMsg)) {
      const a = document.createElement('p');
      a.className = 'muted'; a.style.whiteSpace = 'pre-line'; a.style.marginTop = '6px';
      a.textContent = 'Assistant: ' + agentMsg.slice(0, 500);
      errEl.appendChild(a);
    }
    const btn = document.createElement('button');
    btn.className = 'btn'; btn.textContent = 'Try again'; btn.style.marginTop = '8px';
    btn.addEventListener('click', () => {
      errEl.textContent = '';
      fetch('/api/install/auto', { method: 'POST' }).catch(() => {});
      mountSetup();
    });
    errEl.appendChild(btn);
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
      const st = await pollAuto(s => renderEvents(s.events, els, s.total_steps, s.steps, s.step_status));
      if (st === 'done') {
        clearInterval(timer);
        els.current.textContent = 'Starting ABA…';
        boot();  // installed → boot auto-starts the backend → Control
      } else if (st === 'error') {
        clearInterval(timer);
        // Surface the failed step's remediation + agent diagnosis with an
        // explicit retry, instead of a generic message on an infinite loop.
        const auto = await fetchJSON('/api/install/auto').catch(() => null);
        renderSetupFailure(errEl, auto);
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
    const runUpdate = () => {
      const upd = document.getElementById('ctl-update');
      if (upd && upd.disabled) return;            // already running — don't double-fire (409)
      if (upd) { upd.disabled = true; upd.textContent = '↻ Updating…'; }
      const wrap = document.getElementById('update-progress');
      wrap.hidden = false;
      streamPlaybook('/api/update', {
        current: document.getElementById('update-current'),
        line: document.getElementById('update-line'),
        stepsEl: document.getElementById('update-steps'),
        onComplete: () => boot(),
        // Re-enable on any terminal outcome (ok, failure, or dropped stream) by
        // reconciling against the server's operation state.
        onSettled: () => { fetchJSON('/api/status').then(refreshControl).catch(() => {}); },
      });
    };
    document.getElementById('ctl-update').addEventListener('click', runUpdate);
    // The tray's "Check for updates" opens us with ?update=1 to auto-start.
    // One-shot: strip the param so a refresh won't re-trigger; refreshControl
    // (called by mountControl above) has already disabled the button if an
    // update is mid-flight, and runUpdate bails in that case.
    if (new URLSearchParams(location.search).get('update') === '1') {
      history.replaceState(null, '', location.pathname);
      runUpdate();
    }

    // Model selection lives in the running app now — Settings → Agent (multi-provider).
    // Removed from the control page (the tray's Model submenu still uses /api/auth/model).
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
    // Adaptive cadence: while the backend is warming up (running but not yet
    // serving) poll fast so 'Starting…' flips to 'Running' promptly; once
    // steady, back off to a gentle idle poll.
    if (window._abaPollTimer) clearTimeout(window._abaPollTimer);
    const tick = async () => {
      let warming = false;
      try {
        const s = await fetchJSON('/api/status');
        refreshControl(s);
        warming = !!s.backend_running && !s.backend_ready;
        const d = document.getElementById('diag');
        if (d && d.open) loadDiag();
      } catch(e){}
      window._abaPollTimer = setTimeout(tick, warming ? 1000 : 5000);
    };
    window._abaPollTimer = setTimeout(tick, 1000);
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
    const ready = !!s.backend_ready;          // process up AND serving HTTP
    if (running && ready) {
      status.textContent = '●  Running';
      status.style.color = 'var(--green)';
      meta.textContent = `pid ${s.backend_pid} · ${s.aba_home}`;
    } else if (running) {
      // Process exists but not serving yet — cold-start warmup. Don't let the
      // user click Open onto a dead port; show that it's coming up.
      status.textContent = '◌  Starting…';
      status.style.color = 'var(--amber, #d08700)';
      meta.textContent = `pid ${s.backend_pid} · warming up · ${s.aba_home}`;
    } else {
      status.textContent = '○  Stopped';
      status.style.color = 'var(--muted)';
      meta.textContent = s.aba_home;
    }
    // Start only when stopped; Stop/Restart only when running.
    if (start) start.disabled = running;
    if (stop) stop.disabled = !running;
    if (restart) restart.disabled = !running;
    // "Open ABA" is a link, not a <button> — enable only once actually serving.
    const open = document.getElementById('open-aba');
    if (open) open.classList.toggle('disabled', !ready);
    // Update button reflects the server's long-op state: disabled + relabelled
    // while an install/update runs (incl. one started by the tray or another
    // tab), re-enabled when it finishes. This is the single source of truth, so
    // the 5s/1s status poll re-enables the button after the op completes.
    const upd = document.getElementById('ctl-update');
    if (upd) {
      upd.disabled = !!s.operation;
      upd.textContent = s.operation === 'update' ? '↻ Updating…'
                      : s.operation === 'install' ? '↻ Installing…'
                      : '⤓ Check updates';
    }
  }

  // ─── playbook event stream (used by both Install + Update) ─────────────
  function streamPlaybook(url, { current, line, stepsEl, logEl, onComplete, onSettled }) {
    // EventSource doesn't support POST; use fetch + ReadableStream
    fetch(url, { method: 'POST' }).then(async (r) => {
      if (!r.ok) {
        current.textContent = `Failed to start: HTTP ${r.status}`;
        return;
      }
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      // step_id → <li>, seeded by step_planned and extended on step_start (for
      // back-compat with servers that don't emit step_planned).
      let stepsSeen = new Map();
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

          if (ev === 'step_planned') {
            // Pre-render the full planned checklist so the user can see what's
            // coming, not just what's already happened.
            stepsSeen = populateChecklist(stepsEl, payload.steps);
          } else if (ev === 'command_output') {
            // Live line from the running command — beneath the phase title, so
            // long steps (the conda/pip build) read as alive, not hung.
            if (payload.line && line) line.textContent = payload.line.slice(0, 140);
          } else if (ev === 'repair') {
            if (payload.message && line) line.textContent = '🔧 ' + payload.message.slice(0, 140);
          } else if (ev === 'step_start') {
            // Honor the pre-populated <li> if step_planned arrived; otherwise
            // append a fresh one (old-server fallback).
            let li = stepsSeen.get(payload.step_id);
            if (!li) {
              li = document.createElement('li');
              li.textContent = payload.title || payload.step_id;
              stepsEl.appendChild(li);
              stepsSeen.set(payload.step_id, li);
            }
            li.className = 'active';
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
    }).catch(() => {
      if (current) current.textContent = 'Update connection lost.';
    }).finally(() => { if (onSettled) onSettled(); });
  }

  boot();
})();
