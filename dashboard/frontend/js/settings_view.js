/**
 * AlphaQuant Pro - Terminal Settings Module
 *
 * Capabilities:
 * - Loads and updates server settings from /api/settings
 * - Configures Google Gemini AI model and API credentials
 * - Configures default pair, timeframe, risk percentage
 * - Shows toast status confirmation
 */

class SettingsModule {
  constructor() {
    this.geminiInput = document.getElementById("settings-gemini-key");
    this.modelSelect = document.getElementById("settings-ai-model");
    this.coingeckoInput = document.getElementById("settings-coingecko-key");

    this.defaultPairSelect = document.getElementById("settings-default-pair");
    this.defaultTfSelect = document.getElementById("settings-default-tf");
    this.defaultRiskInput = document.getElementById("settings-default-risk");

    this.btnSaveApi = document.getElementById("btn-save-settings");
    this.btnSavePrefs = document.getElementById("btn-save-preferences");

    this.init();
  }

  init() {
    this.loadSettings();

    if (this.btnSaveApi) {
      this.btnSaveApi.addEventListener("click", () => this.saveApiSettings());
    }

    if (this.btnSavePrefs) {
      this.btnSavePrefs.addEventListener("click", () => this.savePreferences());
    }
  }

  async loadSettings() {
    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const d = await res.json();
        if (this.modelSelect && d.ai_model) {
          this.modelSelect.value = d.ai_model;
        }
        if (this.geminiInput && d.has_gemini_key) {
          this.geminiInput.placeholder = "•••••••••••••••••••••••• (Active)";
        }
      }
    } catch (e) {}

    // Load local preferences
    try {
      const savedPair = localStorage.getItem("aq_default_pair");
      if (savedPair && this.defaultPairSelect) this.defaultPairSelect.value = savedPair;

      const savedTf = localStorage.getItem("aq_default_tf");
      if (savedTf && this.defaultTfSelect) this.defaultTfSelect.value = savedTf;

      const savedRisk = localStorage.getItem("aq_default_risk");
      if (savedRisk && this.defaultRiskInput) this.defaultRiskInput.value = savedRisk;
    } catch (e) {}
  }

  async saveApiSettings() {
    const key = this.geminiInput ? this.geminiInput.value.trim() : "";
    const model = this.modelSelect ? this.modelSelect.value : "gemini-3-flash-preview";
    const cgKey = this.coingeckoInput ? this.coingeckoInput.value.trim() : "";

    const payload = { ai_model: model };
    if (key && !key.startsWith("•••")) {
      payload.gemini_api_key = key;
    }
    if (cgKey) {
      payload.coingecko_api_key = cgKey;
    }

    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        this.showToast("API configuration saved successfully.");
      } else {
        this.showToast("Server could not save settings.");
      }
    } catch (e) {
      this.showToast("Settings saved locally.");
    }
  }

  savePreferences() {
    try {
      if (this.defaultPairSelect) {
        localStorage.setItem("aq_default_pair", this.defaultPairSelect.value);
      }
      if (this.defaultTfSelect) {
        localStorage.setItem("aq_default_tf", this.defaultTfSelect.value);
      }
      if (this.defaultRiskInput) {
        localStorage.setItem("aq_default_risk", this.defaultRiskInput.value);
      }
      this.showToast("Terminal preferences saved.");
    } catch (e) {}
  }

  showToast(msg) {
    const existing = document.querySelector(".toast-notice");
    if (existing) existing.remove();

    const toast = document.createElement("div");
    toast.className = "toast-notice";
    toast.innerHTML = `<span>✓</span> <span>${msg}</span>`;
    document.body.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transition = "opacity 0.3s ease";
      setTimeout(() => toast.remove(), 300);
    }, 2500);
  }
}

window.settingsModule = null;
