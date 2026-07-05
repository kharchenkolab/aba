"""The external-viewer launch page (served at GET /viewer-launch).

A small ABA-owned tab opened on the click gesture: it starts a prepare job,
polls it, shows friendly progress, then redirects itself to the viewer when the
store is ready. On failure it shows the error with Retry + a Report action that
routes into ABA's bug-report flow (postMessage to the opener). It reuses ABA's
built stylesheet (design tokens/typography) — only a few layout rules are local.
"""
from __future__ import annotations

import re
from pathlib import Path


def _css_links(frontend_dist: Path) -> str:
    """Reuse the SPA's stylesheet(s) so this page shares ABA's tokens. Hrefs are
    made page-relative (strip leading '/') so they resolve under an OOD prefix."""
    idx = frontend_dist / "index.html"
    if not idx.is_file():
        return ""
    hrefs = re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', idx.read_text())
    hrefs += re.findall(r'<link[^>]+href="([^"]+\.css)"[^>]*rel="stylesheet"', idx.read_text())
    seen, out = set(), []
    for h in hrefs:
        h = h.lstrip("/")
        if h and h not in seen:
            seen.add(h); out.append(f'<link rel="stylesheet" href="{h}">')
    return "\n  ".join(out)


def render(frontend_dist: Path) -> str:
    return _HTML.replace("<!--CSS-->", _css_links(frontend_dist))


