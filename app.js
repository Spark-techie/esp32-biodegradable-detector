const $ = id => document.getElementById(id);
let failures = 0;
let userEditingCam = false;

let backendUrl = localStorage.getItem("detector_backend_url") || "";

function apiUrl(path) {
  if (!backendUrl) return path;
  return backendUrl.replace(/\/+$/, "") + path;
}

function badge(el, text, ok) {
  if (!el) return;
  el.textContent = text;
  el.className = "badge " + (ok ? "ok" : "off");
}

function getServoInput() { return $("servoUrl") || $("stepperUrl"); }
function getServoMsg() { return $("servoMsg") || $("stepperMsg"); }
function getSetServoBtn() { return $("setServoBtn") || $("setStepperBtn"); }
function getTestServoBtn() { return $("testServoBtn") || $("testStepperBtn"); }

function checkMixedContent() {
  const notice = $("mixedNotice");
  if (!notice) return;
  if (window.location.protocol === "https:" && backendUrl && backendUrl.startsWith("http://")) {
    notice.style.display = "block";
    notice.innerHTML = `⚠️ <b>Mixed Content Block:</b> This page is loaded via <b>HTTPS</b>, but your backend is <code>${backendUrl}</code> (HTTP). Web browsers block insecure HTTP requests.<br><b>Options:</b><br>1. Open the local dashboard directly at <a href="http://localhost:8000" style="color:#60a5fa" target="_blank">http://localhost:8000</a> (recommended).<br>2. Expose backend via HTTPS tunnel (e.g. <code>ngrok http 8000</code>).<br>3. In browser address bar, click the site settings icon and allow "Insecure content".`;
  } else {
    notice.style.display = "none";
  }
}

function render(r) {
  const cls = r.label === "NO OBJECT" ? "none" : r.label;
  if ($("label")) {
    $("label").textContent = r.label;
    $("label").className = "label " + cls;
  }
  const pct = Math.round(r.confidence * 100);
  if ($("conf")) $("conf").textContent = r.label === "NO OBJECT" ? "–" : pct + "%";
  if ($("confBar")) {
    $("confBar").style.width = (r.label === "NO OBJECT" ? 0 : pct) + "%";
    $("confBar").style.background = getComputedStyle(document.documentElement).getPropertyValue(
      (r.label === "NON-BIODEGRADABLE" || r.label === "GREEN") ? "--g" : (r.label === "BIODEGRADABLE" || r.label === "WHITE") ? "--w" : "--u");
  }
  if ($("ts")) $("ts").textContent = r.timestamp || "–";
  if ($("fps")) $("fps").textContent = (r.camera?.fps ?? 0) + " fps";
  if ($("lat")) $("lat").textContent = (r.latency_ms ?? 0) + " ms inference";
  badge($("camBadge"), "Camera: " + (r.camera?.online ? "online" : "offline"), r.camera?.online);
  badge($("modelBadge"), "Model: " + (r.model?.status ?? "active"), true);
  if ($("overlay")) $("overlay").classList.toggle("hidden", !!r.camera?.online);
  if ($("cg")) $("cg").textContent = r.counts?.["NON-BIODEGRADABLE"] ?? r.counts?.GREEN ?? 0;
  if ($("cw")) $("cw").textContent = r.counts?.["BIODEGRADABLE"] ?? r.counts?.WHITE ?? 0;
  if ($("camUrl") && !userEditingCam && document.activeElement !== $("camUrl")) {
    $("camUrl").value = r.camera?.url ?? "";
  }
  if ($("hist") && Array.isArray(r.history)) {
    $("hist").innerHTML = r.history.map(h =>
      `<li class="${(h.label || "").split(" ")[0]}"><span>${h.time}</span><b>${h.label}</b><span>${h.label === "NO OBJECT" ? "" : Math.round(h.confidence * 100) + "%"}</span></li>`).join("");
  }
}

async function setCameraUrl(url) {
  try {
    await fetch(apiUrl("/api/config"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ camera_url: url })
    });
    setTimeout(() => {
      if ($("feed")) $("feed").src = apiUrl("/api/stream") + "?" + Date.now();
    }, 300);
  } catch (err) {
    console.error(err);
  }
}

async function setServoUrl(url) {
  const msg = getServoMsg();
  try {
    const res = await fetch(apiUrl("/api/config"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ servo_url: url, stepper_url: url })
    });
    if (res.ok && msg) {
      msg.textContent = "✓ Servo URL saved";
      msg.style.color = "#22c55e";
      setTimeout(() => { msg.textContent = ""; }, 2500);
    } else if (msg) {
      msg.textContent = "✗ Error saving servo URL";
      msg.style.color = "#ef4444";
    }
  } catch (err) {
    if (msg) {
      msg.textContent = "✗ Network error saving URL";
      msg.style.color = "#ef4444";
    }
  }
}

