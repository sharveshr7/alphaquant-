/**
 * Chat Controller - AI Market Assistant
 */

class ChatAssistant {
  constructor() {
    this.messagesContainer = document.getElementById('chat-messages');
    this.chatForm = document.getElementById('chat-form');
    this.chatInput = document.getElementById('chat-input');
    this.modelTag = document.getElementById('ai-model-tag');
    this.sendBtn = document.getElementById('chat-send');
    this.isLoading = false;

    this.init();
  }

  init() {
    if (this.chatForm) {
      this.chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        this.handleSend();
      });
    }

    // Quick prompt chips
    document.querySelectorAll('.chip-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const query = btn.getAttribute('data-query');
        if (query) {
          this.chatInput.value = query;
          this.handleSend();
        }
      });
    });
  }

  async handleSend() {
    const text = this.chatInput.value.trim();
    if (!text || this.isLoading) return;

    this.chatInput.value = '';
    this.appendMessage('user', text);
    this.isLoading = true;
    this.sendBtn.disabled = true;
    this.sendBtn.textContent = '...';

    // Show loading bubble
    const loadingBubble = document.createElement('div');
    loadingBubble.className = 'message-bubble ai loading';
    loadingBubble.innerHTML = '<span>⚡ AlphaQuant thinking...</span>';
    this.messagesContainer.appendChild(loadingBubble);
    this.scrollToBottom();

    try {
      const activePair = window.appState ? window.appState.pair : 'BTC/USDT';
      const activeInterval = window.appState ? window.appState.interval : '15m';

      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          pair: activePair,
          interval: activeInterval,
        }),
      });

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }

      const data = await response.json();
      loadingBubble.remove();

      // Render Markdown response
      this.appendMessage('ai', data.response, data);

      if (data.model_used && this.modelTag) {
        this.modelTag.textContent = data.model_used;
      }

    } catch (err) {
      loadingBubble.remove();
      this.appendMessage('ai', `⚠️ **Connection Error**: Unable to reach backend AI assistant (${err.message}). Check that the local server is running on http://127.0.0.1:8000.`);
    } finally {
      this.isLoading = false;
      this.sendBtn.disabled = false;
      this.sendBtn.textContent = 'Send';
      this.chatInput.focus();
    }
  }

  appendMessage(role, text, metadata = null) {
    const bubble = document.createElement('div');
    bubble.className = `message-bubble ${role}`;

    if (role === 'ai') {
      try {
        bubble.innerHTML = typeof marked !== 'undefined' ? marked.parse(text) : text.replace(/\n/g, '<br>');
      } catch (e) {
        bubble.innerText = text;
      }
    } else {
      bubble.textContent = text;
    }

    this.messagesContainer.appendChild(bubble);
    this.scrollToBottom();
  }

  scrollToBottom() {
    this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
  }
}

window.chatAssistant = null;
