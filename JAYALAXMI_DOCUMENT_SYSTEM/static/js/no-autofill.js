/* Disable browser autofill / saved-input suggestions on every form and field, including ones added later. */
(function () {
  function lock(el) {
    var tag = el.tagName;
    if (tag === 'FORM') { el.setAttribute('autocomplete', 'off'); return; }
    if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') return;
    if (el.type === 'hidden' || el.type === 'file') return;
    el.setAttribute('autocomplete', el.type === 'password' ? 'new-password' : 'off');
    el.removeAttribute('list');
    el.setAttribute('autocorrect', 'off');
    el.setAttribute('autocapitalize', 'off');
    el.setAttribute('spellcheck', el.hasAttribute('data-spellcheck') ? 'true' : 'false');
  }
  function scan(root) {
    if (root.nodeType !== 1) return;
    lock(root);
    root.querySelectorAll('form,input,textarea,select').forEach(lock);
  }
  scan(document.documentElement);
  new MutationObserver(function (ms) {
    ms.forEach(function (m) { m.addedNodes.forEach(scan); });
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
