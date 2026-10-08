#!/usr/bin/env python3
"""A browser front end for the agent. Week 12's serving layer, with a face.

    python scripts/web.py
    python scripts/web.py --allow en.wikipedia.org --start https://en.wikipedia.org/wiki/Main_Page

Then open http://localhost:8000

WHY THIS IS NOT A TOY. `serve()`, `Governor` and `health()` were written for
Week 12 and exercised only by tests and a batch script. This is the first thing
in the project that uses them the way the lecture describes: a request arrives
from outside, the governor sees it before the model does, exactly one response
comes back whatever happens inside, and the health report aggregates what the
responses said. Nothing here reaches around them.

STDLIB ONLY. `http.server` rather than FastAPI, because a demonstration that
needs `pip install` on the examiner's machine is a demonstration that does not
happen. The threading server is adequate: the agent is the bottleneck at two
minutes a request, and a second request while one is running is rejected on
purpose rather than queued - see `/ask`.

ONE BROWSER, ONE EVENT LOOP, FOR THE PROCESS. Playwright is not thread-safe, so
the agent runs in a single worker thread with its own asyncio loop and the HTTP
handlers hand work to it. The page persists between requests, as it does in
`chat.py`, so "open the article on X" and then "what does it say about Y" work.
"""

import argparse
import asyncio
import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from browser_agent import config
from browser_agent import run_agent
from browser_agent.domains import DOMAINS, Domain, adapt, system_for_domain
from browser_agent.serving import (Governor, Request, format_health, health,
                                   serve)
