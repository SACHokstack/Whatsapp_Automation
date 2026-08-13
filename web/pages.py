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
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}
form{background:#1e293b;padding:2rem;border-radius:12px;width:min(90vw,320px);box-shadow:0 10px 40px rgba(0,0,0,.4)}
h1{font-size:1.1rem;margin:0 0 1.2rem}input{width:100%;padding:.6rem;margin:.3rem 0 1rem;border-radius:8px;
border:1px solid #334155;background:#0f172a;color:#e2e8f0;box-sizing:border-box}
button{width:100%;padding:.6rem;border:0;border-radius:8px;background:#2563eb;color:#fff;font-weight:600;cursor:pointer}
.err{color:#f87171;font-size:.85rem;margin-bottom:.6rem;min-height:1rem}</style></head>
<body><form method="post" action="/admin/login">
<h1>Timmins Admin</h1><div class="err">__ERROR__</div>
<label>Password</label><input type="password" name="password" autofocus required>
<button type="submit">Sign in</button></form></body></html>"""


_ADMIN_SHELL = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Timmins Admin · __TITLE__</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0}
header{background:#1e293b;border-bottom:1px solid #334155;padding:.7rem 1.2rem;
display:flex;align-items:center;gap:1.4rem;flex-wrap:wrap;position:sticky;top:0;z-index:5}
header .brand{font-weight:700;color:#fff}
nav{display:flex;gap:.3rem;flex-wrap:wrap}
nav a{color:#94a3b8;text-decoration:none;padding:.35rem .7rem;border-radius:8px;font-size:.9rem}
nav a:hover{background:#0f172a;color:#e2e8f0}
nav a.on{background:#2563eb;color:#fff}
header .sp{flex:1}
header form{margin:0}
header button{background:transparent;border:1px solid #334155;color:#94a3b8;padding:.35rem .7rem;
border-radius:8px;cursor:pointer;font-size:.85rem}
main{padding:1.3rem;max-width:1100px;margin:0 auto}
h1{font-size:1.25rem;margin:.2rem 0 1.1rem}
h2{font-size:1rem;margin:1.4rem 0 .6rem;color:#cbd5e1}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.9rem}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:1rem}
.card .n{font-size:1.8rem;font-weight:700;color:#fff}
.card .l{color:#94a3b8;font-size:.8rem;text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th,td{text-align:left;padding:.5rem .6rem;border-bottom:1px solid #1e293b}
th{color:#94a3b8;font-weight:600;font-size:.78rem;text-transform:uppercase;letter-spacing:.03em}
tbody tr:hover{background:#1e293b;cursor:pointer}
.pill{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:.72rem;
background:#334155;color:#cbd5e1}
.pill.active{background:#064e3b;color:#6ee7b7}
.pill.archived{background:#3f2937;color:#fca5a5}
.muted{color:#64748b}
select,input{background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:8px;
padding:.4rem .6rem;font-size:.85rem}
.row{display:flex;gap:.7rem;flex-wrap:wrap;align-items:center;margin-bottom:1rem}
pre{white-space:pre-wrap;word-break:break-word;background:#0b1220;border:1px solid #1e293b;
border-radius:10px;padding:.9rem;font-size:.82rem;line-height:1.5;margin:0}
.drawer{position:fixed;top:0;right:0;height:100vh;width:min(92vw,460px);background:#0b1220;
border-left:1px solid #334155;box-shadow:-10px 0 40px rgba(0,0,0,.5);transform:translateX(100%);
transition:transform .18s;z-index:20;display:flex;flex-direction:column}
.drawer.open{transform:none}
.drawer .dh{padding:1rem 1.2rem;border-bottom:1px solid #1e293b;display:flex;
justify-content:space-between;align-items:center}
.drawer .db{padding:1rem 1.2rem;overflow-y:auto;flex:1}
.drawer .x{cursor:pointer;color:#94a3b8;background:transparent;border:0;font-size:1.3rem}
.msg{margin:.5rem 0;padding:.55rem .7rem;border-radius:10px;max-width:85%;font-size:.85rem;line-height:1.4}
.msg.inbound{background:#1e293b;margin-right:auto}
.msg.outbound{background:#1d3a5f;margin-left:auto}
.msg .d{font-size:.68rem;color:#94a3b8;margin-bottom:.15rem}
.empty{color:#64748b;padding:2rem;text-align:center}
.note{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:.7rem .9rem;
color:#94a3b8;font-size:.82rem;margin-bottom:1rem}
a.link{color:#60a5fa}
</style></head><body>
<header>
<span class="brand">Timmins Admin</span>
<nav>
<a href="/admin" data-nav="home">Overview</a>
<a href="/admin/leads" data-nav="leads">Leads</a>
<a href="/admin/courses" data-nav="courses">Courses</a>
<a href="/admin/knowledge" data-nav="knowledge">Knowledge</a>
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
__SCRIPT__
</script></body></html>"""


def _admin_page(nav: str, title: str, body: str, script: str) -> str:
    return (
        _ADMIN_SHELL.replace("__TITLE__", title)
        .replace("__NAV__", nav)
        .replace("__BODY__", body)
        .replace("__SCRIPT__", script)
    )


_ADMIN_HOME_BODY = """<h1>Overview</h1>
<div class="cards" id="cards"><div class="muted">Loading…</div></div>
<h2>Leads by status</h2>
<table><thead><tr><th>Status</th><th>Count</th></tr></thead><tbody id="st"></tbody></table>"""

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

_ADMIN_LEADS_BODY = """<h1>Leads</h1>
<div class="row">
<select id="status"><option value="">All statuses</option></select>
<select id="course"><option value="">All courses</option></select>
<span class="muted" id="count"></span>
</div>
<table><thead><tr><th>Name</th><th>Phone</th><th>Course</th><th>Status</th><th>Updated</th></tr></thead>
<tbody id="rows"><tr><td colspan=5 class="muted">Loading…</td></tr></tbody></table>
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

_ADMIN_COURSES_BODY = """<h1>Courses</h1>
<div class="note">Read-only for now. Editing, uploads and archiving arrive in the next update.</div>
<table><thead><tr><th>Course</th><th>Status</th><th>Dates</th><th>Venue</th><th>Keywords</th></tr></thead>
<tbody id="rows"><tr><td colspan=5 class="muted">Loading…</td></tr></tbody></table>
<div class="drawer" id="drawer">
<div class="dh"><strong id="dName">Course</strong><button class="x" onclick="closeDrawer()">×</button></div>
<div class="db" id="dBody"></div></div>"""

_ADMIN_COURSES_SCRIPT = """
function closeDrawer(){document.getElementById('drawer').classList.remove('open');}
async function openCourse(slug){
 const c=(await api('/admin/api/courses')).courses.find(x=>x.slug===slug);if(!c)return;
 document.getElementById('dName').textContent=c.name;
 const fees=Object.entries(c.fees||{}).map(([k,v])=>`${k}: ${v}`).join('  ·  ')||'—';
 document.getElementById('dBody').innerHTML=
  `<div class="note">Slug: ${esc(c.slug)}<br>Fees: ${esc(fees)}<br>`+
  `HRDC deadline: ${esc(c.hrdc_deadline||'—')}<br>Payment deadline: ${esc(c.payment_deadline||'—')}</div>`+
  `<h2>Overview</h2><pre>${esc(c.overview||'(none)')}</pre>`;
 document.getElementById('drawer').classList.add('open');
}
(async()=>{
 const d=await api('/admin/api/courses');
 document.getElementById('rows').innerHTML=d.courses.map(c=>
  `<tr onclick="openCourse('${esc(c.slug)}')"><td>${esc(c.name)}</td>
   <td><span class="pill ${c.active?'active':'archived'}">${c.active?'active':'archived'}</span></td>
   <td class="muted">${esc(c.dates||'—')}</td><td class="muted">${esc(c.venue||'—')}</td>
   <td class="muted">${esc((c.keywords||[]).slice(0,4).join(', '))}</td></tr>`
 ).join('')||'<tr><td colspan=5 class="muted">No courses.</td></tr>';
})().catch(e=>{});"""

_ADMIN_KNOWLEDGE_BODY = """<h1>Company knowledge</h1>
<div class="note">Read-only for now. Editing arrives in a later update.</div>
<h2>Topics</h2><div id="topics" class="muted">Loading…</div>
<h2>Policies</h2><pre id="policies" class="muted">Loading…</pre>"""

_ADMIN_KNOWLEDGE_SCRIPT = """(async()=>{
 const d=await api('/admin/api/knowledge');
 document.getElementById('topics').innerHTML=(d.topics||[]).map(t=>
  `<h2>${esc(t.topic)}</h2><pre>${esc(t.body)}</pre>`).join('')||'<div class="muted">No topics.</div>';
 document.getElementById('policies').textContent=JSON.stringify(d.policies||{},null,2);
})().catch(e=>{});"""
