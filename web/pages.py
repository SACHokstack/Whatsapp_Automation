"""Inline HTML/CSS/JS for the browser-facing pages served by main.py.

Pure presentation strings — no application logic — kept out of main.py so the
route handlers stay readable. Names are re-exported unchanged, so main.py refers
to them exactly as before.
"""

_RAG_TEST_CHAT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>RAG v2 Test Chat</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #0f172a;
      --panel: #111827;
      --panel-2: #1f2937;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #38bdf8;
      --user: #2563eb;
      --bot: #374151;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body {
      margin: 0;
      background: radial-gradient(circle at top, #1e3a8a 0, var(--bg) 42%);
      color: var(--text);
      min-height: 100vh;
      display: grid;
      place-items: center;
    }
    .shell {
      width: min(980px, calc(100vw - 24px));
      height: min(820px, calc(100vh - 24px));
      background: rgba(17, 24, 39, 0.94);
      border: 1px solid rgba(148, 163, 184, 0.22);
      border-radius: 22px;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.42);
      display: grid;
      grid-template-rows: auto 1fr auto;
      overflow: hidden;
    }
    header {
      padding: 18px 22px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.18);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      letter-spacing: 0.01em;
    }
    .sub {
      color: var(--muted);
      font-size: 13px;
      margin-top: 4px;
    }
    button {
      border: 0;
      border-radius: 12px;
      background: var(--accent);
      color: #082f49;
      font-weight: 700;
      padding: 10px 14px;
      cursor: pointer;
    }
    button.secondary {
      background: #334155;
      color: var(--text);
    }
    button:disabled {
      opacity: 0.55;
      cursor: wait;
    }
    #messages {
      padding: 22px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .message {
      max-width: 78%;
      padding: 12px 14px;
      border-radius: 16px;
      white-space: pre-wrap;
      line-height: 1.42;
      font-size: 15px;
    }
    .user {
      align-self: flex-end;
      background: var(--user);
      color: white;
      border-bottom-right-radius: 4px;
    }
    .assistant {
      align-self: flex-start;
      background: var(--bot);
      border-bottom-left-radius: 4px;
    }
    .system {
      align-self: center;
      color: var(--muted);
      font-size: 13px;
      max-width: 82%;
      text-align: center;
    }
    form {
      border-top: 1px solid rgba(148, 163, 184, 0.18);
      padding: 16px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      background: rgba(15, 23, 42, 0.66);
    }
    textarea {
      resize: none;
      min-height: 48px;
      max-height: 150px;
      border: 1px solid rgba(148, 163, 184, 0.25);
      border-radius: 14px;
      padding: 12px 14px;
      background: var(--panel-2);
      color: var(--text);
      font: inherit;
      outline: none;
    }
    textarea:focus {
      border-color: var(--accent);
    }
    .examples {
      margin-top: 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .chip {
      font-size: 12px;
      padding: 7px 10px;
      border-radius: 999px;
      background: rgba(56, 189, 248, 0.12);
      color: #bae6fd;
      cursor: pointer;
    }
    @media (max-width: 640px) {
      .shell { height: 100vh; width: 100vw; border-radius: 0; }
      header { align-items: flex-start; flex-direction: column; }
      .message { max-width: 92%; }
      form { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <h1>RAG v2 Test Chat</h1>
        <div class="sub">Course-agnostic testing interface. Memory persists in this browser session until reset.</div>
        <div class="examples">
          <span class="chip">What software testing courses are available?</span>
          <span class="chip">What is covered in the testing course?</span>
          <span class="chip">Is it HRDC claimable?</span>
          <span class="chip">What are the fees and dates?</span>
        </div>
      </div>
      <button id="reset" class="secondary" type="button">Reset memory</button>
    </header>
    <section id="messages"></section>
    <form id="chat-form">
      <textarea id="message" placeholder="Ask anything about Timmins courses..." autocomplete="off"></textarea>
      <button id="send" type="submit">Send</button>
    </form>
  </main>
  <script>
    const sessionKey = "timmins-rag-test-session";
    let sessionId = localStorage.getItem(sessionKey) || "";
    const messages = document.getElementById("messages");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("message");
    const send = document.getElementById("send");
    const reset = document.getElementById("reset");

    function addMessage(role, text) {
      const el = document.createElement("div");
      el.className = role === "user" ? "message user" : role === "assistant" ? "message assistant" : "system";
      el.textContent = text;
      messages.appendChild(el);
      messages.scrollTop = messages.scrollHeight;
    }

    function renderHistory(history) {
      messages.innerHTML = "";
      if (!history.length) {
        addMessage("system", "Ask a question to test the course-agnostic RAG. Your chat memory is stored server-side and survives refreshes.");
        return;
      }
      for (const item of history) addMessage(item.role, item.body);
    }

    async function loadHistory() {
      if (!sessionId) {
        renderHistory([]);
        return;
      }
      const res = await fetch(`/rag-test/history/${encodeURIComponent(sessionId)}`);
      if (!res.ok) {
        renderHistory([]);
        return;
      }
      const data = await res.json();
      renderHistory(data.history || []);
    }

    async function sendMessage(text) {
      addMessage("user", text);
      send.disabled = true;
      input.disabled = true;
      try {
        const res = await fetch("/rag-test/message", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({session_id: sessionId, message: text})
        });
        const raw = await res.text();
        let data = {};
        try {
          data = raw ? JSON.parse(raw) : {};
        } catch (_) {
          throw new Error(res.ok ? "The server returned an invalid response." : `Server error (${res.status}). Please check the backend logs.`);
        }
        if (!res.ok) throw new Error((data.detail || "Request failed") + (data.error_id ? ` Error ID: ${data.error_id}` : ""));
        sessionId = data.session_id;
        localStorage.setItem(sessionKey, sessionId);
        addMessage("assistant", data.reply);
      } catch (error) {
        addMessage("system", `Error: ${error.message}`);
      } finally {
        send.disabled = false;
        input.disabled = false;
        input.focus();
      }
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      sendMessage(text);
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    reset.addEventListener("click", async () => {
      reset.disabled = true;
      try {
        await fetch("/rag-test/reset", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({session_id: sessionId})
        });
      } finally {
        localStorage.removeItem(sessionKey);
        sessionId = "";
        reset.disabled = false;
        renderHistory([]);
        input.focus();
      }
    });

    document.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        input.value = chip.textContent;
        input.focus();
      });
    });

    loadHistory();
  </script>
