const $ = id => document.getElementById(id);
let failures = 0;
let userEditingCam = false;

let backendUrl = localStorage.getItem("detector_backend_url") || "";

function apiUrl(path) {
  if (!backendUrl) return path;
  return backendUrl.replace(/\/+$/, "") + path;
}

function badge(el, text, ok) { el.textContent = text; el.className = "badge " + (ok ? "ok" : "off"); }

function render(r) {
  const cls = r.label === "NO OBJECT" ? "none" : r.label;
  $("label").textContent = r.label; $("label").className = "label " + cls;
  const pct = Math.round(r.confidence * 100);
  $("conf").textContent = r.label === "NO OBJECT" ? "–" : pct + "%";
  $("confBar").style.width = (r.label === "NO OBJECT" ? 0 : pct) + "%";
  $("confBar").style.background = getComputedStyle(document.documentElement).getPropertyValue(
    (r.label === "NON-BIODEGRADABLE" || r.label === "GREEN") ? "--g" : (r.label === "BIODEGRADABLE" || r.label === "WHITE") ? "--w" : "--u");
  $("ts").textContent = r.timestamp || "–";
  $("fps").textContent = r.camera.fps + " fps"; $("lat").textContent = r.latency_ms + " ms inference";
  badge($("camBadge"), "Camera: " + (r.camera.online ? "online" : "offline"), r.camera.online);
  badge($("modelBadge"), "Model: " + r.model.status, true);
  $("overlay").classList.toggle("hidden", r.camera.online);
  $("cg").textContent = r.counts["NON-BIODEGRADABLE"] ?? r.counts.GREEN ?? 0;
  $("cw").textContent = r.counts["BIODEGRADABLE"] ?? r.counts.WHITE ?? 0;
  if ($("camUrl") && !userEditingCam && document.activeElement !== $("camUrl")) {
    $("camUrl").value = r.camera.url ?? "";
  }
  $("hist").innerHTML = r.history.map(h =>
    `<li class="${h.label.split(" ")[0]}"><span>${h.time}</span><b>${h.label}</b><span>${h.label === "NO OBJECT" ? "" : Math.round(h.confidence * 100) + "%"}</span></li>`).join("");
}

async function setCameraUrl(url) {
  try {
    await fetch(apiUrl("/api/config"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ camera_url: url })
    });
    setTimeout(() => { $("feed").src = apiUrl("/api/stream") + "?" + Date.now(); }, 300);
  } catch (err) {
    console.error(err);
  }
}

async function setStepperUrl(url) {
  try {
    const res = await fetch(apiUrl("/api/config"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stepper_url: url })
    });
    if (res.ok && $("stepperMsg")) {
      $("stepperMsg").textContent = "✓ Stepper URL saved";
      setTimeout(() => { $("stepperMsg").textContent = ""; }, 2500);
    }
  } catch (err) {
    if ($("stepperMsg")) $("stepperMsg").textContent = "✗ Error saving stepper URL";
  }
}

async function testStepper() {
  if ($("stepperMsg")) $("stepperMsg").textContent = "Triggering stepper…";
  try {
    const res = await fetch(apiUrl("/api/trigger_stepper"), { method: "POST" });
    const data = await res.json();
    if ($("stepperMsg")) {
      $("stepperMsg").textContent = data.ok ? "✓ Stepper triggered!" : ("✗ " + (data.error || "Failed"));
      setTimeout(() => { $("stepperMsg").textContent = ""; }, 3000);
    }
  } catch (err) {
    if ($("stepperMsg")) $("stepperMsg").textContent = "✗ Trigger request failed";
  }
}

async function loadConfig() {
  try {
    const cfg = await (await fetch(apiUrl("/api/config"))).json();
    if ($("stepperUrl") && cfg.stepper_url) $("stepperUrl").value = cfg.stepper_url;
  } catch (e) {}
}

async function tick() {
  try {
    const r = await (await fetch(apiUrl("/api/result"), { cache: "no-store" })).json();
    render(r); failures = 0; badge($("beBadge"), "Backend: online", true);
  } catch (e) {
    failures++; badge($("beBadge"), "Backend: offline", false);
  }
  setTimeout(tick, failures ? 2000 : 400);
}

// MJPEG <img> stops if the backend restarts: re-attach it.
$("feed").addEventListener("error", () => setTimeout(() => $("feed").src = apiUrl("/api/stream") + "?" + Date.now(), 2000));
$("reconnect").onclick = async () => { await fetch(apiUrl("/api/reconnect"), { method: "POST" }); $("feed").src = apiUrl("/api/stream") + "?" + Date.now(); };
$("reset").onclick = () => fetch(apiUrl("/api/reset_counts"), { method: "POST" });

function setBackendUrl(url) {
  backendUrl = url.trim();
  if (backendUrl) {
    localStorage.setItem("detector_backend_url", backendUrl);
  } else {
    localStorage.removeItem("detector_backend_url");
  }
  failures = 0;
  badge($("beBadge"), "Backend: connecting…", false);
  $("feed").src = apiUrl("/api/stream") + "?" + Date.now();
  tick();
  loadConfig();
}

if ($("setBackendBtn") && $("backendUrlInput")) {
  $("backendUrlInput").value = backendUrl;
  $("setBackendBtn").onclick = () => setBackendUrl($("backendUrlInput").value);
  $("backendUrlInput").addEventListener("keydown", e => { if (e.key === "Enter") setBackendUrl($("backendUrlInput").value); });
}
if ($("localHostBtn")) {
  $("localHostBtn").onclick = () => {
    if ($("backendUrlInput")) $("backendUrlInput").value = "";
    setBackendUrl("");
  };
}
if ($("lanHostBtn")) {
  $("lanHostBtn").onclick = () => {
    const u = $("lanHostBtn").getAttribute("data-url");
    if ($("backendUrlInput")) $("backendUrlInput").value = u;
    setBackendUrl(u);
  };
}

if ($("setCamBtn")) {
  $("setCamBtn").onclick = () => setCameraUrl($("camUrl").value.trim());
  $("camUrl").addEventListener("keydown", e => { if (e.key === "Enter") setCameraUrl($("camUrl").value.trim()); });
  $("camUrl").addEventListener("focus", () => { userEditingCam = true; });
  $("camUrl").addEventListener("blur", () => { userEditingCam = false; });
}
if ($("setStepperBtn")) {
  $("setStepperBtn").onclick = () => setStepperUrl($("stepperUrl").value.trim());
  $("stepperUrl").addEventListener("keydown", e => { if (e.key === "Enter") setStepperUrl($("stepperUrl").value.trim()); });
}
if ($("testStepperBtn")) {
  $("testStepperBtn").onclick = testStepper;
}
document.querySelectorAll(".preset-btn[data-url]").forEach(btn => {
  btn.onclick = () => {
    const u = btn.getAttribute("data-url");
    if (btn.id === "lanHostBtn") return;
    if ($("camUrl")) $("camUrl").value = u;
    setCameraUrl(u);
  };
});

loadConfig();
tick();