async function testServo() {
  const msg = getServoMsg();
  if (msg) {
    msg.textContent = "Triggering servo…";
    msg.style.color = "#8b96b8";
  }
  try {
    let res = await fetch(apiUrl("/api/trigger_servo"), { method: "POST" });
    if (!res.ok) {
      res = await fetch(apiUrl("/api/trigger_stepper"), { method: "POST" });
    }
    const data = await res.json();
    if (msg) {
      msg.textContent = data.ok ? "✓ Servo triggered! (0° → 90° → 0°)" : ("✗ " + (data.error || "Failed"));
      msg.style.color = data.ok ? "#22c55e" : "#ef4444";
      setTimeout(() => { msg.textContent = ""; }, 3500);
    }
  } catch (err) {
    // If backend is offline, try direct trigger if direct ESP32 URL was given in the input
    const input = getServoInput();
    const directUrl = input ? input.value.trim() : "";
    if (directUrl && directUrl.startsWith("http")) {
      try {
        await fetch(directUrl, { method: "POST", mode: "no-cors" });
        if (msg) {
          msg.textContent = "✓ Direct trigger sent to ESP32 servo!";
          msg.style.color = "#22c55e";
          setTimeout(() => { msg.textContent = ""; }, 3500);
        }
        return;
      } catch (_) {}
    }
    if (msg) {
      msg.textContent = "✗ Trigger request failed (backend unreachable)";
      msg.style.color = "#ef4444";
    }
  }
}

async function loadConfig() {
  try {
    const cfg = await (await fetch(apiUrl("/api/config"))).json();
    const input = getServoInput();
    if (input && (cfg.servo_url || cfg.stepper_url)) {
      input.value = cfg.servo_url || cfg.stepper_url;
    }
    if ($("camUrl") && cfg.camera_url && !userEditingCam && document.activeElement !== $("camUrl")) {
      $("camUrl").value = cfg.camera_url;
    }
  } catch (e) {}
}

async function tick() {
  try {
    const r = await (await fetch(apiUrl("/api/result"), { cache: "no-store" })).json();
    render(r);
    failures = 0;
    badge($("beBadge"), "Backend: online", true);
    checkMixedContent();
  } catch (e) {
    failures++;
    badge($("beBadge"), "Backend: offline", false);
    checkMixedContent();
  }
  setTimeout(tick, failures ? 2000 : 400);
}

// MJPEG <img> stops if the backend restarts: re-attach it.
if ($("feed")) {
  $("feed").addEventListener("error", () => {
    setTimeout(() => {
      if ($("feed")) $("feed").src = apiUrl("/api/stream") + "?" + Date.now();
    }, 2000);
  });
}

if ($("reconnect")) {
  $("reconnect").onclick = async () => {
    try {
      await fetch(apiUrl("/api/reconnect"), { method: "POST" });
    } catch (_) {}
    if ($("feed")) $("feed").src = apiUrl("/api/stream") + "?" + Date.now();
  };
}

if ($("reset")) {
  $("reset").onclick = () => fetch(apiUrl("/api/reset_counts"), { method: "POST" }).catch(() => {});
}

function setBackendUrl(url) {
  backendUrl = url.trim();
  if (backendUrl) {
    localStorage.setItem("detector_backend_url", backendUrl);
  } else {
    localStorage.removeItem("detector_backend_url");
  }
  failures = 0;
  badge($("beBadge"), "Backend: connecting…", false);
  if ($("feed")) $("feed").src = apiUrl("/api/stream") + "?" + Date.now();
  checkMixedContent();
  tick();
  loadConfig();
}

if ($("setBackendBtn") && $("backendUrlInput")) {
  $("backendUrlInput").value = backendUrl;
  $("setBackendBtn").onclick = () => setBackendUrl($("backendUrlInput").value);
  $("backendUrlInput").addEventListener("keydown", e => {
    if (e.key === "Enter") setBackendUrl($("backendUrlInput").value);
  });
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

if ($("setCamBtn") && $("camUrl")) {
  $("setCamBtn").onclick = () => setCameraUrl($("camUrl").value.trim());
  $("camUrl").addEventListener("keydown", e => {
    if (e.key === "Enter") setCameraUrl($("camUrl").value.trim());
  });
  $("camUrl").addEventListener("focus", () => { userEditingCam = true; });
  $("camUrl").addEventListener("blur", () => { userEditingCam = false; });
}

const setServoBtn = getSetServoBtn();
const servoInputEl = getServoInput();
if (setServoBtn && servoInputEl) {
  setServoBtn.onclick = () => setServoUrl(servoInputEl.value.trim());
  servoInputEl.addEventListener("keydown", e => {
    if (e.key === "Enter") setServoUrl(servoInputEl.value.trim());
  });
}

const testServoBtn = getTestServoBtn();
if (testServoBtn) {
  testServoBtn.onclick = testServo;
}

document.querySelectorAll(".preset-btn[data-url]").forEach(btn => {
  btn.onclick = () => {
    const u = btn.getAttribute("data-url");
    if (btn.id === "lanHostBtn") return;
    if ($("camUrl")) $("camUrl").value = u;
    setCameraUrl(u);
  };
});

// Initialize feed src on startup if remote backend configured
if (backendUrl && $("feed")) {
  $("feed").src = apiUrl("/api/stream") + "?" + Date.now();
}
checkMixedContent();
loadConfig();
tick();
