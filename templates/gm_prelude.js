/* ═══════════════════════════════════════════════════════════
   GM_* 兼容层（Hook2Exe 自动注入，无需油猴扩展）
   提供：GM_info / GM_xmlhttpRequest / GM_getValue / GM_setValue /
   GM_deleteValue / GM_listValues / GM_addStyle /
   GM_registerMenuCommand / GM_notification / GM_setClipboard /
   GM_log / unsafeWindow / GM.* Promise 变体
   网络请求经宿主 exe 转发，不受 CORS 限制。
   ═══════════════════════════════════════════════════════════ */

var GM_info = {
  scriptHandler: "Hook2Exe",
  scriptHandlerVersion: "1.0",
  version: "1.0",
  name: "__SCRIPT_NAME__",
  script: {
    name: "__SCRIPT_NAME__",
    version: "__SCRIPT_VERSION__",
    namespace: ""
  }
};

var __h2eBridge =
  typeof window.chrome === "object" && window.chrome &&
  window.chrome.webview &&
  typeof window.chrome.webview.postMessage === "function"
    ? window.chrome.webview
    : null;

var __h2eSeq = 0;
var __h2ePending = {};
var __h2eStyleCount = 0;
var __h2eMenuCommands = [];

if (__h2eBridge) {
  __h2eBridge.addEventListener("message", function (ev) {
    var d = ev && ev.data;
    if (!d || !d.type) return;
    if (d.type === "http-resp") {
      var p = __h2ePending[d.id];
      if (!p) return;
      delete __h2ePending[d.id];
      if (d.error) p.reject(new Error(String(d.error)));
      else p.resolve({ status: d.status, body: d.body });
    }
  });
}

/* 统一 HTTP：宿主桥（桌面版）优先，浏览器 fetch 兜底 */
function __h2eHttp(opts) {
  if (__h2eBridge) {
    return new Promise(function (resolve, reject) {
      var id = "rq-" + Date.now() + "-" + (++__h2eSeq);
      __h2ePending[id] = { resolve: resolve, reject: reject };
      __h2eBridge.postMessage({
        type: "http",
        id: id,
        method: opts.method || "GET",
        url: opts.url,
        headers: opts.headers || {},
        body: opts.body === undefined || opts.body === null ? null : String(opts.body)
      });
      setTimeout(function () {
        if (__h2ePending[id]) {
          delete __h2ePending[id];
          reject(new Error("宿主请求超时"));
        }
      }, opts.timeout || 60000);
    });
  }
  return fetch(opts.url, {
    method: opts.method || "GET",
    headers: opts.headers || {},
    body: opts.body || null
  }).then(function (r) {
    return r.text().then(function (txt) {
      return { status: r.status, body: txt };
    });
  });
}

/* ── 存储（localStorage 持久化） ── */

var __GM_PREFIX = "__h2e_gm_";

function GM_getValue(key, defaultValue) {
  try {
    var raw = localStorage.getItem(__GM_PREFIX + key);
    if (raw === null) return defaultValue;
    try { return JSON.parse(raw); } catch (e) { return raw; }
  } catch (e) {
    return defaultValue;
  }
}

function GM_setValue(key, value) {
  try { localStorage.setItem(__GM_PREFIX + key, JSON.stringify(value)); } catch (e) {}
}

function GM_deleteValue(key) {
  try { localStorage.removeItem(__GM_PREFIX + key); } catch (e) {}
}

function GM_listValues() {
  var out = [];
  try {
    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (k && k.indexOf(__GM_PREFIX) === 0) out.push(k.slice(__GM_PREFIX.length));
    }
  } catch (e) {}
  return out;
}

/* ── 样式 / 菜单 / 通知 / 剪贴板 / 日志 ── */

function GM_addStyle(css) {
  try {
    var style = document.createElement("style");
    style.setAttribute("data-gm-style", String(++__h2eStyleCount));
    style.textContent = css;
    (document.head || document.documentElement).appendChild(style);
    return style;
  } catch (e) {
    return null;
  }
}

function GM_registerMenuCommand(name, fn) {
  // 桌面版没有油猴菜单 UI，仅登记（保持脚本兼容不报错）
  __h2eMenuCommands.push({ name: String(name || ""), fn: fn });
  return __h2eMenuCommands.length;
}

function GM_notification(details, ondone) {
  try {
    var title = typeof details === "string" ? details : (details && details.title) || "";
    var text = typeof details === "string" ? "" : (details && details.text) || "";
    if (__h2eBridge) {
      __h2eBridge.postMessage({ type: "notify", title: String(title), text: String(text) });
    } else {
      console.log("[GM_notification]", title, text);
    }
  } catch (e) {}
  if (typeof ondone === "function") {
    try { ondone(); } catch (e) {}
  }
}

function GM_setClipboard(text) {
  try {
    text = text === undefined || text === null ? "" : String(text);
    if (__h2eBridge) {
      __h2eBridge.postMessage({ type: "clipboard", text: text });
    }
  } catch (e) {}
}

function GM_log() {
  try {
    var args = Array.prototype.slice.call(arguments);
    console.log.apply(console, ["[GM]"].concat(args));
  } catch (e) {}
}

/* ── 网络 ── */

function GM_xmlhttpRequest(details) {
  if (!details || !details.url) {
    if (details && typeof details.onerror === "function") {
      details.onerror({ error: "missing url" });
    }
    return;
  }
  __h2eHttp({
    method: (details.method || "GET").toUpperCase(),
    url: details.url,
    headers: details.headers || {},
    body: details.data === undefined || details.data === null
      ? null
      : (typeof details.data === "string" ? details.data : String(details.data)),
    timeout: details.timeout || 60000
  }).then(function (r) {
    if (typeof details.onload === "function") {
      details.onload({
        status: r.status,
        responseText: r.body,
        response: r.body,
        finalUrl: details.url
      });
    }
  }).catch(function (e) {
    if (typeof details.onerror === "function") {
      details.onerror({ error: e && e.message });
    } else {
      console.warn("[GM_xmlhttpRequest] failed:", e && e.message);
    }
    if (typeof details.ontimeout === "function" && e && /超时|timeout/i.test(e.message)) {
      details.ontimeout({});
    }
  });
}

/* ── unsafeWindow 与 GM.* Promise 变体 ── */

var unsafeWindow = window;

var GM = {
  info: GM_info,
  getValue: function (key, def) { return Promise.resolve(GM_getValue(key, def)); },
  setValue: function (key, val) { GM_setValue(key, val); return Promise.resolve(); },
  deleteValue: function (key) { GM_deleteValue(key); return Promise.resolve(); },
  listValues: function () { return Promise.resolve(GM_listValues()); },
  xmlHttpRequest: GM_xmlhttpRequest,
  notification: GM_notification,
  addStyle: GM_addStyle,
  setClipboard: GM_setClipboard,
  log: GM_log
};
