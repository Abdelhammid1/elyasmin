// ASSISTANT: talks to /help/assistant/ask. First fetch() in the project, so the
// CSRF token has to be sent explicitly — CSRFProtect is active app-wide.
//
// The conversation lives in sessionStorage, not a plain variable: the widget is
// rendered per page (this is not an SPA), so a bare variable was wiped by every
// navigation and the user felt the chat kept closing on them. sessionStorage
// survives navigation and refresh, and clears when the tab closes — which is a
// free, natural reset. That matters because the history is replayed to the model
// on every question, so a conversation that never ends quietly makes each one
// more expensive.
(function () {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = meta ? meta.content : '';

  // Namespaced by user so a different login in the same tab does not inherit
  // the previous person's conversation.
  const userId = document.body.dataset.userId || 'anon';
  const STORAGE_KEY = 'assistant_history_' + userId;
  // Keep this aligned with AI_MAX_HISTORY_MESSAGES in config.py.
  const MAX_STORED = 10;

  let history = [];

  // sessionStorage throws in some private-browsing modes and when over quota.
  // Falling back to the in-memory array means the assistant still works — it
  // just behaves the way it did before this fix.
  function loadHistory() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter(function (m) {
        return m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string';
      // Capped on the way in as well as out: sessionStorage is editable by hand,
      // and without this a stuffed key would render thousands of bubbles.
      }).slice(-MAX_STORED);
    } catch (err) {
      return [];
    }
  }

  function saveHistory() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-MAX_STORED)));
    } catch (err) {
      /* keep going with the in-memory copy */
    }
  }

  function clearHistory() {
    history = [];
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch (err) {
      /* nothing to clean up */
    }
  }

  const panel = document.getElementById('assistantPanel');
  const chatBox = function () { return document.getElementById('assistant-messages'); };

  function appendMessage(role, text, isLoading) {
    const div = document.createElement('div');
    div.className = 'assistant-msg assistant-msg-' + role + (isLoading ? ' loading' : '');
    div.textContent = text;
    const box = chatBox();
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }

  const GREETING = 'اسألني عن أي خطوة في النظام وأنا أشرحهالك. لو المشكلة تقنية كلّم الدعم مباشرة.';

  // Render the transcript from the stored history. Without this the panel would
  // look empty while the model still remembered the conversation — more
  // confusing than the bug being fixed.
  function renderTranscript() {
    const box = chatBox();
    if (!box) return;
    box.innerHTML = '';
    if (!history.length) {
      appendMessage('assistant', GREETING);
      return;
    }
    history.forEach(function (m) { appendMessage(m.role, m.content); });
  }

  async function askAssistant(message) {
    appendMessage('user', message);
    const loadingEl = appendMessage('assistant', 'بيفكر...', true);

    try {
      const res = await fetch('/help/assistant/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({
          message: message,
          history: history.slice(-MAX_STORED),
          current_page: document.body.dataset.currentPage || 'غير معروف',
        }),
      });
      const data = await res.json();
      loadingEl.remove();

      if (!res.ok || !data.answer) {
        // Failed turns are not stored: only completed pairs are worth replaying.
        appendMessage('assistant', data.error || 'حصل خطأ، جرب تاني.');
        return;
      }
      appendMessage('assistant', data.answer);
      history.push({ role: 'user', content: message });
      history.push({ role: 'assistant', content: data.answer });
      history = history.slice(-MAX_STORED);
      saveHistory();
    } catch (err) {
      loadingEl.remove();
      appendMessage('assistant', 'مفيش اتصال بالسيرفر دلوقتي، جرب تاني.');
    }
  }

  document.getElementById('assistant-form')?.addEventListener('submit', function (e) {
    e.preventDefault();
    const input = document.getElementById('assistant-input');
    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';
    askAssistant(msg);
  });

  // Without a reset the history grows and every question costs more.
  document.getElementById('assistant-reset')?.addEventListener('click', function () {
    clearHistory();
    chatBox().innerHTML = '';
    appendMessage('assistant', 'محادثة جديدة. اسأل براحتك.');
  });

  function togglePanel(show) {
    if (!panel) return;
    panel.hidden = show === undefined ? !panel.hidden : !show;
    if (!panel.hidden) document.getElementById('assistant-input')?.focus();
  }
  document.getElementById('assistantOpen')?.addEventListener('click', function (e) {
    e.preventDefault();
    togglePanel(true);
  });
  document.getElementById('assistantClose')?.addEventListener('click', function () {
    togglePanel(false);
  });

  history = loadHistory();
  renderTranscript();
})();