</body>
</html>"""


_WA_SIMULATOR_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Timmins Training – AI WhatsApp Assistant Demo</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#111b21;height:100vh;display:flex;flex-direction:column;overflow:hidden}
  /* -- Top bar -- */
  #topbar{background:#202c33;display:flex;align-items:center;padding:10px 16px;gap:12px;border-bottom:1px solid #2a3942;min-height:60px;flex-shrink:0}
  #avatar{width:40px;height:40px;border-radius:50%;background:#00a884;display:flex;align-items:center;justify-content:center;color:#fff;font-size:18px;font-weight:700;flex-shrink:0}
  #contact-info{flex:1;min-width:0}
  #contact-name{color:#e9edef;font-size:15px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #contact-status{color:#8696a0;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #topbar-actions{display:flex;gap:8px;align-items:center}
  .btn-icon{background:none;border:none;color:#aebac1;cursor:pointer;padding:6px;border-radius:50%;transition:background .15s;font-size:13px}
  .btn-icon:hover{background:#2a3942;color:#e9edef}
  /* -- Layout -- */
  #main{display:flex;flex:1;overflow:hidden}
  /* -- Sidebar -- */
  #sidebar{width:280px;background:#111b21;border-right:1px solid #2a3942;display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
  #sidebar-header{background:#202c33;padding:14px 16px;border-bottom:1px solid #2a3942;flex-shrink:0}
  #sidebar-brand{display:flex;align-items:center;gap:10px;margin-bottom:14px}
  #sidebar-brand-icon{width:36px;height:36px;border-radius:8px;background:#00a884;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:16px;flex-shrink:0}
  #sidebar-brand-text{color:#e9edef;font-size:13px;font-weight:600;line-height:1.3}
  #sidebar-brand-sub{color:#8696a0;font-size:11px;margin-top:2px}
  .field-group{margin-bottom:10px}
  .field-label{color:#8696a0;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
  .field-value{color:#e9edef;font-size:13px;padding:6px 10px;background:#2a3942;border-radius:6px;word-break:break-word;min-height:28px}
  select.field-select{width:100%;color:#e9edef;font-size:13px;padding:6px 10px;background:#2a3942;border:none;border-radius:6px;outline:none;cursor:pointer;appearance:none;-webkit-appearance:none}
  select.field-select option{background:#2a3942}
  input.field-input{width:100%;color:#e9edef;font-size:13px;padding:6px 10px;background:#2a3942;border:none;border-radius:6px;outline:none}
  input.field-input::placeholder{color:#8696a0}
  #sidebar-body{padding:12px 16px;flex:1;overflow-y:auto}
  #btn-reset{width:100%;background:#2a3942;color:#8696a0;border:1px solid #3b4a54;padding:8px;border-radius:8px;font-size:12px;cursor:pointer;margin-top:4px;transition:all .15s}
  #btn-reset:hover{background:#d9363e;color:#fff;border-color:#d9363e}
  #btn-new-lead{width:100%;background:#00a884;color:#fff;border:none;padding:9px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;margin-top:8px;transition:background .15s}
  #btn-new-lead:hover{background:#008f6f}
  /* Demo mode toggle */
  #demo-toggle-row{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;border-top:1px solid #2a3942;flex-shrink:0;background:#111b21}
  #demo-toggle-label{color:#8696a0;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  .toggle{position:relative;display:inline-block;width:36px;height:20px}
  .toggle input{opacity:0;width:0;height:0}
  .toggle-slider{position:absolute;cursor:pointer;inset:0;background:#2a3942;border-radius:20px;transition:.2s}
  .toggle-slider:before{position:absolute;content:"";height:14px;width:14px;left:3px;bottom:3px;background:#8696a0;border-radius:50%;transition:.2s}
  input:checked+.toggle-slider{background:#00a884}
  input:checked+.toggle-slider:before{transform:translateX(16px);background:#fff}
  /* Qualified fields (hidden in demo mode) */
  .dev-only{transition:opacity .2s}
  body.demo-mode .dev-only{display:none}
  /* Handoff alert */
  #handoff-banner{display:none;margin:8px 16px;background:#4a001a;border:1px solid #f5185e;color:#f5185e;border-radius:8px;padding:10px 12px;font-size:13px;font-weight:600;text-align:center}
  #handoff-banner.visible{display:block}
  /* Score pill */
  .score-pill{display:inline-block;background:#003b5e;color:#5bc8f5;border-radius:12px;padding:2px 10px;font-size:12px;font-weight:700}
  /* Quick chips */
  #chips{padding:8px 16px;display:flex;flex-wrap:wrap;gap:6px;flex-shrink:0;border-top:1px solid #2a3942}
  .chip{background:#2a3942;color:#8696a0;border:none;padding:5px 10px;border-radius:12px;font-size:12px;cursor:pointer;transition:background .15s,color .15s}
  .chip:hover{background:#3b4a54;color:#e9edef}
  /* -- Chat area -- */
  #chat-area{flex:1;display:flex;flex-direction:column;background:#0b141a;overflow:hidden;position:relative}
  #chat-bg{position:absolute;inset:0;background-image:url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23182229' fill-opacity='0.4'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");z-index:0}
  #messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:4px;position:relative;z-index:1}
  .msg{display:flex;max-width:72%;animation:fadein .2s ease}
  @keyframes fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
  .msg.inbound{justify-content:flex-start}
  .msg.outbound{justify-content:flex-end;align-self:flex-end}
  .bubble{padding:8px 12px;border-radius:8px;font-size:14px;line-height:1.5;word-break:break-word;position:relative;max-width:100%}
  .msg.inbound .bubble{background:#202c33;color:#e9edef;border-top-left-radius:0}
  .msg.outbound .bubble{background:#005c4b;color:#e9edef;border-top-right-radius:0}
  .bubble .time{font-size:11px;color:#8696a0;margin-top:4px;text-align:right;white-space:nowrap}
  .system-msg{text-align:center;color:#8696a0;font-size:12px;padding:4px 12px;background:rgba(11,20,26,.7);border-radius:8px;align-self:center;margin:8px 0}
  /* -- Input -- */
  #input-area{background:#202c33;padding:10px 16px;display:flex;gap:10px;align-items:flex-end;position:relative;z-index:1;flex-shrink:0}
  #msg-input{flex:1;background:#2a3942;color:#e9edef;border:none;border-radius:10px;padding:10px 14px;font-size:14px;resize:none;outline:none;min-height:44px;max-height:120px;font-family:inherit;line-height:1.4}
  #msg-input::placeholder{color:#8696a0}
  #btn-send{background:#00a884;color:#fff;border:none;border-radius:50%;width:44px;height:44px;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;transition:background .15s}
  #btn-send:hover{background:#008f6f}
  #btn-send:disabled{background:#2a3942;cursor:default}
  #btn-send svg{width:20px;height:20px}
  /* Typing indicator */
  #typing{display:none;padding:0 16px 8px;color:#8696a0;font-size:13px;position:relative;z-index:1}
  #typing.visible{display:block}
  /* Status badge */
  .badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.3px}
  .badge-green{background:#00543b;color:#00e5a0}
  .badge-yellow{background:#4a3800;color:#f5c518}
  .badge-red{background:#4a001a;color:#f5185e}
  .badge-gray{background:#2a3942;color:#8696a0}
  .badge-blue{background:#003b5e;color:#5bc8f5}
  ::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#2a3942;border-radius:3px}
</style>
</head>
<body>
<div id="topbar">
  <div id="avatar">T</div>
  <div id="contact-info">
    <div id="contact-name">Timmins Training Consulting</div>
    <div id="contact-status">AI WhatsApp Assistant · Live Demo</div>
  </div>
  <div id="topbar-actions">
    <span id="lead-badge" class="badge badge-gray">NEW</span>
  </div>
</div>
<div id="main">
  <div id="sidebar">
    <div id="sidebar-header">
      <div id="sidebar-brand">
        <div id="sidebar-brand-icon">T</div>
        <div>
          <div id="sidebar-brand-text">Timmins Training</div>
          <div id="sidebar-brand-sub">AI Bot Demo Console</div>
        </div>
      </div>
      <div class="field-group">
        <div class="field-label">Your Name</div>
        <input class="field-input" id="input-name" placeholder="e.g. Ahmad Faris" value="Demo Lead"/>
      </div>
      <div class="field-group">
        <div class="field-label">Course Context</div>
        <select class="field-select" id="course-select">
          <option value="">None (General Enquiry)</option>
          <option value="sw-testing-aug-2026">Software Testing – Aug 2026</option>
          <option value="embedded-c-july-2026">Embedded C – July 2026</option>
          <option value="embedded-linux-internals-aug-2026">Embedded Linux Internals – Aug 2026</option>
          <option value="embedded-linux-yocto-aug-2026">Embedded Linux with Yocto – Aug 2026</option>
          <option value="linux-kernel-aug-2026">Linux Kernel Development – Aug 2026</option>
        </select>
      </div>
      <button id="btn-new-lead">＋ Start New Conversation</button>
      <button id="btn-reset">↺ Reset Conversation</button>
    </div>
    <div id="handoff-banner">🚨 Handoff Requested — Consultant Notified</div>
    <div id="sidebar-body">
      <div class="field-group">
        <div class="field-label">Lead Status</div>
        <div class="field-value" id="info-status">–</div>
      </div>
      <div class="field-group">
        <div class="field-label">Course Interest</div>
        <div class="field-value" id="info-course">–</div>
      </div>
      <div class="field-group">
        <div class="field-label">Lead Score</div>
        <div class="field-value" id="info-score">–</div>
      </div>
      <div class="field-group dev-only">
        <div class="field-label">Session Phone</div>
        <div class="field-value" id="info-phone">–</div>
      </div>
      <div class="field-group dev-only">
        <div class="field-label">Conversation State</div>
        <div class="field-value" id="info-state">–</div>
      </div>
      <div class="field-group dev-only">
        <div class="field-label">Experience (yrs)</div>
        <div class="field-value" id="info-exp">–</div>
      </div>
      <div class="field-group dev-only">
        <div class="field-label">Technologies</div>
        <div class="field-value" id="info-tech">–</div>
      </div>
      <div class="field-group dev-only">
        <div class="field-label">Funding</div>
        <div class="field-value" id="info-funding">–</div>
      </div>
      <div class="field-group dev-only">
        <div class="field-label">Motivation</div>
        <div class="field-value" id="info-motivation">–</div>
      </div>
      <div class="field-group dev-only">
        <div class="field-label">Goals</div>
        <div class="field-value" id="info-goals">–</div>
      </div>
      <div class="field-group dev-only">
        <div class="field-label">Availability</div>
        <div class="field-value" id="info-avail">–</div>
      </div>
    </div>
    <div id="demo-toggle-row">
      <span id="demo-toggle-label">Demo Mode</span>
      <label class="toggle">
        <input type="checkbox" id="demo-toggle" checked/>
        <span class="toggle-slider"></span>
      </label>
    </div>
  </div>
  <div id="chat-area">
    <div id="chat-bg"></div>
    <div id="messages"></div>
    <div id="typing">Bot is typing…</div>
    <div id="chips">
      <button class="chip">👋 Hi there!</button>
      <button class="chip">💰 What are the fees?</button>
      <button class="chip">📅 What are the course dates?</button>
      <button class="chip">✅ Is it HRDC claimable?</button>
      <button class="chip">🙋 I'm interested</button>
      <button class="chip">📚 What is covered in the course?</button>
      <button class="chip">🧑‍🏫 Who is the trainer?</button>
      <button class="chip">📄 Can I get a quotation?</button>
      <button class="chip">🤝 Talk to a consultant</button>
    </div>
    <div id="input-area">
      <textarea id="msg-input" placeholder="Type a message…" rows="1"></textarea>
      <button id="btn-send" title="Send">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M1.101 21.757 23.8 12.028 1.101 2.3l.011 7.912 13.623 1.816-13.623 1.817-.011 7.912z"/></svg>
      </button>
    </div>
  </div>
</div>
<script>
(function(){
  const SIM_KEY = "timmins-sim-session-v2";
  let sessionId = localStorage.getItem(SIM_KEY) || "";
  const msgs = document.getElementById("messages");
  const typing = document.getElementById("typing");
  const input = document.getElementById("msg-input");
  const sendBtn = document.getElementById("btn-send");
  const courseSelect = document.getElementById("course-select");
  const nameInput = document.getElementById("input-name");
  const demoToggle = document.getElementById("demo-toggle");
  const handoffBanner = document.getElementById("handoff-banner");

  // Demo mode toggle
  if(localStorage.getItem("timmins-demo-mode") === "off"){
    demoToggle.checked = false;
  } else {
    document.body.classList.add("demo-mode");
  }
  demoToggle.addEventListener("change", () => {
    if(demoToggle.checked){
      document.body.classList.add("demo-mode");
      localStorage.setItem("timmins-demo-mode", "on");
    } else {
      document.body.classList.remove("demo-mode");
      localStorage.setItem("timmins-demo-mode", "off");
    }
  });

  function now(){
    const d=new Date();
    return d.getHours().toString().padStart(2,"0")+":"+d.getMinutes().toString().padStart(2,"0");
  }

  function addBubble(direction, text){
    const row = document.createElement("div");
    row.className = "msg " + direction;
    const bub = document.createElement("div");
    bub.className = "bubble";
    text.split("\\n").forEach((line, i) => {
      if(i>0) bub.appendChild(document.createElement("br"));
      bub.appendChild(document.createTextNode(line));
    });
    const time = document.createElement("div");
    time.className = "time";
    time.textContent = now();
    bub.appendChild(time);
    row.appendChild(bub);
    msgs.appendChild(row);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function addSystem(text){
    const el = document.createElement("div");
    el.className = "system-msg";
    el.textContent = text;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function statusBadge(status){
    const s = (status||"").toUpperCase();
    if(s==="HOT") return "badge-red";
    if(s==="ENGAGED"||s==="QUALIFIED"||s==="REGISTERED") return "badge-green";
    if(s.startsWith("ASKING_")) return "badge-yellow";
    if(s==="BOT_PAUSED"||s==="NOT_INTERESTED") return "badge-red";
    if(s==="CONTACTED") return "badge-blue";
    return "badge-gray";
  }

  function updateLeadPanel(lead){
    if(!lead) return;
    const badge = document.getElementById("lead-badge");
    const st = (lead.status||"NEW").toUpperCase();
    badge.textContent = st;
    badge.className = "badge " + statusBadge(st);
    document.getElementById("info-phone").textContent = lead.phone || sessionId.slice(0,12)+"…";
    document.getElementById("info-status").textContent = lead.status || "–";
    document.getElementById("info-state").textContent = lead.conversation_state || lead.qualification_step || "–";
    document.getElementById("info-course").textContent = lead.course || "–";
    document.getElementById("info-exp").textContent = lead.experience_years || "–";
    document.getElementById("info-tech").textContent = lead.technologies || "–";
    document.getElementById("info-funding").textContent = lead.funding_path || "–";
    document.getElementById("info-motivation").textContent = lead.motivation || "–";
    document.getElementById("info-goals").textContent = lead.learning_goals || "–";
    document.getElementById("info-avail").textContent = lead.availability || "–";
    const score = lead.lead_score != null && lead.lead_score !== "" ? lead.lead_score : "–";
    document.getElementById("info-score").textContent = score;
    // Show handoff banner if needs_human
    if((lead.needs_human||"").toUpperCase() === "YES"){
      handoffBanner.classList.add("visible");
    }
    const name = (nameInput.value||"").trim();
    document.getElementById("contact-status").textContent = name ? "Chatting as: "+name : "AI WhatsApp Assistant · Live Demo";
  }

  async function loadSession(){
    if(!sessionId){ msgs.innerHTML=""; addSystem("👋 Welcome! Select a course above and start chatting to try the Timmins AI assistant."); return; }
    try{
      const r = await fetch("/simulate/session/"+encodeURIComponent(sessionId));
      if(!r.ok){sessionId="";localStorage.removeItem(SIM_KEY);loadSession();return;}
      const d = await r.json();
      renderHistory(d.history||[]);
      updateLeadPanel(d.lead);
      if(d.course) courseSelect.value = d.course;
    }catch(e){addSystem("Could not load session.");}
  }

  function renderHistory(history){
    msgs.innerHTML = "";
    if(!history.length){ addSystem("👋 Welcome! Select a course above and start chatting to try the Timmins AI assistant."); return; }
    history.forEach(h => {
      const dir = h.direction==="inbound" ? "outbound" : "inbound";
      addBubble(dir, h.body);
    });
  }

  async function send(text){
    if(!text.trim()) return;
    addBubble("outbound", text);
    input.value=""; input.style.height="";
    sendBtn.disabled=true; typing.classList.add("visible");
    try{
      const r = await fetch("/simulate/message", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          session_id: sessionId,
          message: text,
          course: courseSelect.value||null,
          name: nameInput.value.trim()||"Demo Lead"
        })
      });
      const raw = await r.text();
      let d = {};
      try{ d = raw ? JSON.parse(raw) : {}; }
      catch(_){ throw new Error(r.ok ? "Invalid response from server." : `Server error (${r.status}).`); }
      if(!r.ok) throw new Error((d.detail||"error") + (d.error_id ? ` [${d.error_id}]` : ""));
      sessionId = d.session_id;
      localStorage.setItem(SIM_KEY, sessionId);
      addBubble("inbound", d.reply);
      updateLeadPanel(d.lead);
    }catch(e){
      addBubble("inbound","⚠️ "+e.message);
    }finally{
      sendBtn.disabled=false; typing.classList.remove("visible");
      input.focus();
    }
  }

  document.getElementById("btn-reset").addEventListener("click", async()=>{
    if(!confirm("Reset this conversation?")) return;
    await fetch("/simulate/reset",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_id:sessionId})});
    msgs.innerHTML=""; addSystem("Conversation reset.");
    handoffBanner.classList.remove("visible");
    document.querySelectorAll("[id^=info-]").forEach(el=>el.textContent="–");
    document.getElementById("lead-badge").textContent="NEW";
    document.getElementById("lead-badge").className="badge badge-gray";
  });

  document.getElementById("btn-new-lead").addEventListener("click",()=>{
    localStorage.removeItem(SIM_KEY);
    sessionId="";
    msgs.innerHTML="";
    handoffBanner.classList.remove("visible");
    addSystem("New conversation started. Send a message to begin.");
    document.querySelectorAll("[id^=info-]").forEach(el=>el.textContent="–");
    document.getElementById("lead-badge").textContent="NEW";
    document.getElementById("lead-badge").className="badge badge-gray";
  });

  document.querySelectorAll(".chip").forEach(c=>{
    c.addEventListener("click",()=>send(c.textContent.replace(/^[\\u{1F000}-\\u{1FFFF}]|^[\\u2600-\\u27FF]\\s*/u,"").trim()));
  });

  document.getElementById("btn-send").addEventListener("click",()=>send(input.value));
  input.addEventListener("keydown",e=>{
    if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send(input.value);}
  });
  input.addEventListener("input",()=>{
    input.style.height="auto";
    input.style.height=Math.min(input.scrollHeight,120)+"px";
  });

  loadSession();
})();
</script>
</body>
</html>"""


