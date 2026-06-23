#!/usr/bin/env python3
"""Patch the OOD dashboard's batch_connect form page (new.html.erb) so the ABA
app's form reflects the LAB the user selects in the dropdown:
  * folder path  -> /groups/<selected-lab>/aba/<user>   (shown in BLUE, in the footer)
  * saved-credential status -> per-lab, placed RIGHT UNDER the API-key field
        green ✓ when a user/lab key exists, red ⚠ when none.
Both update live via JS on aba_lab change, from a per-lab map embedded
server-side. Other apps keep the stock "data root directory" note.

new.html.erb lives INSIDE dev_ood (not our repo); idempotent + upgrades older
versions (regex-replaces whatever session-data <p> block is present).
Re-run after any dev_ood recreate:
  docker cp patch_dashboard.py dev_ood:/tmp/ && docker exec dev_ood python3 /tmp/patch_dashboard.py
  docker exec dev_ood touch /var/www/ood/apps/sys/dashboard/tmp/restart.txt
"""
import re, sys

P = "/var/www/ood/apps/sys/dashboard/app/views/batch_connect/session_contexts/new.html.erb"
MARK = "abaCredEl"   # unique to this version
BLOCK_RE = re.compile(r'        <p>\n.*?\n        </p>', re.DOTALL)

NEW = '''        <p>
        <% if @app.title.to_s == "ABA" %>
          <%
            _site = (YAML.safe_load(File.read(ENV['ABA_SITE_CONFIG'] || '/cluster/aba/site.yaml')) rescue {}) || {}
            _scopes = _site['scopes'] || {}
            _sd = ((_scopes['user'] || {})['state_dir']) || '/groups/{group}/aba/{user}'
            _user = ENV['USER'] || 'you'
            _filled = _sd.gsub('{user}', _user).gsub('{home}', '~')
            _parts = _filled.split('{group}', 2)
            _groot = (_sd.split('/{group}').first rescue '/groups')
            _labs = (Dir.exist?(_groot) ? Dir.children(_groot).sort : []) rescue []
            _default_lab = _labs.first || ''
            _creds = _site['credentials'] || {}
            _ukey = _creds['user_key_path'] || ''
            _gkey = _creds['group_key_path'] || ''
            _labmap = {}
            _labs.each do |g|
              u = (!_ukey.empty? && File.exist?(_ukey.gsub('{group}',g).gsub('{user}',_user).gsub('{home}','~'))) rescue false
              gr = (!_gkey.empty? && File.exist?(_gkey.gsub('{group}',g).gsub('{user}',_user).gsub('{home}','~'))) rescue false
              _labmap[g] = {'user'=>(!!u), 'group'=>(!!gr)}
            end
          %>
          <% if _parts.length == 2 %>
            Your ABA workspace — projects, runs, and chat history — will be saved in your
            lab's ABA folder at <code style="color:#1565c0"><%= _parts[0] %><span id="aba-folder-lab"><%= _default_lab %></span><%= _parts[1] %></code>,
            persisting across sessions.
            <script type="text/javascript">
              (function(){
                var MAP = <%= _labmap.to_json.html_safe %>;
                function abaCredEl(){
                  var cs = document.getElementById('aba-cred-status');
                  if(cs) return cs;
                  var tok = document.getElementById('batch_connect_session_context_aba_token');
                  if(!tok) return null;
                  cs = document.createElement('small');
                  cs.id = 'aba-cred-status';
                  cs.className = 'form-text';
                  cs.style.display = 'block';
                  cs.style.fontWeight = '600';
                  tok.parentNode.insertBefore(cs, tok.nextSibling);  // right under the field
                  return cs;
                }
                function abaUpd(){
                  var sel = document.getElementById('batch_connect_session_context_aba_lab');
                  if(!sel) return;
                  var lab = sel.value || '';
                  var p = document.getElementById('aba-folder-lab'); if(p){ p.textContent = lab; }
                  var cs = abaCredEl();
                  if(cs){
                    var c = MAP[lab] || {};
                    if(c.user){ cs.textContent = '\\u2713 A saved key for '+lab+' will be used \\u2014 you can leave this field blank.'; cs.style.color = '#2e7d32'; }
                    else if(c.group){ cs.textContent = '\\u2713 '+lab+' has a shared lab key \\u2014 you can leave this field blank.'; cs.style.color = '#2e7d32'; }
                    else { cs.textContent = '\\u26A0 No saved key for '+lab+' \\u2014 paste one here to enable chat (the UI still loads).'; cs.style.color = '#c62828'; }
                  }
                }
                var s = document.getElementById('batch_connect_session_context_aba_lab');
                if(s){ s.addEventListener('change', abaUpd); }
                abaUpd();
                document.addEventListener('turbolinks:load', abaUpd);
              })();
            </script>
          <% else %>
            Your ABA workspace will be saved at <code style="color:#1565c0"><%= _filled %></code>, persisting across sessions.
          <% end %>
        <% else %>
          <%= t('dashboard.batch_connect_form_session_data_html',
                title: @app.title,
                data_link_tag: link_to(
                  t('dashboard.batch_connect_form_data_root'),
                  OodAppkit.files.url(
                    path: BatchConnect::Session.dataroot(@app.token)
                  ).to_s,
                  target: "_blank")
                )
          %>
        <% end %>
        </p>'''


def main():
    s = open(P).read()
    if MARK in s:
        print("already patched")
        return
    m = BLOCK_RE.search(s)
    if not m:
        print("ERROR: no <p> block found (dashboard version differs)")
        sys.exit(1)
    block = m.group(0)
    if "batch_connect_form_session_data_html" not in block and "aba-folder-lab" not in block:
        print("ERROR: matched <p> is not the session-data block")
        sys.exit(1)
    open(P, "w").write(s[:m.start()] + NEW + s[m.end():])
    print("patched -> current")


if __name__ == "__main__":
    main()
