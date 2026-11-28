// HoneyBadger and Bees Stream Deck plugin: draws live status keys from System/pipeline/deck.py and runs its presses.
const { execFile } = require("child_process");
const os = require("os");
const path = require("path");

const ROOT = path.join(os.homedir(), "HBB", "System", "pipeline");
const PY = path.join(ROOT, ".venv", "bin", "python");
const DECK = path.join(ROOT, "deck.py");

const args = {};
for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^-/, "")] = process.argv[i + 1];

const contexts = new Map();   // context -> key name
let status = {};
let ws;

function deck(argv, cb) {
  execFile(PY, ["-W", "ignore", DECK, ...argv], { timeout: 60000, maxBuffer: 1 << 20 }, (err, out) => cb && cb(err, out));
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// HBB brand (pipeline gfx.py): navy, gold, cream; red and green only for alerts and done
const NAVY = "#0A192F", GOLD = "#E8A317", CREAM = "#F5F0E8", RED = "#C44034", GREEN = "#3E9E6A", EDGE = "#24395C";

function look(key, s) {
  // background, accent (big text colour), border
  if (!s) return [NAVY, "#8B95A5", EDGE];
  if (s.alert) return [RED, CREAM, CREAM];
  if (key === "review" && (s.failed || 0) > 0) return [RED, CREAM, CREAM];
  if (key === "runway" && s.warn) return [RED, CREAM, CREAM];
  if (key === "rec" && s.on) return [RED, CREAM, CREAM];
  if (key === "approve" && s.armed) return [RED, CREAM, CREAM];
  if (key === "approve" && s.ready) return [GREEN, CREAM, CREAM];
  if (key === "review" && (s.count || 0) > 0 && s.new) return [GOLD, NAVY, GOLD];
  if (key === "comments" && (s.new || 0) > 0) return [GOLD, NAVY, GOLD];
  if (s.attention || (key === "runway" && s.low)) return [GOLD, NAVY, GOLD];
  if (key === "review" && (s.count || 0) > 0) return [NAVY, GOLD, GOLD];
  if (key === "pipeline" && s.busy) return [NAVY, CREAM, GOLD];
  return [NAVY, GOLD, EDGE];
}

function hex(cx, cy, r) {
  const p = [];
  for (let i = 0; i < 6; i++) { const a = Math.PI / 3 * i + Math.PI / 6; p.push((cx + r * Math.cos(a)).toFixed(1) + "," + (cy + r * Math.sin(a)).toFixed(1)); }
  return p.join(" ");
}

function svg(key) {
  const s = status[key];
  let lines = (s && s.lines) || ["HBB", "...", ""];
  if (key === "rec" && s && s.on && s.start) {
    const t = Math.max(0, Math.floor(Date.now() / 1000 - s.start));
    const hh = Math.floor(t / 3600), mm = Math.floor((t % 3600) / 60), ss = t % 60;
    lines = ["REC", (hh ? hh + ":" : "") + String(mm).padStart(2, "0") + ":" + String(ss).padStart(2, "0"), "tap to stop"];
  }
  const [bg, accent, border] = look(key, s);
  const small = bg === GOLD ? NAVY : CREAM;
  const big = String(lines[1] ?? "");
  const bigSize = big.length <= 3 ? 54 : big.length <= 5 ? 42 : big.length <= 7 ? 32 : 24;
  const bottom = String(lines[2] ?? "");
  const bottomSize = bottom.length <= 12 ? 15 : 13;
  const comb = bg === NAVY ? GOLD : (bg === GOLD ? NAVY : CREAM);
  const body =
    `<svg xmlns="http://www.w3.org/2000/svg" width="144" height="144" viewBox="0 0 144 144">` +
    `<rect width="144" height="144" fill="${bg}"/>` +
    `<polygon points="${hex(124, 124, 26)}" fill="none" stroke="${comb}" stroke-opacity="0.16" stroke-width="2"/>` +
    `<polygon points="${hex(124, 79, 26)}" fill="none" stroke="${comb}" stroke-opacity="0.08" stroke-width="2"/>` +
    `<rect x="4" y="4" width="136" height="136" rx="12" fill="none" stroke="${border}" stroke-width="4"/>` +
    `<text x="72" y="36" text-anchor="middle" font-family="Helvetica Neue, Arial" font-weight="700" font-size="17" letter-spacing="0.5" fill="${small}">${esc(lines[0])}</text>` +
    `<text x="72" y="${86 + Math.round((bigSize - 40) / 4)}" text-anchor="middle" font-family="Helvetica Neue, Arial" font-weight="800" font-size="${bigSize}" fill="${accent}">${esc(big)}</text>` +
    `<text x="72" y="124" text-anchor="middle" font-family="Helvetica Neue, Arial" font-weight="600" font-size="${bottomSize}" fill="${small}">${esc(bottom)}</text>` +
    `</svg>`;
  return "data:image/svg+xml;charset=utf8," + encodeURIComponent(body);
}

function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

function draw(ctx) {
  const key = contexts.get(ctx);
  if (key) send({ event: "setImage", context: ctx, payload: { image: svg(key), target: 0 } });
}

function drawAll() {
  for (const ctx of contexts.keys()) draw(ctx);
}

let refreshing = false;
function refresh() {
  if (refreshing || contexts.size === 0) return;
  refreshing = true;
  deck(["status"], (err, out) => {
    refreshing = false;
    if (!err) {
      try { status = JSON.parse(out); } catch (e) { /* keep the last good status */ }
    }
    drawAll();
  });
}

function connect() {
  ws = new WebSocket("ws://127.0.0.1:" + args.port);
  ws.onopen = () => {
    send({ event: args.registerEvent, uuid: args.pluginUUID });
    refresh();
  };
  ws.onmessage = (m) => {
    let msg;
    try { msg = JSON.parse(m.data); } catch (e) { return; }
    const key = msg.action ? msg.action.split(".").pop() : null;
    if (msg.event === "willAppear") {
      contexts.set(msg.context, key);
      draw(msg.context);
      refresh();
    } else if (msg.event === "willDisappear") {
      contexts.delete(msg.context);
    } else if (msg.event === "keyDown") {
      deck(["press", key], (err) => {
        if (err) send({ event: "showAlert", context: msg.context });
        refresh();
        setTimeout(refresh, 4500);   // e.g. the approve key disarms after 4 s
      });
    }
  };
  ws.onclose = () => process.exit(0);
}

connect();
setInterval(refresh, 30000);
setInterval(() => {   // the recording timer ticks every second
  if (status.rec && status.rec.on) for (const [ctx, key] of contexts) if (key === "rec") draw(ctx);
}, 1000);
