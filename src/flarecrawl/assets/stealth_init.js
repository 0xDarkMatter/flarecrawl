// Stealth init script — runs before any page JS via Page.addScriptToEvaluateOnNewDocument.
// Patches the fingerprints that bot-detection engines (Cloudflare Bot Management,
// DataDome, PerimeterX, Akamai BMP) most commonly check.
//
// References:
//   - https://github.com/berstend/puppeteer-extra/tree/master/packages/puppeteer-extra-plugin-stealth
//   - https://github.com/Niek/chromium-undetected
//   - https://intoli.com/blog/not-possible-to-block-chrome-headless/
//
// Vendored idea-by-idea, keep this file < 200 lines. v0.24.0 P2.2a.

(() => {
  // Idempotent: if already patched in this context, skip.
  if (window.__flarecrawl_stealth_applied) return;
  window.__flarecrawl_stealth_applied = true;

  // 1. navigator.webdriver — most common headless tell
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined,
      configurable: true,
    });
  } catch (e) {}

  // 2. window.chrome — must exist on real Chrome
  if (!window.chrome) {
    Object.defineProperty(window, 'chrome', {
      value: { runtime: {}, loadTimes: function () {}, csi: function () {} },
      writable: false,
      enumerable: true,
      configurable: false,
    });
  }

  // 3. navigator.permissions.query — return granted instead of prompt for notifications
  try {
    const originalQuery = navigator.permissions && navigator.permissions.query;
    if (originalQuery) {
      navigator.permissions.query = (params) =>
        params && params.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : originalQuery.call(navigator.permissions, params);
    }
  } catch (e) {}

  // 4. navigator.plugins / navigator.mimeTypes — empty arrays are a tell
  try {
    Object.defineProperty(Navigator.prototype, 'plugins', {
      get: () => {
        // Provide a fake non-empty PluginArray
        const fake = [
          { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
          { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
          { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        ];
        Object.defineProperty(fake, 'length', { value: fake.length });
        return fake;
      },
      configurable: true,
    });
  } catch (e) {}

  // 5. navigator.languages — empty array is suspicious
  try {
    Object.defineProperty(Navigator.prototype, 'languages', {
      get: () => ['en-US', 'en'],
      configurable: true,
    });
  } catch (e) {}

  // 6. WebGL vendor/renderer — UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL
  try {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (parameter) {
      // UNMASKED_VENDOR_WEBGL = 37445
      if (parameter === 37445) return 'Intel Inc.';
      // UNMASKED_RENDERER_WEBGL = 37446
      if (parameter === 37446) return 'Intel Iris OpenGL Engine';
      return getParameter.call(this, parameter);
    };
  } catch (e) {}

  // 7. Permissions: Notification — must report 'default' not 'denied' in headless
  try {
    if (window.Notification && Notification.permission === 'denied') {
      Object.defineProperty(Notification, 'permission', {
        get: () => 'default',
        configurable: true,
      });
    }
  } catch (e) {}

  // 8. navigator.deviceMemory / hardwareConcurrency — undefined in headless
  try {
    if (!('deviceMemory' in navigator)) {
      Object.defineProperty(Navigator.prototype, 'deviceMemory', {
        get: () => 8,
        configurable: true,
      });
    }
    if (navigator.hardwareConcurrency === 0 || navigator.hardwareConcurrency === undefined) {
      Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {
        get: () => 8,
        configurable: true,
      });
    }
  } catch (e) {}

  // 9. navigator.connection — undefined in headless
  try {
    if (!('connection' in navigator)) {
      Object.defineProperty(Navigator.prototype, 'connection', {
        get: () => ({
          effectiveType: '4g',
          rtt: 50,
          downlink: 10,
          saveData: false,
        }),
        configurable: true,
      });
    }
  } catch (e) {}

  // 10. document.hidden / visibilityState — bots often run with these wrong
  try {
    Object.defineProperty(Document.prototype, 'hidden', {
      get: () => false,
      configurable: true,
    });
    Object.defineProperty(Document.prototype, 'visibilityState', {
      get: () => 'visible',
      configurable: true,
    });
  } catch (e) {}

  // 11. iframe.contentWindow — patch for nested-frame fingerprinting
  try {
    const elementDescriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    if (elementDescriptor && elementDescriptor.get) {
      const originalGetter = elementDescriptor.get;
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function () {
          const w = originalGetter.call(this);
          try {
            if (w && w.navigator) {
              Object.defineProperty(w.Navigator.prototype, 'webdriver', { get: () => undefined });
            }
          } catch (e) {}
          return w;
        },
      });
    }
  } catch (e) {}

  // 12. (v0.26.0) chrome.runtime.id — must be defined for many real-Chrome checks
  try {
    if (window.chrome && !window.chrome.runtime.id) {
      window.chrome.runtime.id = 'fkmilfbjfdfgflnnckdklobiaajicdbo';  // arbitrary stable id
    }
  } catch (e) {}

  // 13. (v0.26.0) AudioContext fingerprint — randomise the noise floor slightly
  try {
    const origGetChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function (...args) {
      const data = origGetChannelData.apply(this, args);
      // Tiny noise injection on every read
      for (let i = 0; i < data.length; i += 100) {
        data[i] += (Math.random() - 0.5) * 1e-7;
      }
      return data;
    };
  } catch (e) {}

  // 14. (v0.26.0) speechSynthesis.getVoices — empty list is a tell
  try {
    if (window.speechSynthesis) {
      const fakeVoices = [
        { name: 'Microsoft David Desktop', lang: 'en-US', default: true,
          localService: true, voiceURI: 'Microsoft David Desktop - English (United States)' },
        { name: 'Google US English', lang: 'en-US', default: false,
          localService: false, voiceURI: 'Google US English' },
      ];
      window.speechSynthesis.getVoices = () => fakeVoices;
    }
  } catch (e) {}

  // 15. (v0.26.0) Battery API — undefined in headless, but spec says
  // ``getBattery`` should resolve to a BatteryManager
  try {
    if (navigator.getBattery === undefined) {
      Object.defineProperty(Navigator.prototype, 'getBattery', {
        value: () => Promise.resolve({
          charging: true, chargingTime: 0, dischargingTime: Infinity,
          level: 1.0,
          addEventListener: () => {}, removeEventListener: () => {},
          dispatchEvent: () => false,
        }),
        configurable: true,
      });
    }
  } catch (e) {}

  // 16. (v0.26.0) WebGL2 vendor/renderer — same masking as WebGL1
  try {
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function (parameter) {
      if (parameter === 37445) return 'Intel Inc.';
      if (parameter === 37446) return 'Intel Iris OpenGL Engine';
      return getParameter2.call(this, parameter);
    };
  } catch (e) {}

  // 17. (v0.26.0) WebRTC IP leak protection — replace MediaDevices.enumerateDevices
  // with a generic camera+mic+speaker triple. Headless leaks empty list.
  try {
    if (navigator.mediaDevices) {
      const fake = [
        { deviceId: 'default', kind: 'audioinput', label: '', groupId: 'g1' },
        { deviceId: 'default', kind: 'audiooutput', label: '', groupId: 'g1' },
        { deviceId: 'default', kind: 'videoinput', label: '', groupId: 'g2' },
      ];
      navigator.mediaDevices.enumerateDevices = () => Promise.resolve(fake);
    }
  } catch (e) {}

  // 18. (v0.26.0) outerWidth/outerHeight — headless reports 0
  try {
    if (window.outerWidth === 0) {
      Object.defineProperty(window, 'outerWidth', {
        get: () => window.innerWidth,
        configurable: true,
      });
    }
    if (window.outerHeight === 0) {
      Object.defineProperty(window, 'outerHeight', {
        get: () => window.innerHeight + 80,  // chrome height + tabs
        configurable: true,
      });
    }
  } catch (e) {}
})();