_ADMIN_LOGIN_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Timmins Admin</title><style>
:root{color-scheme:light;--ink:#172033;--muted:#65738a;--line:#d9e2ee;--soft:#f6f8fb;
--panel:#ffffff;--brand:#0f766e;--brand-dark:#115e59;--danger:#b42318}
*{box-sizing:border-box}body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
background:linear-gradient(135deg,#effaf8 0%,#f7f1e8 48%,#eef4ff 100%);color:var(--ink);display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0;padding:1.2rem}
form{background:rgba(255,255,255,.92);padding:2rem;border-radius:8px;width:min(92vw,380px);
box-shadow:0 24px 60px rgba(23,32,51,.18);border:1px solid rgba(255,255,255,.8)}
.mark{width:42px;height:42px;border-radius:8px;background:#0f766e;color:#fff;display:grid;place-items:center;
font-weight:800;margin-bottom:1rem}h1{font-size:1.45rem;margin:0 0 .25rem;letter-spacing:0}
p{margin:0 0 1.4rem;color:var(--muted);font-size:.93rem;line-height:1.45}label{display:block;color:#344256;
font-size:.86rem;font-weight:700;margin-bottom:.35rem}input{width:100%;padding:.78rem .85rem;margin:0 0 1rem;border-radius:8px;
border:1px solid var(--line);background:#fff;color:var(--ink);box-shadow:0 1px 2px rgba(23,32,51,.04)}
input:focus{outline:3px solid rgba(15,118,110,.18);border-color:var(--brand)}button{width:100%;padding:.78rem;border:0;
border-radius:8px;background:var(--brand);color:#fff;font-weight:800;cursor:pointer;box-shadow:0 10px 22px rgba(15,118,110,.24)}
button:hover{background:var(--brand-dark)}.err{color:var(--danger);font-size:.88rem;margin-bottom:.75rem;min-height:1rem;
font-weight:700}</style></head>
<body><form method="post" action="/admin/login">
<div class="mark">TA</div><h1>Welcome back</h1><p>Sign in to manage leads, courses and bot knowledge.</p>
<div class="err">__ERROR__</div><label>Password</label><input type="password" name="password" autofocus required>
<button type="submit">Sign in</button></form></body></html>"""


_ADMIN_SHELL = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Timmins Admin · __TITLE__</title><style>
:root{color-scheme:light;--bg:#f5f7fb;--panel:#ffffff;--panel-soft:#f8fafc;--ink:#172033;
--muted:#64748b;--faint:#8a98ab;--line:#dbe4ef;--line-soft:#edf1f6;--brand:#0f766e;
--brand-dark:#115e59;--brand-soft:#d9f3ee;--blue:#2563eb;--blue-soft:#dbeafe;--amber:#a16207;
--amber-soft:#fef3c7;--danger:#b42318;--danger-soft:#fee4e2;--success:#047857;--success-soft:#d1fae5}
*{box-sizing:border-box}
body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
background:var(--bg);color:var(--ink);margin:0;line-height:1.45}
header{background:rgba(255,255,255,.94);border-bottom:1px solid var(--line);padding:.85rem 1.2rem;
display:flex;align-items:center;gap:1.1rem;flex-wrap:wrap;position:sticky;top:0;z-index:5;backdrop-filter:blur(12px)}
header .brand{font-weight:850;color:var(--ink);display:flex;align-items:center;gap:.6rem;letter-spacing:0}
header .brand:before{content:'TA';width:34px;height:34px;border-radius:8px;background:var(--brand);color:#fff;
display:grid;place-items:center;font-size:.8rem;font-weight:900}
nav{display:flex;gap:.35rem;flex-wrap:wrap;background:#eef3f8;border:1px solid var(--line);border-radius:8px;padding:.2rem}
nav a{color:#475569;text-decoration:none;padding:.45rem .75rem;border-radius:7px;font-size:.9rem;font-weight:700}
nav a:hover{background:#fff;color:var(--ink)}
nav a.on{background:var(--brand);color:#fff;box-shadow:0 8px 18px rgba(15,118,110,.18)}
header .sp{flex:1}
header form{margin:0}header button{background:#fff;border:1px solid var(--line);color:#475569;padding:.45rem .75rem;
border-radius:8px;cursor:pointer;font-size:.86rem;font-weight:700}header button:hover{border-color:#bac7d6;color:var(--ink)}
main{padding:1.6rem;max-width:1180px;margin:0 auto;width:100%}.page-head{margin:.35rem 0 1.15rem;
display:flex;justify-content:space-between;gap:1rem;align-items:flex-end;flex-wrap:wrap}.eyebrow{font-size:.75rem;
text-transform:uppercase;letter-spacing:.08em;color:var(--brand);font-weight:850;margin:0 0 .25rem}
h1{font-size:1.75rem;margin:0;color:#111827;letter-spacing:0}h2{font-size:1.05rem;margin:1.5rem 0 .65rem;color:#273449}
.sub{color:var(--muted);margin:.25rem 0 0;max-width:680px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.9rem}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:1rem;box-shadow:0 10px 28px rgba(23,32,51,.06)}
.card .n{font-size:2rem;font-weight:850;color:#111827;line-height:1.1}.card .l{color:var(--muted);font-size:.8rem;
text-transform:uppercase;letter-spacing:.05em;font-weight:800;margin-top:.45rem}.table-wrap{background:var(--panel);border:1px solid var(--line);
border-radius:8px;overflow:auto;box-shadow:0 10px 28px rgba(23,32,51,.05)}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th,td{text-align:left;padding:.72rem .8rem;border-bottom:1px solid var(--line-soft);vertical-align:top}
th{color:var(--muted);font-weight:850;font-size:.74rem;text-transform:uppercase;letter-spacing:.06em;background:#f8fafc}
tbody tr:hover{background:#f2f7fb;cursor:pointer}.pill{display:inline-flex;align-items:center;padding:.18rem .55rem;border-radius:999px;
font-size:.74rem;background:#eef2f7;color:#475569;font-weight:800}.pill.active{background:var(--success-soft);color:var(--success)}
.pill.archived{background:var(--danger-soft);color:var(--danger)}.muted{color:var(--muted)}select,input{background:#fff;color:var(--ink);
border:1px solid var(--line);border-radius:8px;padding:.55rem .7rem;font-size:.88rem;box-shadow:0 1px 2px rgba(23,32,51,.03)}
select:focus,input:focus,textarea:focus{outline:3px solid rgba(15,118,110,.16);border-color:var(--brand)}.row{display:flex;gap:.7rem;
flex-wrap:wrap;align-items:center;margin-bottom:1rem}.row .muted{font-weight:700}pre{white-space:pre-wrap;word-break:break-word;
background:#f8fafc;border:1px solid var(--line);border-radius:8px;padding:.9rem;font-size:.82rem;line-height:1.5;margin:0}
.drawer{position:fixed;top:0;right:0;height:100vh;width:min(94vw,520px);background:#fff;
border-left:1px solid var(--line);box-shadow:-18px 0 50px rgba(23,32,51,.16);transform:translateX(100%);
transition:transform .18s;z-index:20;display:flex;flex-direction:column}
.drawer.open{transform:none}
.drawer .dh{padding:1rem 1.2rem;border-bottom:1px solid var(--line);display:flex;
justify-content:space-between;align-items:center}
.drawer .db{padding:1rem 1.2rem;overflow-y:auto;flex:1}
.drawer .x{cursor:pointer;color:var(--muted);background:#f1f5f9;border:1px solid var(--line);font-size:1.15rem;
width:34px;height:34px;border-radius:8px}
.msg{margin:.5rem 0;padding:.55rem .7rem;border-radius:10px;max-width:85%;font-size:.85rem;line-height:1.4}
.msg.inbound{background:#f1f5f9;margin-right:auto}.msg.outbound{background:#dff3ee;margin-left:auto}.msg .d{font-size:.68rem;
color:var(--muted);margin-bottom:.15rem}.empty{color:var(--muted);padding:2rem;text-align:center;background:#f8fafc;
border:1px dashed var(--line);border-radius:8px}.note{background:#f8fafc;border:1px solid var(--line);border-radius:8px;padding:.8rem 1rem;
color:#526174;font-size:.86rem;margin-bottom:1rem}a.link{color:var(--blue)}textarea{background:#fff;color:var(--ink);border:1px solid var(--line);border-radius:8px;
padding:.6rem;font-size:.85rem;width:100%;font-family:ui-monospace,monospace;line-height:1.5}
label.f{display:block;margin:.8rem 0 .25rem;color:#475569;font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;font-weight:850}
label.f input,label.f textarea,label.f select{width:100%}
.btn{background:var(--brand);color:#fff;border:0;border-radius:8px;padding:.58rem .95rem;
cursor:pointer;font-size:.87rem;font-weight:850;box-shadow:0 8px 18px rgba(15,118,110,.18)}
.btn:hover{background:var(--brand-dark)}.btn.ghost{background:#fff;border:1px solid var(--line);color:#475569;font-weight:800;box-shadow:none}
.btn.ghost:hover{background:#f8fafc;color:#172033}.btn.danger{background:var(--danger);color:#fff;box-shadow:0 8px 18px rgba(180,35,24,.16)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btns{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:1rem}
.toast{position:fixed;bottom:1.2rem;left:50%;transform:translateX(-50%) translateY(200%);
background:#173b35;color:#fff;padding:.78rem 1.2rem;border-radius:8px;font-size:.88rem;
transition:transform .2s;z-index:50;box-shadow:0 14px 34px rgba(23,32,51,.18);max-width:90vw}
.toast.show{transform:translateX(-50%)}
.toast.bad{background:var(--danger);color:#fff}.warn{background:var(--amber-soft);border:1px solid #f2cf75;color:#6f4600;border-radius:8px;
padding:.7rem .9rem;font-size:.83rem;margin-bottom:1rem}
.doc{display:flex;align-items:center;gap:.6rem;padding:.65rem 0;border-bottom:1px solid var(--line-soft);
font-size:.85rem}
.doc .g{flex:1;min-width:0}
.doc .fn{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.st{font-size:.72rem;padding:.14rem .5rem;border-radius:999px;background:#eef2f7;color:#475569;font-weight:800}
.st.indexed{background:var(--success-soft);color:var(--success)}.st.failed{background:var(--danger-soft);color:var(--danger)}
.st.pending,.st.extracting,.st.embedding{background:var(--blue-soft);color:var(--blue)}@media(max-width:720px){header{align-items:flex-start}
nav{width:100%;overflow:auto;flex-wrap:nowrap}nav a{white-space:nowrap}main{padding:1rem}.page-head{align-items:flex-start}.cards{grid-template-columns:1fr}
.drawer{width:100vw}.table-wrap table{min-width:680px}}
</style></head><body>
<div class="toast" id="toast"></div>
<header>
<span class="brand">Timmins Admin</span>
<nav>
<a href="/admin" data-nav="home">Overview</a>
<a href="/admin/leads" data-nav="leads">Leads</a>
<a href="/admin/courses" data-nav="courses">Courses</a>
<a href="/admin/knowledge" data-nav="knowledge">Knowledge</a>
<a href="/admin/controls" data-nav="controls">Controls</a>
</nav>
<span class="sp"></span>
<form method="post" action="/admin/logout"><button type="submit">Sign out</button></form>
</header>
<main>__BODY__</main>
<script>
document.querySelectorAll('nav a').forEach(a=>{if(a.dataset.nav==="__NAV__")a.classList.add('on');});
async function api(path){const r=await fetch(path,{credentials:'same-origin'});
if(r.status===401){location.href='/admin/login';throw new Error('unauth');}
if(!r.ok)throw new Error('http '+r.status);return r.json();}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function send(path,method,body,isForm){
 const opts={method:method,credentials:'same-origin'};
 if(isForm){opts.body=body;}
 else if(body!==undefined){opts.headers={'Content-Type':'application/json'};opts.body=JSON.stringify(body);}
 const r=await fetch(path,opts);
 if(r.status===401){location.href='/admin/login';throw new Error('unauth');}
 let data={};try{data=await r.json();}catch(e){}
 if(!r.ok)throw new Error(data.detail||('request failed ('+r.status+')'));
 return data;}
function toast(msg,bad){const t=document.getElementById('toast');if(!t)return;
 t.textContent=msg;t.className='toast show'+(bad?' bad':'');
 clearTimeout(window._tt);window._tt=setTimeout(()=>{t.className='toast';},4000);}
__SCRIPT__
</script></body></html>"""


def _admin_page(nav: str, title: str, body: str, script: str) -> str:
    return (
        _ADMIN_SHELL.replace("__TITLE__", title)
        .replace("__NAV__", nav)
        .replace("__BODY__", body)
        .replace("__SCRIPT__", script)
    )


_ADMIN_HOME_BODY = """<section class="page-head"><div><div class="eyebrow">Today at a glance</div>
<h1>Overview</h1><p class="sub">A quick health check for leads, messages, course content and bot knowledge.</p></div></section>
<div class="cards" id="cards"><div class="muted">Loading…</div></div>
<h2>Leads by status</h2>
<div class="table-wrap"><table><thead><tr><th>Status</th><th>Count</th></tr></thead><tbody id="st"></tbody></table></div>"""

_ADMIN_HOME_SCRIPT = """(async()=>{
const s=await api('/admin/api/summary');
document.getElementById('cards').innerHTML=[
 ['Total leads',s.total_leads],['Messages',s.total_messages],
 ['Courses (active)',s.active_courses+' / '+s.total_courses],
 ['Knowledge topics',s.knowledge_topics]
].map(([l,n])=>`<div class="card"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`).join('');
document.getElementById('st').innerHTML=(s.leads_by_status||[]).map(r=>
 `<tr><td><span class="pill">${esc(r.status)}</span></td><td>${esc(r.count)}</td></tr>`).join('')
 ||'<tr><td colspan=2 class="muted">No leads yet.</td></tr>';
})().catch(e=>{});"""

_ADMIN_LEADS_BODY = """<section class="page-head"><div><div class="eyebrow">Conversations</div>
<h1>Leads</h1><p class="sub">Filter enquiries, open a lead, and review the WhatsApp conversation in one place.</p></div></section>
<div class="row">
<select id="status"><option value="">All statuses</option></select>
<select id="course"><option value="">All courses</option></select>
<span class="muted" id="count"></span>
</div>
<div class="table-wrap"><table><thead><tr><th>Name</th><th>Phone</th><th>Course</th><th>Status</th><th>Updated</th></tr></thead>
<tbody id="rows"><tr><td colspan=5 class="muted">Loading…</td></tr></tbody></table></div>
<div class="drawer" id="drawer">
<div class="dh"><strong id="dName">Lead</strong><button class="x" onclick="closeDrawer()">×</button></div>
<div class="db" id="dBody"></div></div>"""

_ADMIN_LEADS_SCRIPT = """
let COURSES=[];
function fmt(s){if(!s)return '';return String(s).replace('T',' ').slice(0,16);}
async function load(){
 const st=document.getElementById('status').value, co=document.getElementById('course').value;
 const q=new URLSearchParams();if(st)q.set('status',st);if(co)q.set('course',co);
 const d=await api('/admin/api/leads?'+q.toString());
 document.getElementById('count').textContent=d.leads.length+' lead(s)';
 document.getElementById('rows').innerHTML=d.leads.map(l=>
  `<tr onclick="openLead('${encodeURIComponent(l.phone)}')">
   <td>${esc(l.name||'—')}</td><td>${esc(l.phone)}</td><td>${esc(l.course||'—')}</td>
   <td><span class="pill">${esc(l.status||'—')}</span></td><td class="muted">${esc(fmt(l.updated_at))}</td></tr>`
 ).join('')||'<tr><td colspan=5 class="muted">No leads match.</td></tr>';
}
async function openLead(p){
 const d=await api('/admin/api/leads/'+p);const L=d.lead||{};
 document.getElementById('dName').textContent=L.name||L.phone||'Lead';
 const meta=['status','course','score','email','source','created_at']
  .filter(k=>L[k]).map(k=>`<div class="msg-meta"><span class="muted">${k}:</span> ${esc(L[k])}</div>`).join('');
 const msgs=(d.history||[]).map(m=>
  `<div class="msg ${m.direction==='inbound'?'inbound':'outbound'}">
   <div class="d">${m.direction} · ${esc(fmt(m.created_at))}</div>${esc(m.body)}</div>`).join('')
  ||'<div class="empty">No messages.</div>';
 document.getElementById('dBody').innerHTML=`<div class="note">${meta||'No metadata.'}</div>${msgs}`;
 document.getElementById('drawer').classList.add('open');
}
function closeDrawer(){document.getElementById('drawer').classList.remove('open');}
(async()=>{
 const m=await api('/admin/api/meta');
 document.getElementById('status').insertAdjacentHTML('beforeend',
  m.statuses.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join(''));
 document.getElementById('course').insertAdjacentHTML('beforeend',
  m.courses.map(c=>`<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join(''));
 document.getElementById('status').onchange=load;
 document.getElementById('course').onchange=load;
 await load();
})().catch(e=>{});"""

_ADMIN_COURSES_BODY = """<section class="page-head"><div><div class="eyebrow">Training catalogue</div>
<h1>Courses</h1><p class="sub">Keep dates, fees, venues and source documents ready for the WhatsApp bot.</p></div>
<button class="btn" onclick="openCourse(null)">+ Add course</button></section>
<div id="banner"></div>
<div class="row"><span class="muted" id="count"></span></div>
<div class="table-wrap"><table><thead><tr><th>Course</th><th>Status</th><th>Dates</th><th>Venue</th><th>Docs</th></tr></thead>
<tbody id="rows"><tr><td colspan=5 class="muted">Loading…</td></tr></tbody></table></div>
<div class="drawer" id="drawer">
<div class="dh"><strong id="dName">Course</strong><button class="x" onclick="closeDrawer()">×</button></div>
<div class="db" id="dBody"></div></div>"""

_ADMIN_COURSES_SCRIPT = """
let CUR=null, POLL=null, EDITABLE=true, PENDING_FILE=null;
function closeDrawer(){document.getElementById('drawer').classList.remove('open');
 if(POLL){clearInterval(POLL);POLL=null;} CUR=null; PENDING_FILE=null;}

async function loadList(){
 const d=await api('/admin/api/courses');
 EDITABLE=d.editable!==false;
 document.getElementById('banner').innerHTML=EDITABLE?'':
  `<div class="warn">Editing is disabled: the bot is reading its content from files.
   Set <b>CONTENT_SOURCE=db</b> to turn on editing.</div>`;
 document.getElementById('count').textContent=d.courses.length+' course(s)';
 document.getElementById('rows').innerHTML=d.courses.map(c=>
  `<tr onclick="openCourse('${esc(c.slug)}')"><td>${esc(c.name)}</td>
   <td><span class="pill ${c.active?'active':'archived'}">${c.active?'active':'archived'}</span></td>
   <td class="muted">${esc(c.dates||'—')}</td><td class="muted">${esc(c.venue||'—')}</td>
   <td class="muted">${c.document_count||0}</td></tr>`
 ).join('')||'<tr><td colspan=5 class="muted">No courses yet.</td></tr>';
}

async function openCourse(slug){
 const blank={slug:'',name:'',active:true,dates:'',venue:'',fees:{},hrdc_deadline:'',
  payment_deadline:'',keywords:[],overview:''};
 CUR=slug?((await api('/admin/api/courses')).courses.find(x=>x.slug===slug)||blank):blank;
 document.getElementById('dName').textContent=slug?CUR.name:'New course';
 const dis=EDITABLE?'':'disabled';
 document.getElementById('dBody').innerHTML=`
  ${slug?'':`<div class="note">Have the brochure? Upload it and the fields below are filled in
   for you — <b>check them before saving</b>, especially the fees. Scanned files are read with OCR.
   <div class="btns"><input type="file" id="src" ${dis}>
   <button class="btn" id="read" ${dis}>Read document</button></div>
   <div id="readmsg" class="muted" style="margin-top:.5rem"></div></div>`}
  <label class="f">Course name<input id="f_name" value="${esc(CUR.name)}" ${dis}></label>
  <label class="f">Dates<input id="f_dates" value="${esc(CUR.dates||'')}" ${dis}></label>
  <label class="f">Venue<input id="f_venue" value="${esc(CUR.venue||'')}" ${dis}></label>
  <label class="f">Fees (JSON)<input id="f_fees" value='${esc(JSON.stringify(CUR.fees||{}))}' ${dis}></label>
  <label class="f">HRDC deadline<input id="f_hrdc" value="${esc(CUR.hrdc_deadline||'')}" ${dis}></label>
  <label class="f">Payment deadline<input id="f_pay" value="${esc(CUR.payment_deadline||'')}" ${dis}></label>
  <label class="f">Keywords (comma separated)
   <input id="f_kw" value="${esc((CUR.keywords||[]).join(', '))}" ${dis}></label>
  <label class="f">Overview (Markdown — the bot answers from this)
   <textarea id="f_ov" rows="14" ${dis}>${esc(CUR.overview||'')}</textarea></label>
  <div class="btns"><button class="btn" id="save" ${dis}>Save</button>
   ${slug?`<button class="btn ghost" id="toggle" ${dis}>${CUR.active?'Archive':'Reactivate'}</button>
   <button class="btn danger" id="del" ${dis}>Delete permanently</button>`:''}</div>
  ${slug?`<h2>Documents</h2>
   <div class="note">Upload a PDF, Word file or scan. Scanned pages are read with OCR.
    The bot can answer from a document once it shows <b>indexed</b>.</div>
   <input type="file" id="file" ${dis}>
   <div class="btns"><button class="btn" id="up" ${dis}>Upload</button>
    <button class="btn ghost" id="reindex" ${dis}>Re-index</button></div>
   <div id="docs" class="muted" style="margin-top:.8rem">Loading…</div>`:''}`;

 const save=document.getElementById('save');
 if(save)save.onclick=saveCourse;
 const read=document.getElementById('read'); if(read)read.onclick=readDocument;
 const t=document.getElementById('toggle'); if(t)t.onclick=toggleActive;
 const d=document.getElementById('del'); if(d)d.onclick=deleteCourse;
 const u=document.getElementById('up'); if(u)u.onclick=upload;
 const r=document.getElementById('reindex'); if(r)r.onclick=reindex;
 document.getElementById('drawer').classList.add('open');
 if(slug){loadDocs();if(POLL)clearInterval(POLL);POLL=setInterval(loadDocs,3000);}
}

function formValues(){
 return {slug:CUR.slug||undefined,name:document.getElementById('f_name').value,
  dates:document.getElementById('f_dates').value,venue:document.getElementById('f_venue').value,
  fees:document.getElementById('f_fees').value,
  hrdc_deadline:document.getElementById('f_hrdc').value,
  payment_deadline:document.getElementById('f_pay').value,
  keywords:document.getElementById('f_kw').value,
  overview:document.getElementById('f_ov').value};
}
// Fills the form from a brochure. Nothing is saved here — the admin reviews first, and the
// same file is attached to the course after Save so it also becomes searchable.
async function readDocument(){
 const input=document.getElementById('src');
 if(!input.files.length){toast('Choose the brochure first.',true);return;}
 const btn=document.getElementById('read'), msg=document.getElementById('readmsg');
 btn.disabled=true;btn.textContent='Reading…';
 msg.textContent='Extracting text and reading the details. Scanned files take longer.';
 const fd=new FormData();fd.append('file',input.files[0]);
 try{
  const r=await send('/admin/api/courses/extract','POST',fd,true);
  const f=r.fields||{};
  const set=(id,v)=>{const el=document.getElementById(id);if(el&&v)el.value=v;};
  set('f_name',f.name);set('f_dates',f.dates);set('f_venue',f.venue);
  set('f_hrdc',f.hrdc_deadline);set('f_pay',f.payment_deadline);
  set('f_kw',(f.keywords||[]).join(', '));
  if(f.fees&&Object.keys(f.fees).length)set('f_fees',JSON.stringify(f.fees));
  set('f_ov',f.overview);
  PENDING_FILE=input.files[0];
  const missing=['name','dates','venue'].filter(k=>!f[k]);
  msg.innerHTML=`Read ${r.characters} characters (${esc(r.extraction_method)}). `+
   (missing.length?`Could not find: <b>${esc(missing.join(', '))}</b> — please fill those in. `:'')+
   `<b>Check the fees and dates against the document before saving.</b>`;
 }catch(e){msg.textContent='';toast(e.message,true);}
 finally{btn.disabled=false;btn.textContent='Read document';}
}

async function saveCourse(){
 try{
  const r=await send('/admin/api/courses','POST',formValues());
  toast(r.created?'Course created.':'Course saved.');
  // Attach the brochure the fields came from, so the bot can answer from its full text too.
  if(PENDING_FILE&&r.slug){
   const fd=new FormData();fd.append('file',PENDING_FILE);
   try{
    await send('/admin/api/courses/'+r.slug+'/documents','POST',fd,true);
    toast('Course created. Reading the document into the bot now…');
   }catch(e){toast('Course saved, but attaching the document failed: '+e.message,true);}
  }
  PENDING_FILE=null;closeDrawer();await loadList();
 }catch(e){toast(e.message,true);}
}
async function toggleActive(){
 try{const r=await send('/admin/api/courses/'+CUR.slug+'/active','POST',{active:!CUR.active});
  toast(r.active?'Course reactivated and re-indexed.':'Course archived — it is no longer searchable.');
  closeDrawer();await loadList();}
 catch(e){toast(e.message,true);}
}
async function deleteCourse(){
 if(!confirm('Delete "'+CUR.name+'" permanently? Its documents and search data are erased. '+
  'Archiving instead keeps everything and is reversible.'))return;
 try{await send('/admin/api/courses/'+CUR.slug,'DELETE');
  toast('Course deleted.');closeDrawer();await loadList();}
 catch(e){toast(e.message,true);}
}
async function upload(){
 const input=document.getElementById('file');
 if(!input.files.length){toast('Choose a file first.',true);return;}
 const fd=new FormData();fd.append('file',input.files[0]);
 const btn=document.getElementById('up');btn.disabled=true;btn.textContent='Uploading…';
 try{await send('/admin/api/courses/'+CUR.slug+'/documents','POST',fd,true);
  input.value='';toast('Uploaded. Reading the document now…');await loadDocs();}
 catch(e){toast(e.message,true);}
 finally{btn.disabled=false;btn.textContent='Upload';}
}
async function reindex(){
 try{const r=await send('/admin/api/courses/'+CUR.slug+'/reindex','POST');
  toast('Re-indexed: '+(r.chunks||0)+' chunk(s) from '+(r.documents||0)+' document(s).');}
 catch(e){toast(e.message,true);}
}
async function delDoc(id){
 if(!confirm('Delete this document? The bot will stop using it.'))return;
 try{await send('/admin/api/documents/'+id,'DELETE');toast('Document deleted.');await loadDocs();}
 catch(e){toast(e.message,true);}
}
async function loadDocs(){
 if(!CUR||!CUR.slug)return;
 try{
  const d=await api('/admin/api/courses/'+CUR.slug+'/documents');
  const box=document.getElementById('docs');if(!box)return;
  box.innerHTML=(d.documents||[]).map(x=>
   `<div class="doc"><div class="g"><div class="fn">${esc(x.filename)}</div>
    <div class="muted" style="font-size:.75rem">${esc(x.extraction_method||'')}
     ${x.chunk_count?esc(x.chunk_count)+' chunks':''}
     ${x.ingest_error?'· '+esc(x.ingest_error):''}</div></div>
    <span class="st ${esc(x.ingest_status)}">${esc(x.ingest_status)}</span>
    <button class="btn ghost" onclick="delDoc(${x.id})">Delete</button></div>`
  ).join('')||'<div class="muted">No documents yet.</div>';
 }catch(e){}
}
loadList().catch(e=>{});"""

_ADMIN_KNOWLEDGE_BODY = """<section class="page-head"><div><div class="eyebrow">Bot answers</div>
<h1>Company knowledge</h1><p class="sub">Update the permanent information the bot uses across every course.</p></div>
<button class="btn" onclick="openTopic(null)">+ Add topic</button></section>
<div id="banner"></div>
<div class="note">Permanent information the bot uses for every course — who the company is,
payment terms, cancellation rules. Changes take effect on the next message.</div>
<div class="row"><span class="muted" id="count"></span></div>
<div class="table-wrap"><table><thead><tr><th>Topic</th><th>Preview</th></tr></thead>
<tbody id="rows"><tr><td colspan=2 class="muted">Loading…</td></tr></tbody></table></div>
<h2>Policies</h2>
<div class="note">Structured rules (payment, cancellation tiers, certification, company details).
Edit as JSON — it is validated before saving.</div>
<textarea id="pol" rows="16">Loading…</textarea>
<div class="btns"><button class="btn" id="savePol">Save policies</button></div>
<div class="drawer" id="drawer">
<div class="dh"><strong id="dName">Topic</strong><button class="x" onclick="closeDrawer()">×</button></div>
<div class="db" id="dBody"></div></div>"""

_ADMIN_KNOWLEDGE_SCRIPT = """
let EDITABLE=true, CUR=null;
function closeDrawer(){document.getElementById('drawer').classList.remove('open');CUR=null;}

async function load(){
 const d=await api('/admin/api/knowledge');
 EDITABLE=d.editable!==false;
 document.getElementById('banner').innerHTML=EDITABLE?'':
  `<div class="warn">Editing is disabled: the bot is reading its content from files.
   Set <b>CONTENT_SOURCE=db</b> to turn on editing.</div>`;
 const topics=d.topics||[];
 document.getElementById('count').textContent=topics.length+' topic(s)';
 document.getElementById('rows').innerHTML=topics.map(t=>
  `<tr onclick="openTopic('${esc(t.topic)}')"><td>${esc(t.topic)}</td>
   <td class="muted">${esc((t.body||'').replace(/\\s+/g,' ').slice(0,90))}…</td></tr>`
 ).join('')||'<tr><td colspan=2 class="muted">No topics yet.</td></tr>';
 document.getElementById('pol').value=JSON.stringify(d.policies||{},null,2);
 document.getElementById('savePol').disabled=!EDITABLE;
 window._topics=topics;
}

function openTopic(name){
 const t=name?(window._topics||[]).find(x=>x.topic===name):{topic:'',body:''};
 CUR=t||{topic:'',body:''};
 document.getElementById('dName').textContent=name||'New topic';
 const dis=EDITABLE?'':'disabled';
 document.getElementById('dBody').innerHTML=`
  <label class="f">Topic name (letters, numbers and underscores)
   <input id="k_topic" value="${esc(CUR.topic)}" ${name?'readonly':''} ${dis}></label>
  <label class="f">Content (Markdown)
   <textarea id="k_body" rows="18" ${dis}>${esc(CUR.body||'')}</textarea></label>
  <div class="btns"><button class="btn" id="k_save" ${dis}>Save</button>
   ${name?`<button class="btn danger" id="k_del" ${dis}>Delete</button>`:''}</div>`;
 document.getElementById('k_save').onclick=saveTopic;
 const del=document.getElementById('k_del'); if(del)del.onclick=deleteTopic;
 document.getElementById('drawer').classList.add('open');
}

async function saveTopic(){
 try{
  await send('/admin/api/knowledge','POST',{topic:document.getElementById('k_topic').value,
   body:document.getElementById('k_body').value});
  toast('Topic saved — the bot is using it now.');closeDrawer();await load();
 }catch(e){toast(e.message,true);}
}
async function deleteTopic(){
 if(!confirm('Delete "'+CUR.topic+'"? The bot will no longer use it.'))return;
 try{await send('/admin/api/knowledge/'+encodeURIComponent(CUR.topic),'DELETE');
  toast('Topic deleted.');closeDrawer();await load();}
 catch(e){toast(e.message,true);}
}
document.getElementById('savePol').onclick=async()=>{
 let parsed;
 try{parsed=JSON.parse(document.getElementById('pol').value);}
 catch(e){toast('That is not valid JSON: '+e.message,true);return;}
 try{await send('/admin/api/policies','POST',{policies:parsed});toast('Policies saved.');}
 catch(e){toast(e.message,true);}
};
load().catch(e=>{});"""

_ADMIN_CONTROLS_BODY = """<section class="page-head"><div><div class="eyebrow">Bot behaviour</div>
<h1>Controls</h1><p class="sub">Switches that take effect on the next message — no redeploy needed.</p></div></section>
<div id="live" class="muted">Loading…</div>
<div class="btns"><button class="btn" id="saveSettings">Save changes</button></div>
<h2>Set at deploy time</h2>
<div class="note">These are read once when the service starts, so changing them needs a redeploy.
Secrets are shown only as configured or not set.</div>
<div class="table-wrap"><table><thead><tr><th>Setting</th><th>Value</th></tr></thead>
<tbody id="fixed"></tbody></table></div>
<h2>Change password</h2>
<div class="note">Signing in uses one shared password. Changing it signs nobody out, but the old
password stops working immediately.</div>
<label class="f">Current password<input type="password" id="pw_cur"></label>
<label class="f">New password (at least 10 characters)<input type="password" id="pw_new"></label>
<div class="btns"><button class="btn ghost" id="savePw">Change password</button></div>"""

_ADMIN_CONTROLS_SCRIPT = """
let SPECS=[];
function field(s){
 const id='s_'+s.key;
 if(s.type==='bool'){
  const on=String(s.value).toLowerCase()==='true';
  return `<label class="f">${esc(s.label)}
   <select id="${id}"><option value="true"${on?' selected':''}>On</option>
   <option value="false"${on?'':' selected'}>Off</option></select></label>
   <div class="muted" style="font-size:.82rem;margin-top:-.2rem">${esc(s.help)}</div>`;
 }
 return `<label class="f">${esc(s.label)}
  <input id="${id}" value="${esc(s.value)}"></label>
  <div class="muted" style="font-size:.82rem;margin-top:-.2rem">${esc(s.help)}</div>`;
}
async function load(){
 const d=await api('/admin/api/settings');
 SPECS=d.editable||[];
 document.getElementById('live').innerHTML=SPECS.map(field).join('');
 document.getElementById('fixed').innerHTML=(d.restart_required||[]).map(r=>
  `<tr><td>${esc(r.label)}<div class="muted" style="font-size:.78rem">${esc(r.key)}</div></td>
   <td>${r.value?esc(r.value):'<span class="muted">not set</span>'}</td></tr>`).join('');
}
document.getElementById('saveSettings').onclick=async()=>{
 const settings={};
 SPECS.forEach(s=>{const el=document.getElementById('s_'+s.key);if(el)settings[s.key]=el.value;});
 try{await send('/admin/api/settings','POST',{settings});
  toast('Saved — the bot uses these from its next message.');await load();}
 catch(e){toast(e.message,true);}
};
document.getElementById('savePw').onclick=async()=>{
 const cur=document.getElementById('pw_cur').value, nw=document.getElementById('pw_new').value;
 if(!cur||!nw){toast('Fill in both password fields.',true);return;}
 try{await send('/admin/api/password','POST',{current:cur,new:nw});
  document.getElementById('pw_cur').value='';document.getElementById('pw_new').value='';
  toast('Password changed. Use the new one next time you sign in.');}
 catch(e){toast(e.message,true);}
};
load().catch(e=>{});"""
