/* Local-only Markdown renderer. Message text is data, never JavaScript. */
'use strict';
(() => {
  const host = document.querySelector('#message');
  const preview = document.querySelector('#preview');
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let bridge, revision = -1, generation = 0, timer, pending, lastSource = '';
  const diagrams = new Map();
  const textButton = (label, action) => {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = label;
    b.addEventListener('click', action); return b;
  };
  function copy(text, button) {
    const label = button.textContent;
    bridge?.copy(text); button.textContent = '已复制';
    setTimeout(() => { if (button.isConnected) button.textContent = label; }, 1400);
  }
  function measure() {
    if (revision < 0) return;
    bridge?.measure(revision, Math.max(1, Math.ceil(host.getBoundingClientRect().height)));
  }
  new ResizeObserver(measure).observe(host);
  document.fonts.ready.then(measure);
  addEventListener('scroll', () => bridge?.scroll(revision, scrollY), {passive:true});
  document.querySelector('#preview-close').onclick = () => preview.close();
  function zoom(node) {
    const target = document.querySelector('#preview-content');
    target.replaceChildren(node.cloneNode(true)); preview.showModal();
  }
  function safeTarget(raw, image = false) {
    try {
      if (raw.startsWith('#')) return image ? '' : raw;
      const url = new URL(raw, bridge?.baseUrl || location.href);
      if (['https:','http:','file:'].includes(url.protocol)) return url.href;
      if (!image && ['mailto:','codex:'].includes(url.protocol)) return url.href;
      if (image && /^data:image\/(?:png|jpeg|gif|webp|avif);base64,/i.test(raw)) return raw;
    } catch (_) { /* show an inert label for unsupported attachment schemes */ }
    return '';
  }
  function mathToken(src, block) {
    const expression = block ? /^(?:\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\])(?:\n|$)/
      : /^(?:\\\(([\s\S]+?)\\\)|\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d))/;
    const match = expression.exec(src);
    if (match) return {type: block ? 'mathBlock' : 'mathInline', raw:match[0], text:match[1] || match[2], display:block};
  }
  marked.use({gfm:true, breaks:true, extensions: ['block','inline'].map(level => ({
    name:level === 'block' ? 'mathBlock' : 'mathInline', level,
    start(src) { const i = src.search(level === 'block' ? /\$\$|\\\[/ : /\$|\\\(/); return i < 0 ? undefined : i; },
    tokenizer(src) { return mathToken(src, level === 'block'); },
    renderer(token) { return `<span class="math-source" data-math="${esc(encodeURIComponent(token.text))}" data-display="${token.display}">${esc(token.raw)}</span>`; }
  })), renderer: {
    link(token) { return `<a data-target="${esc(encodeURIComponent(token.href))}">${this.parser.parseInline(token.tokens)}</a>`; },
    image(token) { return `<span class="image-source" data-image="${esc(encodeURIComponent(token.href))}" data-alt="${esc(token.text)}">${esc(token.text || '图片')}</span>`; },
    code(token) {
      const language = (token.lang || '').split(/\s/)[0].toLowerCase();
      const closed = /(?:`{3,}|~{3,})\s*$/.test(token.raw.trim());
      return `<pre><code data-language="${esc(language)}" data-closed="${closed}">${esc(token.text)}</code></pre>`;
    }
  }});
  mermaid.initialize({startOnLoad:false, securityLevel:'strict', theme:'dark',
    suppressErrorRendering:true, maxTextSize:50000, htmlLabels:false,
    flowchart:{htmlLabels:false}, fontFamily:'-apple-system, sans-serif'});

  async function drawDiagram(code, ticket) {
    const source = code.textContent;
    // Unclosed streaming fences remain readable source until the closing fence.
    if (code.dataset.closed !== 'true') return;
    try {
      let svg = diagrams.get(source);
      if (!svg) {
        // Per-diagram directives cannot override the host's rendering policy.
        const safeSource = source.replace(/%%\{[\s\S]*?\}%%/g, '').replace(/^---\n[\s\S]*?\n---\n/, '');
        const result = await mermaid.render('diagram-' + ticket + '-' + Math.random().toString(36).slice(2), safeSource);
        svg = DOMPurify.sanitize(result.svg, {USE_PROFILES:{svg:true,svgFilters:true}, FORBID_TAGS:['foreignObject','a']});
        if (diagrams.size >= 32) diagrams.delete(diagrams.keys().next().value);
        diagrams.set(source, svg);
      }
      if (ticket !== generation || !code.isConnected) return;
      const figure = document.createElement('figure'); const header = document.createElement('header');
      const label = document.createElement('span'); label.textContent = 'Mermaid'; header.append(label);
      const content = document.createElement('div'); content.className = 'diagram'; content.innerHTML = svg;
      const copySource = textButton('复制源码', () => copy(source, copySource)); header.append(copySource);
      header.append(textButton('放大', () => zoom(content.firstElementChild)));
      figure.append(header, content);
      const detail = document.createElement('details'), summary = document.createElement('summary');
      summary.textContent = '查看源码'; detail.append(summary, code.parentElement.cloneNode(true)); figure.append(detail);
      code.closest('.code-block').replaceWith(figure); measure();
    } catch (_) {
      if (ticket !== generation || !code.isConnected) return;
      const note = document.createElement('div'); note.className = 'render-note';
      note.textContent = '图表尚未完成或语法不支持，保留源码。'; code.parentElement.before(note); measure();
    }
  }

  async function render(source, nextRevision) {
    const ticket = ++generation, oldY = scrollY;
    revision = nextRevision; lastSource = source;
    if (preview.open) preview.close();
    try {
      host.style.whiteSpace = '';
      host.innerHTML = DOMPurify.sanitize(marked.parse(source), {
        USE_PROFILES:{html:true}, FORBID_TAGS:['script','style','iframe','object','embed','form','button','textarea','video','audio'],
        FORBID_ATTR:['style','srcset','src','href','id','name'], ALLOW_DATA_ATTR:true
      });
      for (const a of host.querySelectorAll('a[data-target]')) {
        const url = safeTarget(decodeURIComponent(a.dataset.target));
        if (!url) { a.replaceWith(document.createTextNode(a.textContent)); continue; }
        a.href = url; a.title = url; a.rel = 'noreferrer noopener';
        a.onclick = event => {
          event.preventDefault();
          if (url.startsWith('#')) document.getElementById(url.slice(1))?.scrollIntoView();
          else bridge?.openLink(url);
        };
      }
      const anchors = new Map();
      for (const heading of host.querySelectorAll('h1,h2,h3,h4,h5,h6')) {
        const slug = heading.textContent.toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu,'').trim().replace(/\s+/g,'-');
        const count = anchors.get(slug) || 0; anchors.set(slug,count+1);
        heading.id = slug + (count ? '-' + count : '');
      }
      for (const el of host.querySelectorAll('.image-source')) {
        const url = safeTarget(decodeURIComponent(el.dataset.image), true);
        if (!url) { el.className = 'image-error'; el.textContent += '（此附件地址无法预览）'; continue; }
        const img = document.createElement('img'); img.alt = el.dataset.alt || '图片';
        img.referrerPolicy = 'no-referrer'; img.src = url;
        img.onload = measure; img.onclick = () => zoom(img);
        img.onerror = () => {
          const error = document.createElement('span'); error.className = 'image-error';
          error.textContent = img.alt + '（图片未能加载）'; img.replaceWith(error); measure();
        };
        el.replaceWith(img);
      }
      for (const table of host.querySelectorAll('table')) {
        const wrap = document.createElement('div'); wrap.className = 'table-scroll'; table.before(wrap); wrap.append(table);
      }
      for (const math of host.querySelectorAll('.math-source')) {
        try { katex.render(decodeURIComponent(math.dataset.math), math, {displayMode:math.dataset.display === 'true',
          throwOnError:false, trust:false, strict:'ignore', maxExpand:1000, maxSize:20}); }
        catch (_) { math.classList.add('render-note'); }
      }
      const tasks = [];
      for (const code of host.querySelectorAll('pre > code')) {
        const sourceCode = code.textContent, language = code.dataset.language || 'text';
        const block = document.createElement('div'); block.className = 'code-block';
        const bar = document.createElement('div'); bar.className = 'code-toolbar';
        const label = document.createElement('span'); label.textContent = language;
        const button = textButton('复制', () => copy(sourceCode, button)); bar.append(label, button);
        const pre = code.parentElement; pre.before(block); block.append(bar, pre);
        if (language === 'mermaid') tasks.push(() => drawDiagram(code, ticket));
        else if (hljs.getLanguage(language)) {
          code.innerHTML = hljs.highlight(sourceCode, {language, ignoreIllegals:true}).value; code.classList.add('hljs');
        }
      }
      const toolbar = document.createElement('div'); toolbar.className = 'reply-toolbar';
      const button = textButton('复制全文', () => copy(source, button)); toolbar.append(button); host.append(toolbar);
      measure(); window.scrollTo(0, oldY);
      for (const draw of tasks) { if (ticket !== generation) break; await draw(); }
      measure();
    } catch (_) {
      // Malformed streaming input must never erase a message.
      host.textContent = source; host.style.whiteSpace = 'pre-wrap'; measure();
    }
  }
  function schedule() {
    pending = {source:bridge.source, revision:bridge.revision};
    if (timer) return;
    timer = setTimeout(() => {
      timer = null;
      if (getSelection()?.toString()) return; // Preserve text the user is copying.
      const update = pending; pending = null;
      if (update) render(update.source, update.revision);
    }, 80);
  }
  document.addEventListener('selectionchange', () => { if (pending && !getSelection()?.toString()) schedule(); });
  // Also serves as a deterministic local fixture entrypoint; no network service.
  window.AgentMessage = {render, notify(text) {
    const note = document.createElement('p'); note.className = 'render-note';
    note.setAttribute('role','status'); note.textContent = text; host.append(note);
    note.scrollIntoView({block:'nearest'}); setTimeout(() => note.remove(),4000);
  }, get source() { return lastSource; }, get revision() { return revision; }};
  if (typeof QWebChannel !== 'undefined' && window.qt?.webChannelTransport) {
    new QWebChannel(qt.webChannelTransport, channel => {
      bridge = channel.objects.messageBridge;
      bridge.sourceChanged.connect(schedule); bridge.revisionChanged.connect(schedule); schedule();
    });
  }
})();
