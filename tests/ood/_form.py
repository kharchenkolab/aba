import _pwenv as E
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(args=E.LAUNCH_ARGS)
    ctx = b.new_context(ignore_https_errors=True, http_credentials=E.AUTH)
    pg = ctx.new_page()
    pg.goto(E.BASE + "/pun/sys/dashboard/batch_connect/sys/RStudio/session_contexts/new",
            wait_until="networkidle", timeout=60000)
    print("URL:", pg.url)
    fields = pg.eval_on_selector_all("form input, form select, form textarea, form button",
      """els => els.map(e => ({tag:e.tagName, type:e.type||'', name:e.name||'', id:e.id||'',
                               value:(e.value||'').slice(0,30), text:(e.textContent||'').trim().slice(0,30),
                               required:e.required||false}))""")
    for f in fields: print(" ", f)
    b.close()
