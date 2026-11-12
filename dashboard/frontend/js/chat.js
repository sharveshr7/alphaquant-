/**
 * AlphaQuant Pro - AI Market Intelligence Chat Controller
 */

class ChatAssistant {
  constructor() {
    this.messagesContainer = document.getElementById("chat-messages-container") || document.getElementById("chat-messages");
    this.chatForm = document.getElementById("chat-form");
    this.chatInput = document.getElementById("chat-input-field") || document.getElementById("chat-input");
    this.sendBtn = document.getElementById("btn-chat-send") || document.getElementById("chat-send");
    this.isLoading = false;

    this.init();
  }

  init() {
    if (this.chatForm) {
      this.chatForm.addEventListener("submit", (e) => {
        e.preventDefault();
        this.handleSend();
      });
    }

    if (this.sendBtn && !this.chatForm) {
      this.sendBtn.addEventListener("click", () => this.handleSend());
    }

    if (this.chatInput) {
      this.chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          this.handleSend();
        }
      });
    }

    // Quick prompt chips
    document.querySelectorAll(".chip-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const query = btn.getAttribute("data-query");
        if (query && this.chatInput) {
          this.chatInput.value = query;
          this.handleSend();
        }
      });
    });
  }

  async handleSend() {
    if (!this.chatInput) return;
    const text = this.chatInput.value.trim();
    if (!text || this.isLoading) return;

    this.chatInput.value = "";
    this.appendMessage("user", text);
    this.isLoading = true;

    if (this.sendBtn) {
      this.sendBtn.disabled = true;
      this.sendBtn.textContent = "...";
    }

    // Show thinking bubble
    const loadingBubble = document.createElement("div");
    loadingBubble.className = "message-bubble ai loading";
    loadingBubble.innerHTML = "<em>⚡ AlphaQuant thinking...</em>";
    if (this.messagesContainer) {
      this.messagesContainer.appendChild(loadingBubble);
      this.scrollToBottom();
    }

    try {
      const activePair = window.appState ? (window.appState.currentPair || window.appState.pair || "BTC/USDT") : "BTC/USDT";
      const activeInterval = window.appState ? (window.appState.currentInterval || window.appState.interval || "15m") : "15m";

      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          pair: activePair,
          interval: activeInterval,
        }),
      });

      if (!response.ok) {
        throw new Error(`Server returned status ${response.status}`);
      }

      const data = await response.json();
      loadingBubble.remove();

      this.appendMessage("ai", data.response || "No response received.", data);
    } catch (err) {
      loadingBubble.remove();
      this.appendMessage(
        "ai",
        `⚠️ **Connection Note**: AI assistant response could not be loaded (${err.message}). Ensure backend server is active on http://127.0.0.1:8000.`
      );
    } finally {
      this.isLoading = false;
      if (this.sendBtn) {
        this.sendBtn.disabled = false;
        this.sendBtn.textContent = "Send";
      }
      if (this.chatInput) {
        this.chatInput.focus();
      }
    }
  }

  appendMessage(role, text, metadata = null) {
    if (!this.messagesContainer) return;

    const bubble = document.createElement("div");
    bubble.className = `message-bubble ${role}`;

    if (role === "ai") {
      try {
        const parsed = typeof marked !== "undefined" ? marked.parse(text) : text.replace(/\n/g, "<br>");
        const modelTag = metadata && metadata.model_used
          ? `<div style="margin-top:6px; font-size:10px; color:var(--blue); font-family:var(--font-mono);">⚡ ${metadata.model_used}</div>`
          : "";
        bubble.innerHTML = parsed + modelTag;
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
    if (this.messagesContainer) {
      this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }
  }
}

window.chatAssistant = null;
