// ASSISTANT: talks to /help/assistant/ask. First fetch() in the project, so the
// CSRF token has to be sent explicitly — CSRFProtect is active app-wide.
(function () {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = meta ? meta.content : '';
  let history = [];

  const panel = document.getElementById('assistantPanel');
  const chatBox = () => document.getElementById('assistant-messages');

  function appendMessage(role, text, isLoading) {
    const div = document.createElement('div');
    div.className = 'assistant-msg assistant-msg-' + role + (isLoading ? ' loading' : '');
    div.textContent = text;
    const box = chatBox();
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
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
          history: history.slice(-10),
          current_page: document.body.dataset.currentPage || 'غير معروف',
        }),
      });
      const data = await res.json();
      loadingEl.remove();

      if (!res.ok) {
        appendMessage('assistant', data.error || 'حصل خطأ، جرب تاني.');
        return;
      }
      appendMessage('assistant', data.answer);
      history.push({ role: 'user', content: message });
      history.push({ role: 'assistant', content: data.answer });
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
    history = [];
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
})();