_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Opening viewer…</title>
  <!--CSS-->
  <style>
    /* Layout only — colours/typography come from ABA's reused stylesheet tokens. */
    html, body { height: 100%; margin: 0; }
    body { background: var(--page, #fafaf9); color: var(--text, #1c1917);
           font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
           display: flex; align-items: center; justify-content: center; }
    .vl-card { background: var(--surface, #fff); border: 1px solid var(--panel-bd, #e7e5e4);
               border-radius: 14px; box-shadow: 0 6px 24px rgba(0,0,0,.06);
               width: min(440px, calc(100vw - 32px)); padding: 28px 30px; }
    .vl-row { display: flex; align-items: center; gap: 14px; }
    .vl-title { font-size: 16px; font-weight: 600; }
    .vl-sub { color: var(--text-3, #6b645f); font-size: 13.5px; margin-top: 3px; }
    .vl-spin { width: 26px; height: 26px; flex: none; border-radius: 50%;
               border: 3px solid var(--accent-soft, #eef2ff);
               border-top-color: var(--accent, #4f46e5); animation: vl-rot .8s linear infinite; }
    @keyframes vl-rot { to { transform: rotate(360deg); } }
    .vl-meta { color: var(--text-4, #6f6862); font-size: 12px; margin-top: 16px; }
    .vl-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }
    .vl-btn { font: inherit; font-size: 13.5px; font-weight: 550; cursor: pointer;
              border-radius: 9px; padding: 9px 15px; border: 1px solid var(--panel-bd, #e7e5e4);
              background: var(--surface-2, #f5f5f4); color: var(--text, #1c1917); }
    .vl-btn:hover { background: var(--surface-3, #e7e5e4); }
    .vl-btn--primary { background: var(--accent, #4f46e5); border-color: var(--accent, #4f46e5); color: #fff; }
    .vl-btn--primary:hover { background: var(--accent-2, #6366f1); }
    .vl-err-badge { width: 26px; height: 26px; flex: none; border-radius: 50%;
                    background: var(--skeptic, #e0506a); color: #fff; display: flex;
                    align-items: center; justify-content: center; font-weight: 700; }
    .vl-err-detail { margin-top: 14px; padding: 10px 12px; border-radius: 9px;
                     background: var(--surface-2, #f5f5f4); border: 1px solid var(--panel-bd, #e7e5e4);
                     color: var(--text-2, #57534e); font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                     font-size: 12px; white-space: pre-wrap; word-break: break-word; max-height: 160px; overflow: auto; }
    .vl-hidden { display: none; }
  </style>
</head>
<body>
  <main class="vl-card" role="status" aria-live="polite">
    <!-- preparing -->
    <div id="vl-loading">
      <div class="vl-row"><div class="vl-spin"></div>
        <div><div class="vl-title" id="vl-title">Opening viewer…</div>
             <div class="vl-sub" id="vl-phase">Starting…</div></div></div>
      <div class="vl-meta" id="vl-meta"></div>
    </div>
    <!-- error -->
    <div id="vl-error" class="vl-hidden">
      <div class="vl-row"><div class="vl-err-badge">!</div>
        <div><div class="vl-title">Couldn't open the viewer</div>
             <div class="vl-sub" id="vl-errsub">Preparing the dataset failed.</div></div></div>
      <div class="vl-err-detail" id="vl-errdetail"></div>
      <div class="vl-actions">
        <button class="vl-btn vl-btn--primary" id="vl-retry">Try again</button>
        <button class="vl-btn" id="vl-report">Report to the ABA team</button>
      </div>
    </div>
  </main>
  <script>
  (function () {
    var BASE = location.pathname.replace(/\/viewer-launch\/?$/, "");
    var q = new URLSearchParams(location.search);
    var params = { viewer_id: q.get("viewer") || undefined, path: q.get("path") || undefined,
                   entity_id: q.get("entity") || undefined };
    var project = q.get("project") || "";
    var label = q.get("label") || "";
    var action = q.get("action") || "view";        // 'view' | 'download'
    var isDownload = action === "download";
    var verb = isDownload ? "Preparing" : "Opening";
    var t0 = Date.now(), pollTimer = null;
    var $ = function (id) { return document.getElementById(id); };
    if (label) { $("vl-title").textContent = verb + " " + label + "…"; }
    if (isDownload) { document.title = "Preparing download…"; }

    function withBase(u) { return (BASE && u && u.charAt(0) === "/") ? BASE + u : u; }
    function api(p) { return withBase("/api" + p); }

    function finalUrl(url) {                       // base-prefix the app path AND the nested ?store=
      var i = url.indexOf("?store=");
      if (i < 0) return withBase(url);
      var app = url.slice(0, i), store = decodeURIComponent(url.slice(i + 7));
      return withBase(app) + "?store=" + encodeURIComponent(withBase(store));
    }

    function showError(msg) {
      if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
      $("vl-loading").classList.add("vl-hidden");
      $("vl-errdetail").textContent = msg || "Unknown error.";
      $("vl-error").classList.remove("vl-hidden");
    }

    function tick() {
      var el = ((Date.now() - t0) / 1000);
      $("vl-meta").textContent = (label ? label + " · " : "") + el.toFixed(0) + "s";
    }

    function poll(jobId) {
      fetch(api("/viewers/launch/status?job=" + encodeURIComponent(jobId)))
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("status " + r.status)); })
        .then(function (s) {
          if (s.label && !label) { label = s.label; $("vl-title").textContent = verb + " " + label + "…"; }
          if (s.phase) { $("vl-phase").textContent = s.phase; }
          tick();
          if (s.status === "ready" && s.url) {
            if (isDownload) {                       // stream the prepared store back as .lstar.zarr.zip
              var dq = new URLSearchParams({ viewer_id: params.viewer_id, project_id: project });
              if (params.path) dq.set("path", params.path);
              else if (params.entity_id) dq.set("entity_id", params.entity_id);
              $("vl-phase").textContent = "Ready — downloading…";
              location.href = api("/viewers/download?" + dq.toString());
              $("vl-title").textContent = "Download started";
              $("vl-phase").textContent = "Your .lstar.zarr.zip is downloading — you can close this tab.";
              return;
            }
            if (s.set_local_storage) {
              try { Object.keys(s.set_local_storage).forEach(function (k) {
                var v = s.set_local_storage[k]; localStorage.setItem(k, (v && v.charAt(0) === "/") ? withBase(v) : v);
              }); } catch (e) {}
            }
            $("vl-title").textContent = "Opening pagoda3…";
            $("vl-phase").textContent = "Store ready — loading the embedding in pagoda3…";
            location.replace(finalUrl(s.url));
            return;
          }
          if (s.status === "error") { showError(s.error); return; }
          pollTimer = setTimeout(function () { poll(jobId); }, 700);
        })
        .catch(function (e) { showError(String(e && e.message || e)); });
    }

    function launch() {
      $("vl-error").classList.add("vl-hidden");
      $("vl-loading").classList.remove("vl-hidden");
      $("vl-phase").textContent = "Starting…"; t0 = Date.now(); tick();
      var body = { viewer_id: params.viewer_id };
      if (params.path) body.path = params.path; else if (params.entity_id) body.entity_id = params.entity_id;
      fetch(api("/viewers/launch"), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Project-Id": project },
        body: JSON.stringify(body),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok) { showError(res.d && res.d.detail || "Launch failed."); return; }
          if (res.d.label) { label = res.d.label; $("vl-title").textContent = verb + " " + label + "…"; }
          poll(res.d.job_id);
        })
        .catch(function (e) { showError(String(e && e.message || e)); });
    }

    $("vl-retry").addEventListener("click", launch);
    $("vl-report").addEventListener("click", function () {
      var ctx = { where: "viewer-launch", viewer: label || params.viewer_id,
                  file: params.path || params.entity_id, project: project,
                  error: $("vl-errdetail").textContent };
      try {
        if (window.opener && !window.opener.closed) {
          window.opener.postMessage({ type: "aba:viewer-error", context: ctx }, location.origin);
          window.opener.focus();
          $("vl-report").textContent = "Opened in the ABA tab ↗";
          return;
        }
      } catch (e) {}
      // No opener (tab reloaded / opened directly) → fall back to the ABA app.
      location.href = withBase("/") + "?report=" + encodeURIComponent(JSON.stringify(ctx));
    });

    if (!params.viewer_id) { showError("Missing viewer parameter."); } else { launch(); }
  })();
  </script>
</body>
</html>
"""
