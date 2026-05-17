(() => {
  const MIN_TREE = 180, MAX_TREE = 520;
  const MIN_RIGHT = 200, MAX_RIGHT = 520;
  const MIN_TOP = 160, MIN_BOTTOM = 160;

  const app = document.querySelector('.app');
  if (!app) return;

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function px(name) { return parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name)); }
  function setVar(name, v) { document.documentElement.style.setProperty(name, v + 'px'); }

  /* --- column resizers (horizontal drag, leftmost rail stays fixed) --- */
  function makeColResizer(cls) {
    const el = document.createElement('div');
    el.className = `resizer ${cls}`;
    app.appendChild(el);
    return el;
  }
  const rTree  = makeColResizer('resizer--tree');
  const rRight = makeColResizer('resizer--right');

  function bindColDrag(handle, onDelta) {
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      handle.classList.add('is-dragging');
      document.body.classList.add('is-resizing');
      const startX = e.clientX;
      const start = onDelta(0, true);
      function onMove(ev) { onDelta(ev.clientX - startX, false, start); }
      function onUp() {
        handle.classList.remove('is-dragging');
        document.body.classList.remove('is-resizing');
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      }
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
  }

  bindColDrag(rTree, (dx, init, start) => {
    if (init) return { tree: px('--w-tree') };
    setVar('--w-tree', clamp(start.tree + dx, MIN_TREE, MAX_TREE));
  });

  bindColDrag(rRight, (dx, init, start) => {
    if (init) return { right: px('--w-right') };
    setVar('--w-right', clamp(start.right - dx, MIN_RIGHT, MAX_RIGHT));
  });

  /* --- pane divider inside .main.split (vertical drag of horizontal divider) --- */
  document.querySelectorAll('.pane-divider').forEach((handle) => {
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      const main = handle.closest('.main.split');
      if (!main) return;
      const topPane = main.querySelector('.pane--top');
      const mainBox = main.getBoundingClientRect();
      const startY = e.clientY;
      const startTop = topPane.getBoundingClientRect().height;
      handle.classList.add('is-dragging');
      document.body.classList.add('is-resizing-v');
      function onMove(ev) {
        const next = clamp(startTop + (ev.clientY - startY), MIN_TOP, mainBox.height - MIN_BOTTOM);
        document.documentElement.style.setProperty('--h-top', next + 'px');
      }
      function onUp() {
        handle.classList.remove('is-dragging');
        document.body.classList.remove('is-resizing-v');
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      }
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
  });
})();
