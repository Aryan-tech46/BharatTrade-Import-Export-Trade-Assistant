/* BharatTrade v3 app.js — Flask-compatible (SSE /get, /upload, /clear-upload, /clear-history) + playground widgets */
(function () {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  /* ── Toast ── */
  const toastEl = $('#toast');
  let toastT = null;
  function toast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastT);
    toastT = setTimeout(() => toastEl.classList.remove('show'), 2600);
  }

  /* ── Clock (IST) / mobile / reveal ── */
  const istFmt = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  function tickClock() {
    const el = $('#liveClock');
    if (el) el.textContent = '● LIVE ' + istFmt.format(new Date()) + ' IST';
  }
  tickClock();
  setInterval(tickClock, 1000);
  $('#hamb')?.addEventListener('click', () => $('#mobileMenu')?.classList.toggle('open'));
  $$('#mobileMenu a').forEach((a) => a.addEventListener('click', () => $('#mobileMenu')?.classList.remove('open')));
  const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && e.target.classList.add('in')), { threshold: 0.12 });
  $$('.reveal').forEach((el) => io.observe(el));

  /* ── Tabs (incl. nav data-goto-tab) ── */
  function gotoTab(name) {
    $$('.tab').forEach((x) => x.classList.toggle('active', x.dataset.tab === name));
    $$('.panel').forEach((x) => x.classList.toggle('active', x.id === 'panel-' + name));
  }
  $$('.tab').forEach((t) => t.addEventListener('click', () => gotoTab(t.dataset.tab)));
  $$('[data-goto-tab]').forEach((a) => a.addEventListener('click', () => gotoTab(a.dataset.gotoTab)));

  /* ── HS Finder (local, ITC-HS hints) ── */
  const HSDB = [
    { k: ['coffee', '0901', 'cafe'], code: '0901 11 10', name: 'Coffee, neither roasted nor decaffeinated', d: 'BCD 30% · GST 5% · FSSAI + COO' },
    { k: ['sneaker', 'shoe', 'footwear', '6404', 'joota'], code: '6404 11 90', name: 'Footwear with textile uppers', d: 'BCD 20–25% · GST 18% · BIS if bulk' },
    { k: ['saree', 'sari', 'textile', 'cotton', 'fabric', 'kurti'], code: '5208 52 10', name: 'Woven cotton fabrics (sarees)', d: 'BCD 10% · GST 12% · EU-GSP benefit' },
    { k: ['tile', 'ceramic', 'morbi', 'sanitary'], code: '6907 21 00', name: 'Ceramic flags, paving & tiles', d: 'BCD 15% · GST 18% · breakage cover' },
    { k: ['spice', 'turmeric', 'masala', 'haldi', 'chilli', 'cardamom'], code: '0910 30 10', name: 'Turmeric (curcuma)', d: 'BCD 30% · FSSAI + Phytosanitary must' },
    { k: ['mango', 'dried', 'pulp', 'fruit'], code: '0804 50 20', name: 'Dried / preserved mango', d: 'BCD 30% · APEDA + Phyto' },
    { k: ['brass', 'valve', 'faucet', 'tap'], code: '8481 80 30', name: 'Brass taps, cocks & valves', d: 'BCD 12.5% · GST 18%' },
    { k: ['phone', 'mobile', 'electronic', 'laptop'], code: '8517 12 11', name: 'Smartphones', d: 'BCD 20% · BIS + WPC mandatory' },
  ];
  function hsCard(h) {
    return `<div class="hs-card"><b>${h.code}</b><p>${h.name}</p><span>${h.d}</span><br><button data-ask="Explain HS code ${h.code} (${h.name}) for export from India">Ask AI →</button></div>`;
  }
  function hsSearch(q) {
    const out = $('#hsOut');
    if (!out) return;
    q = (q || '').toLowerCase().trim();
    if (!q) { out.innerHTML = '<p class="muted">Type a product name first — e.g. coffee, saree, tiles.</p>'; return; }
    const hits = HSDB.filter((h) => h.k.some((k) => q.includes(k)));
    out.innerHTML = hits.length
      ? hits.map(hsCard).join('')
      : `<div class="hs-card"><b>??</b><p>No exact local hit — ask the AI, it searches Commerce data fuzzily.</p><span>Grounded RAG</span><br><button data-ask="What is the HS code for ${q.replace(/</g, '')}?">Ask AI about “${q.slice(0, 40)}” →</button></div>`;
    wireAskButtons(out);
  }
  $('#hsBtn')?.addEventListener('click', () => hsSearch($('#hsInput').value));
  $('#hsInput')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') hsSearch(e.target.value); });
  // defaults
  hsSearch('coffee');

  /* ── Duty Estimator: BCD + AIDC(10% of BCD) + SWS(10% of BCD+AIDC) + GST ── */
  let BCD = 20, GST = 18;
  $$('#catRow .pchip').forEach((c) => c.addEventListener('click', () => {
    $$('#catRow .pchip').forEach((x) => x.classList.remove('on'));
    c.classList.add('on');
    BCD = +c.dataset.bcd; GST = +c.dataset.gst; calcDuty();
  }));
  $('#cifRange')?.addEventListener('input', calcDuty);
  $('#laneSel')?.addEventListener('change', calcDuty);
  function calcDuty() {
    const cif = +($('#cifRange')?.value || 10000);
    const pref = +($('#laneSel')?.value || 0); // negative = FTA saving on BCD
    const effBCD = Math.max(0, BCD + pref);
    $('#cifVal').textContent = '$' + cif.toLocaleString();
    const bcd = cif * effBCD / 100, aidc = bcd * 0.1, sws = (bcd + aidc) * 0.1;
    const assess = cif + bcd + aidc + sws, gst = assess * GST / 100;
    const total = Math.round(cif + bcd + aidc + sws + gst + 120);
    $('#landedNum').textContent = '$' + total.toLocaleString();
    $('#dutyBreak').innerHTML =
      `<div><span>BCD ${effBCD}%${pref ? ' (FTA applied)' : ''}</span><b>$${Math.round(bcd).toLocaleString()}</b></div>` +
      `<div><span>AIDC + SWS surcharge</span><b>$${Math.round(aidc + sws).toLocaleString()}</b></div>` +
      `<div><span>GST ${GST}% on assessable</span><b>$${Math.round(gst).toLocaleString()}</b></div>` +
      `<div><span>Freight + Insurance + CHA</span><b>$120</b></div>`;
  }
  calcDuty();

  /* ── Docs generator ── */
  const DOCS = {
    food: ['Commercial Invoice (3 copies)', 'Packing List with batch no.', 'Bill of Lading (Sea)', 'Certificate of Origin (APEDA)', 'FSSAI + Phytosanitary Certificate', 'Health Certificate', 'Marine insurance + fumigation cert', 'IEC + AD code on ICEGATE'],
    textile: ['Commercial Invoice + Packing List', 'Bill of Lading', 'Certificate of Origin (EU-GSP REX)', 'AZO-free lab test report', 'Marine insurance', 'ISPM-15 pallets if wood', 'Export declaration on ICEGATE'],
    ceramic: ['Invoice + packing with fragility marks', 'BL + breakage insurance', 'Certificate of Origin', 'ISPM-15 pallet cert', 'Pre-shipment inspection', 'Marine insurance + IEC'],
    electronics: ['Invoice + technical datasheet', 'AWB / BL', 'Certificate of Origin', 'BIS + WPC / EPR compliance', 'MSDS if lithium battery', 'Insurance + IEC'],
  };
  function genDocs() {
    const v = $('#docProduct')?.value || 'food';
    const items = DOCS[v] || DOCS.food;
    $('#docOut').innerHTML = '<h4 style="font-size:12px;letter-spacing:.12em">YOUR CHECKLIST — ' + items.length + ' DOCS ✓</h4>' +
      items.map((d, i) => `<div class="check-item ${i < 2 ? 'done' : ''}">${i < 2 ? '✅' : '⬜'} ${d}</div>`).join('') +
      `<button class="btn btn-dark" style="margin-top:10px" data-ask="What export documents do I need for ${v} shipment from India?">Explain each in chat →</button>`;
    wireAskButtons($('#docOut'));
  }
  $('#docBtn')?.addEventListener('click', genDocs);
  $$('#modeRow .pchip').forEach((c) => c.addEventListener('click', () => {
    $$('#modeRow .pchip').forEach((x) => x.classList.remove('on')); c.classList.add('on');
  }));
  genDocs();

  /* ── Newsletter (demo) ── */
  $('#nlBtn')?.addEventListener('click', () => {
    const v = $('#nlInput')?.value.trim();
    toast(v && v.includes('@') ? 'Subscribed ✓ — trade intel incoming.' : 'Enter a valid email to subscribe.');
  });

  /* ═════════ CHAT — Flask SSE backend (IDs match original templates/chat.html) ═════════ */
  const chatBox = $('#chat-box');
  const userInput = $('#user-input');
  const sendBtn = $('#send-btn');
  const typingIndicator = $('#typing-indicator');
  const welcomeMessage = $('#welcome-message');
  const uploadBtn = $('#upload-btn');
  const fileInput = $('#file-input');
  const uploadBadge = $('#upload-badge');
  const uploadBadgeFilename = $('#upload-badge-filename');
  const uploadBadgeRemove = $('#upload-badge-remove');
  const uploadProgress = $('#upload-progress');
  const scrollBottomBtn = $('#scroll-bottom-btn');
  const clearChatBtn = $('#clear-chat-btn');

  let isWaiting = false;
  let isUserScrolledUp = false;

  // suggestion chips (original .chip[data-query])
  $$('#suggestion-chips .chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      userInput.value = chip.getAttribute('data-query') || '';
      sendMessage();
    });
  });

  // any [data-ask] button anywhere → send to chat
  function wireAskButtons(root) {
    (root || document).querySelectorAll('[data-ask]').forEach((b) => {
      if (b.dataset.wired) return;
      b.dataset.wired = '1';
      b.addEventListener('click', () => {
        userInput.value = b.dataset.ask;
        document.getElementById('assistant')?.scrollIntoView({ behavior: 'smooth' });
        setTimeout(sendMessage, 450);
      });
    });
  }
  wireAskButtons(document);

  /* file upload → POST /upload */
  uploadBtn?.addEventListener('click', () => fileInput?.click());
  fileInput?.addEventListener('change', () => {
    const file = fileInput.files[0];
    if (!file) return;
    uploadProgress.style.display = 'flex';
    if (uploadBadge) uploadBadge.style.display = 'none';
    const formData = new FormData();
    formData.append('file', file);
    fetch('/upload', { method: 'POST', body: formData })
      .then((res) => res.json())
      .then((data) => {
        uploadProgress.style.display = 'none';
        if (data.status === 'ok') {
          uploadBadgeFilename.textContent = data.filename;
          uploadBadge.style.display = 'block';
          userInput.placeholder = 'Ask about your business — get personalized suggestions...';
          appendMessage('bot', `📂 File "${data.filename}" loaded! I can now give personalized suggestions based on your business data.\n\nTry asking: "Which new markets should I target?" or "How can I grow my exports?"`, ['📊 Uploaded Business Data']);
          toast('File loaded ✓ — personalised mode ON');
        } else {
          appendMessage('bot', `❌ Upload failed: ${data.message}`, []);
        }
      })
      .catch((err) => {
        console.error('Upload error:', err);
        uploadProgress.style.display = 'none';
        appendMessage('bot', '❌ Upload failed. Is Flask running? (`python app.py`)', []);
      });
    fileInput.value = '';
  });
  uploadBadgeRemove?.addEventListener('click', () => {
    fetch('/clear-upload', { method: 'POST' })
      .then((res) => res.json())
      .then(() => {
        uploadBadge.style.display = 'none';
        userInput.placeholder = 'Ask about import/export, trade data, HS codes...';
        appendMessage('bot', '🗑️ Business data removed. Back to standard mode.', []);
      })
      .catch(() => { uploadBadge.style.display = 'none'; });
  });

  /* markdown */
  if (typeof marked !== 'undefined' && marked && marked.setOptions) {
    try { marked.setOptions({ breaks: true, gfm: true }); } catch (e) { /* noop */ }
  }
  function renderMarkdown(text) {
    if (!text) return '';
    if (typeof marked !== 'undefined' && marked && marked.parse) {
      try { return marked.parse(text); } catch (e) { console.error(e); }
    }
    return fallbackMarkdown(text);
  }
  function fallbackMarkdown(text) {
    let safe = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    safe = safe.replace(/^### (.*$)/gim, '<h3>$1</h3>').replace(/^## (.*$)/gim, '<h2>$1</h2>').replace(/^# (.*$)/gim, '<h1>$1</h1>');
    safe = safe.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\*(.*?)\*/g, '<em>$1</em>');
    const lines = safe.split('\n');
    let inTable = false, html = [], headDone = false;
    for (const ln of lines) {
      const line = ln.trim();
      if (line.startsWith('|') && line.endsWith('|')) {
        if (/^\|[-:\s|]+\|$/.test(line)) { headDone = true; continue; }
        if (!inTable) { inTable = true; headDone = false; html.push('<table>'); }
        const cells = line.split('|').slice(1, -1);
        const tag = headDone ? 'td' : 'th';
        html.push('<tr>' + cells.map((c) => `<${tag}>${c.trim()}</${tag}>`).join('') + '</tr>');
      } else {
        if (inTable) { html.push('</table>'); inTable = false; }
        if (line) html.push(`<p>${line}</p>`);
      }
    }
    if (inTable) html.push('</table>');
    return html.join('');
  }

  /* SSE streaming send */
  async function sendMessage() {
    const msg = userInput.value.trim();
    if (!msg || isWaiting) return;
    if (welcomeMessage) welcomeMessage.style.display = 'none';
    appendMessage('user', msg);
    userInput.value = '';
    isWaiting = true;
    if (sendBtn) sendBtn.disabled = true;
    isUserScrolledUp = false;
    if (scrollBottomBtn) scrollBottomBtn.style.display = 'none';
    typingIndicator?.classList.add('active');
    scrollToBottom(true);

    let botElements = null, sourcesRendered = false, botFullText = '';
    try {
      const formData = new FormData();
      formData.append('msg', msg);
      const response = await fetch('/get', { method: 'POST', body: formData });
      if (!response.ok) throw new Error(`Server error (${response.status})`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(trimmed.slice(6));
            if (data.type === 'token') {
              if (!botElements) { typingIndicator?.classList.remove('active'); botElements = createBotMessage(); }
              botFullText += data.token;
              botElements.bubble.innerHTML = renderMarkdown(botFullText);
              scrollToBottom();
            } else if (data.type === 'sources') {
              if (data.sources?.length) {
                if (!botElements) { typingIndicator?.classList.remove('active'); botElements = createBotMessage(); }
                if (!sourcesRendered) { renderSources(botElements.content, data.sources); sourcesRendered = true; }
              }
            } else if (data.type === 'error') {
              typingIndicator?.classList.remove('active');
              if (!botElements) botElements = createBotMessage();
              botElements.bubble.textContent = data.message || '⚠️ An error occurred.';
              scrollToBottom();
            }
          } catch (e) { console.error('SSE parse error:', e); }
        }
      }
      // graceful non-stream JSON fallback (some Flask versions return plain JSON)
      if (!botElements && botFullText === '') {
        // if body was JSON not SSE, try reading leftover buffer
        if (buffer.trim().startsWith('{')) {
          try {
            const j = JSON.parse(buffer.trim());
            botElements = createBotMessage();
            botElements.bubble.innerHTML = renderMarkdown(j.answer || '(empty answer)');
            if (j.sources?.length) renderSources(botElements.content, j.sources);
          } catch (e) { /* ignore */ }
        }
      }
      if (!botElements) {
        typingIndicator?.classList.remove('active');
        appendMessage('bot', '⚠️ Empty response from server. Please try again.', []);
      }
    } catch (err) {
      console.error('Streaming error:', err);
      typingIndicator?.classList.remove('active');
      if (!botElements) appendMessage('bot', '⚠️ Could not connect to the server. Please ensure `python app.py` is running.', []);
      else botElements.bubble.textContent += '\n\n⚠️ Connection interrupted.';
    } finally {
      typingIndicator?.classList.remove('active');
      isWaiting = false;
      if (sendBtn) sendBtn.disabled = false;
      userInput.focus();
      scrollToBottom();
    }
  }

  function createBotMessage() {
    const row = document.createElement('div');
    row.className = 'message-row bot';
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = '🤖';
    const content = document.createElement('div');
    content.className = 'message-content';
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    content.appendChild(bubble);
    row.appendChild(avatar);
    row.appendChild(content);
    chatBox.appendChild(row);
    scrollToBottom();
    return { row, content, bubble };
  }
  function renderSources(contentDiv, sources = []) {
    if (!sources?.length || contentDiv.querySelector('.message-sources')) return;
    const div = document.createElement('div');
    div.className = 'message-sources';
    sources.forEach((src) => {
      const tag = document.createElement('span');
      tag.className = 'source-tag';
      const lower = String(src).toLowerCase();
      if (lower.includes('export') || lower.includes('uploaded')) tag.classList.add('export');
      else if (lower.includes('law') || lower.includes('regulation')) tag.classList.add('laws');
      else if (lower.includes('book') || lower.includes('weiss')) tag.classList.add('book');
      tag.textContent = src;
      tag.title = src;
      div.appendChild(tag);
    });
    contentDiv.appendChild(div);
    scrollToBottom();
  }
  function appendMessage(role, text, sources = []) {
    const row = document.createElement('div');
    row.className = `message-row ${role}`;
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? '🧑' : '🤖';
    const content = document.createElement('div');
    content.className = 'message-content';
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    if (role === 'bot') bubble.innerHTML = renderMarkdown(text);
    else bubble.textContent = text;
    content.appendChild(bubble);
    if (role === 'bot' && sources.length) renderSources(content, sources);
    row.appendChild(avatar);
    row.appendChild(content);
    chatBox.appendChild(row);
    scrollToBottom();
  }

  /* smart scroll */
  chatBox?.addEventListener('wheel', (e) => {
    if (e.deltaY < 0) { isUserScrolledUp = true; if (scrollBottomBtn) scrollBottomBtn.style.display = 'flex'; }
    else if (e.deltaY > 0) {
      const dist = chatBox.scrollHeight - chatBox.scrollTop - chatBox.clientHeight;
      if (dist <= 30) { isUserScrolledUp = false; if (scrollBottomBtn) scrollBottomBtn.style.display = 'none'; }
    }
  }, { passive: true });
  let touchY = 0;
  chatBox?.addEventListener('touchstart', (e) => { touchY = e.touches[0].clientY; }, { passive: true });
  chatBox?.addEventListener('touchmove', (e) => {
    if (e.touches[0].clientY > touchY + 5) { isUserScrolledUp = true; if (scrollBottomBtn) scrollBottomBtn.style.display = 'flex'; }
  }, { passive: true });
  chatBox?.addEventListener('scroll', () => {
    const d = chatBox.scrollHeight - chatBox.scrollTop - chatBox.clientHeight;
    if (d > 40) { isUserScrolledUp = true; if (scrollBottomBtn) scrollBottomBtn.style.display = 'flex'; }
    else if (d <= 15) { isUserScrolledUp = false; if (scrollBottomBtn) scrollBottomBtn.style.display = 'none'; }
  });
  scrollBottomBtn?.addEventListener('click', () => scrollToBottom(true));
  function scrollToBottom(force) {
    if (!chatBox) return;
    if (force) { isUserScrolledUp = false; if (scrollBottomBtn) scrollBottomBtn.style.display = 'none'; chatBox.scrollTop = chatBox.scrollHeight; return; }
    if (!isUserScrolledUp) chatBox.scrollTop = chatBox.scrollHeight;
  }

  /* input events */
  sendBtn?.addEventListener('click', sendMessage);
  userInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  clearChatBtn?.addEventListener('click', () => {
    fetch('/clear-history', { method: 'POST' })
      .catch((err) => console.error(err))
      .finally(() => { window.location.href = '/'; });
  });
  userInput?.focus();
})();