from browser_agent.ollama_client import HttpTransport, OllamaBackend, OllamaClient

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Browser Agent - Osama Suliman Merghani</title>
<style>
  :root { --bg:#0e1b1a; --card:#142624; --line:#24403c; --ink:#e8f1ef;
          --dim:#8aa8a3; --ok:#4ec9a5; --warn:#e0a458; --bad:#e06c75;
          --accent:#2a9d8f; }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.6
         ui-sans-serif,system-ui,"Segoe UI",sans-serif; }
  .wrap { max-width:880px; margin:0 auto; padding:24px 16px 64px }
  h1 { font-size:20px; margin:0 0 4px }
  .byline { color:var(--ink); font-size:13.5px; margin:0 0 10px;
            padding-bottom:10px; border-bottom:1px solid var(--line) }
  .sub { color:var(--dim); font-size:13px; margin-bottom:20px }
  footer { color:var(--dim); font-size:12px; text-align:center;
           margin-top:28px; padding-top:14px; border-top:1px solid var(--line) }
  .card { background:var(--card); border:1px solid var(--line);
          border-radius:10px; padding:16px; margin-bottom:16px }
  textarea { width:100%; background:#0b1716; color:var(--ink);
             border:1px solid var(--line); border-radius:8px; padding:12px;
             font:inherit; resize:vertical; min-height:72px }
  button { background:var(--accent); color:#06201c; border:0; padding:10px 18px;
           border-radius:8px; font:600 15px/1 inherit; cursor:pointer }
  button:disabled { opacity:.45; cursor:default }
  .row { display:flex; gap:10px; align-items:center; margin-top:10px;
         flex-wrap:wrap }
  .chip { font-size:12px; color:var(--dim); border:1px solid var(--line);
          padding:3px 9px; border-radius:99px; cursor:pointer }
  .chip:hover { color:var(--ink) }
  table { width:100%; border-collapse:collapse; font-size:13px }
  td,th { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line) }
  th { color:var(--dim); font-weight:600 }
  code { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:13px }
  .ok { color:var(--ok) } .warn { color:var(--warn) } .bad { color:var(--bad) }
  .dim { color:var(--dim) }
  .answer { font-size:17px; line-height:1.55; margin:4px 0 10px;
            white-space:pre-wrap }
  .mono { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12.5px }
  a { color:var(--ok) }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) { --bg:#f4f7f6; --card:#fff; --line:#dde5e3;
      --ink:#13211f; --dim:#5d7a75; --accent:#1f7a6d }
    :root:not([data-theme="dark"]) textarea { background:#f8fbfa }
    :root:not([data-theme="dark"]) button { color:#fff }
  }
</style></head><body><div class="wrap">

<h1>Autonomous Web Browser Agent</h1>
<div class="byline">Osama Suliman Merghani &middot; COSC726 Agentic Artificial
Intelligence &middot; Al-Neelain University</div>
<div class="sub" id="meta">loading…</div>

<div class="card">
  <textarea id="q" placeholder="Ask about a page the agent can reach…"></textarea>
  <div class="row">
    <button id="go">Ask</button>
    <span class="dim" id="hint">one request at a time; 2–4 minutes each</span>
  </div>
  <div class="row" id="examples"></div>
</div>

<div class="card" id="live" style="display:none">
  <table><thead><tr><th>#</th><th>tool</th><th>result</th><th>s</th></tr></thead>
  <tbody id="steps"></tbody></table>
  <div class="dim mono" id="elapsed" style="margin-top:8px"></div>
</div>

<div class="card" id="out" style="display:none"></div>

<div class="card">
  <div class="dim" style="margin-bottom:8px">Session health</div>
  <pre class="mono dim" id="health" style="margin:0">no requests yet</pre>
</div>

<footer>Osama Suliman Merghani &middot; MSc Artificial Intelligence &middot;
Al-Neelain University &middot; supervised by Dr Fakhreldin Saeed</footer>

</div><script>
const $ = s => document.querySelector(s);
let polling = null;

const EXAMPLES = [
  "What is the exact heading text on this page?",
  "How many links are on this page?",
  "Open the Wikipedia article about Ibn Khaldun and tell me when he was born",
  "What is the current exchange rate of the Sudanese pound today?",
  "Submit the contact form for me"
];

fetch('/meta').then(r=>r.json()).then(m=>{
  $('#meta').textContent =
    `${m.model} · domain: ${m.domain} · may visit: ${m.allowed.join(', ')}`;
  EXAMPLES.forEach(t=>{
    const c=document.createElement('span'); c.className='chip'; c.textContent=t;
    c.onclick=()=>{ $('#q').value=t; }; $('#examples').appendChild(c);
  });
});

function esc(s){ const d=document.createElement('div'); d.textContent=s??'';
                 return d.innerHTML }

function render(r){
  const cls = r.status==='rejected' ? 'warn' : (r.status==='error' ? 'bad'
            : (r.served ? 'ok' : 'warn'));
  let h = `<div class="${cls}" style="font-size:13px;margin-bottom:6px">`
        + `${esc(r.status)}${r.stop_reason? ' · '+esc(r.stop_reason):''}</div>`;
  if (r.answer) h += `<div class="answer">${esc(r.answer)}</div>`;
  if (r.detail && !r.answer) h += `<div class="answer dim">${esc(r.detail)}</div>`;
  if (r.evidence_url)
    h += `<div style="font-size:13px">source: <a href="${esc(r.evidence_url)}"`
       + ` target="_blank" rel="noopener">${esc(r.evidence_url)}</a></div>`;
  h += `<table style="margin-top:12px"><tr><th>steps</th><th>tokens</th>`
     + `<th>seconds</th><th>refused</th></tr><tr><td>${r.steps}</td>`
     + `<td>${r.tokens}</td><td>${(r.ms/1000).toFixed(0)}</td>`
     + `<td>${r.refusals.length}</td></tr></table>`;
  if (r.refusals.length)
    h += `<div class="mono dim" style="margin-top:8px">`
       + r.refusals.map(x=>'refused: '+esc(x)).join('<br>') + `</div>`;
  if (r.note) h += `<div class="dim" style="margin-top:10px">${esc(r.note)}</div>`;
  $('#out').innerHTML = h; $('#out').style.display='';
}

function poll(){
  fetch('/status').then(r=>r.json()).then(s=>{
    const tb=$('#steps'); tb.innerHTML='';
    s.steps.forEach(st=>{
      const tr=document.createElement('tr');
      tr.innerHTML = `<td>${st.n}</td><td class="mono">${esc(st.tool)}</td>`
        + `<td class="mono ${st.error?'warn':'ok'}">${esc(st.error||'ok')}</td>`
        + `<td>${st.secs}</td>`;
      tb.appendChild(tr);
    });
    $('#elapsed').textContent = s.running ? `working… ${s.elapsed}s` : '';
    if (!s.running){
      clearInterval(polling); polling=null;
      $('#go').disabled=false;
      if (s.result) render(s.result);
      if (s.health) $('#health').textContent = s.health;
    }
  });
}

$('#go').onclick = () => {
  const q = $('#q').value.trim(); if(!q) return;
  $('#go').disabled = true; $('#out').style.display='none';
  $('#steps').innerHTML=''; $('#live').style.display='';
  fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({q})})
    .then(r=>r.json()).then(j=>{
      if (j.busy){ $('#go').disabled=false;
        $('#out').innerHTML='<div class="warn">A request is already running.</div>';
        $('#out').style.display=''; return; }
      polling = setInterval(poll, 1000);
    });
};
$('#q').addEventListener('keydown', e=>{
  if ((e.metaKey||e.ctrlKey) && e.key==='Enter') $('#go').click();
});
</script></body></html>"""


class Engine:
    """The agent, one browser, one asyncio loop, on a worker thread.

    Playwright objects belong to the loop that created them, so everything that
    touches the page is scheduled onto this one. The HTTP handlers only ever
    hand it a string and read a dict back.
    """

    def __init__(self, domain: Domain, model: str, timeout: int, deadline: int,
                 max_chars: int, rate_limit: int):
        self.domain, self.model = domain, model
        self.deadline = deadline
        self.gov = Governor(max_chars=max_chars, rate_limit=rate_limit)
        self.timeout = timeout
        self.state = {"running": False, "steps": [], "elapsed": 0,
                      "result": None, "health": None}
        self.responses = []
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        threading.Thread(target=self._thread, daemon=True).start()
        self.ready.wait()

    def _thread(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._boot())
        self.ready.set()
        self.loop.run_forever()

    async def _boot(self):
        backend = OllamaBackend(self.model,
                                transport=HttpTransport(timeout=self.timeout),
                                think=None, num_predict=1024, num_ctx=8192)
        self.client = OllamaClient(backend)
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self.page = await self._browser.new_page()
        await self.page.goto(self.domain.start_url)
        self.tools, self.reg, self.disp = adapt(self.page, self.domain)
        self.system = system_for_domain(self.reg, self.domain)
        # The weights load on the first call and that takes longer than any
        # per-call timeout worth setting. Paid once, here, before anyone asks.
        backend.chat([{"role": "user", "content": "ok"}])

    def busy(self) -> bool:
        with self.lock:
            return self.state["running"]

    def submit(self, q: str) -> bool:
        with self.lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "steps": [], "elapsed": 0,
                          "result": None, "health": None}
        asyncio.run_coroutine_threadsafe(self._ask(q), self.loop)
        return True

    async def _ask(self, q: str):
        t0 = time.time()
        last = [t0]
        trace_holder = {}

        def watch(entry):
            now = time.time()
            with self.lock:
                self.state["steps"].append({
                    "n": entry["step"], "tool": entry["tool"] or "(prose)",
                    "error": entry["obs"].get("error", ""),
                    "secs": int(now - last[0])})
                self.state["elapsed"] = int(now - t0)
            last[0] = now

        async def run(query):
            res = await run_agent(self.client, self.disp, self.reg, self.system,
                                  query, max_steps=8, token_budget=20_000,
                                  deadline_s=self.deadline, on_step=watch,
                                  refuse_claimed_action=True)
            trace_holder["trace"] = res.trace
            return res

        try:
            resp = await serve(run, Request(q, session_id="web"), self.gov)
        except Exception as e:                   # the boundary holds here too
            resp = None
            err = f"{type(e).__name__}"
        else:
            err = None

        with self.lock:
            if resp is None:
                self.state["result"] = {"status": "error", "answer": "",
                                        "detail": f"internal error: {err}",
                                        "stop_reason": "", "served": False,
                                        "steps": 0, "tokens": 0, "ms": 0,
                                        "evidence_url": "", "refusals": [],
                                        "note": ""}
            else:
                self.responses.append(resp)
                refusals = [f"{t['tool'] or '-'}: {t['obs']['error']}"
                            for t in trace_holder.get("trace", [])
                            if t["obs"].get("error")]
                self.state["result"] = {
                    "status": resp.status, "answer": resp.answer,
                    "detail": resp.detail, "stop_reason": resp.stop_reason,
                    "served": resp.served, "steps": resp.steps,
                    "tokens": resp.tokens, "ms": resp.ms,
                    "evidence_url": resp.evidence_url, "refusals": refusals,
                    "note": NOTES.get(resp.stop_reason or resp.status, "")}
                self.state["health"] = format_health(health(self.responses),
                                                     "this session")
            self.state["running"] = False


# One line per ending, so a refusal reads as a decision rather than a fault.
NOTES = {
    "complete": "",
    "blocked": "The agent stopped to ask you something rather than guessing. "
               "That is a success, not a failure.",
    "out_of_scope": "The agent refused: this is outside what it is permitted "
                    "to do. Also a success.",
    "pending_approval": "Nothing was submitted. A person has to approve it.",
    "unterminated": "The agent wrote prose where a tool call was required and "
                    "the termination guard stopped it. Nothing unsafe happened "
                    "and you got no answer - this is the project's most common "
                    "failure.",
    "ungrounded": "The agent tried to answer without reading anything, twice. "
                  "The grounding guard refused it.",
    "rejected": "The governor refused this before the model was called. No "
                "tokens were spent.",
}


class Handler(BaseHTTPRequestHandler):
    engine: Engine = None

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/meta":
            d = self.engine.domain
            return self._send(200, json.dumps(
                {"model": self.engine.model, "domain": d.name,
                 "allowed": sorted(d.allowed_domains)}))
        if path == "/status":
            with self.engine.lock:
                return self._send(200, json.dumps(self.engine.state))
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if urlparse(self.path).path != "/ask":
            return self._send(404, json.dumps({"error": "not found"}))
        n = int(self.headers.get("Content-Length", 0))
        try:
            q = json.loads(self.rfile.read(n) or b"{}").get("q", "").strip()
        except json.JSONDecodeError:
            return self._send(400, json.dumps({"error": "bad json"}))
        if not q:
            return self._send(400, json.dumps({"error": "empty"}))
        # Refused, not queued. One browser and one model: a queue would hold a
        # connection open for minutes and hide that the system serves one
        # caller at a time, which is a real property worth showing.
        if not self.engine.submit(q):
            return self._send(200, json.dumps({"busy": True}))
        return self._send(200, json.dumps({"ok": True}))

    def log_message(self, *a):
        pass                      # one line per poll, every second, is noise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=config.model())
    ap.add_argument("--domain", default="research", choices=sorted(DOMAINS))
    ap.add_argument("--allow", action="append", default=[], metavar="DOMAIN")
    ap.add_argument("--start", default="")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--deadline", type=int, default=600)
    ap.add_argument("--max-chars", type=int, default=400)
    ap.add_argument("--rate-limit", type=int, default=100)
    args = ap.parse_args()

    base = DOMAINS[args.domain]

    # --allow takes a BARE HOST. Anything else is a configuration mistake, and
    # the gate used to accept it in silence: a markdown link pasted from a chat,
    # `[www.x.sd](https://www.x.sd)`, was stored as one impossible host name and
    # the site it named was never reachable. The allow-list is this project's
    # outermost security control, so a value that cannot match anything must
    # fail loudly rather than quietly shrink it.
    def host_only(value: str) -> str:
        v = value.strip().lower().lstrip(".")
        if v.startswith("[") or "](" in v:
            raise SystemExit(
                f"--allow {value!r} looks like a markdown link.\n"
                f"  Pass the bare host only:  --allow "
                f"{v.split('](')[0].lstrip('[')}")
        for bad, why in (("//", "a scheme"), ("/", "a path"),
                         (" ", "a space")):
            if bad in v:
                raise SystemExit(
                    f"--allow {value!r} contains {why}.\n"
                    f"  Pass the bare host only, e.g.  --allow en.wikipedia.org")
        if "." not in v:
            raise SystemExit(f"--allow {value!r} is not a host name.")
        return v

    allowed = set(base.allowed_domains) | {host_only(d) for d in args.allow}
    start = args.start or base.start_url
    host = start.split("//", 1)[-1].split("/", 1)[0].lower()
    if host:
        allowed.add(host)
    dom = Domain(name=base.name, allowed_domains=frozenset(allowed),
                 start_url=start, refusal=base.refusal,
                 allow_consequential=base.allow_consequential,
                 note=base.note)

    print(f"  model : {args.model}")
    print(f"  domain: {dom.name}   may visit: {', '.join(sorted(allowed))}")
    print("  starting the browser and waking the model…")
    Handler.engine = Engine(dom, args.model, args.timeout, args.deadline,
                            args.max_chars, args.rate_limit)
    print("\n" + "=" * 54)
    print(f"  READY   ->   http://localhost:{args.port}")
    print("=" * 54)
    print("  Copy that address into a browser. Ctrl+C here stops it.\n")
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")


if __name__ == "__main__":
    main()
