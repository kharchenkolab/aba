// Launch is greyed out while the Lab field holds the form's "not enrolled" option.
//
// form.yml.erb renders that option when none of the user's groups is enrolled:
// an EMPTY value whose label starts with REFUSAL and names the lab to enrol. A
// launch from it cannot succeed, so the button says so instead of being pressed.
// This is only the visible half. submit.yml.erb refuses the same launch before
// OnDemand submits anything — the guarantee, which holds even where this file is
// not loaded. An empty value alone is NOT a refusal: a deployment that does not
// use labs renders one too, and launches from it.
(function () {
  'use strict';
  var REFUSAL = '— not enrolled';          // == refusal in form.yml.erb
  var LAB = 'select[id$="_aba_lab"], select[name$="[aba_lab]"]';
  var MARK = 'data-aba-refused';

  function sync() {
    var lab = document.querySelector(LAB);
    if (!lab || !lab.form) return;
    var opt = lab.options[lab.selectedIndex];
    var refused = lab.value === '' && !!opt && opt.text.indexOf(REFUSAL) === 0;
    var buttons = lab.form.querySelectorAll('[type="submit"]');
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      if (refused) {
        b.disabled = true;
        b.setAttribute('title', opt.text.replace(/^—\s*/, ''));
        b.setAttribute(MARK, '');
      } else if (b.hasAttribute(MARK)) {       // undo only what this file did
        b.disabled = false;
        b.removeAttribute('title');
        b.removeAttribute(MARK);
      }
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync);
  else sync();
  document.addEventListener('change', function (e) {
    if (e.target && e.target.matches && e.target.matches(LAB)) sync();
  });
})();
