// ROADSTATE_APPJS_V=notify-motion-only-v1
const $ = (id) => document.getElementById(id);

function setText(id, val) {
  const el = $(id);
  if (!el) return;
  el.textContent = (val === undefined || val === null || val === "") ? "-" : String(val);
}

function log(msg) {
  const el = $("lastUpload");
  const line = `[${new Date().toLocaleTimeString()}] ${msg}`;
  if (el) el.textContent = line;
  console.log(line);
}

function setSensors(msg) {
  const el = $("liveSensors");
  if (el) el.textContent = msg;
}

function setBadge(state, text) {
  const b = $("eligBadge");
  if (!b) return;
  b.classList.remove("ok","warn","off");
  b.classList.add(state);
  b.textContent = text;
}

function clamp01(x){ return Math.max(0, Math.min(1, x)); }
function nowMs(){ return Date.now(); }

log("app.js loaded ✅");
setSensors("JS running ✅ (tap Start)");

window.running = false;

let geoWatchId = null;
let tickUI = null;
let tickSend = null;
let tickSensors = null;

let lastGPS = null;        // {lat, lon, speedMps, headingDeg, ts}
let lastMotion = null;     // {g, ts}
let lastOrient = null;     // {alpha,beta,gamma, ts}
let motionEvents = 0;

// smoothed stability metrics
let gEMA = 0;
let gJitterEMA = 0;
let lastG = 1.0;

// sending control
let lastSendMs = 0;
let lastNotMovingSendMs = 0;

// notification control
let lastNotifyMs = 0;
let lastNotifyKey = "";

// wake lock best effort
let wakeLock = null;

function gridKeyFor(lat, lon) {
  const gx = Math.floor(lat * 100);
  const gy = Math.floor(lon * 100);
  return `g100:x${gx}:y${gy}`;
}

function speedBand(speedMps) {
  if (!isFinite(speedMps)) return "unknown";
  const mph = speedMps * 2.236936;
  if (mph < 2) return "0-2";
  if (mph < 10) return "2-10";
  if (mph < 25) return "10-25";
  if (mph < 45) return "25-45";
  if (mph < 70) return "45-70";
  return "70+";
}

function postureFromOrientation(beta, gamma) {
  if (!isFinite(beta) || !isFinite(gamma)) return "unknown";
  const ab = Math.abs(beta);
  const ag = Math.abs(gamma);
  if (ab < 25 && ag < 25) return "flat";
  if (ab > 55 && ab < 125) return "portrait";
  if (ag > 45) return "landscape";
  return "unknown";
}

function sensorFreshnessScore() {
  const t = nowMs();
  const gpsAge = lastGPS ? (t - lastGPS.ts) : 999999;
  const motAge = lastMotion ? (t - lastMotion.ts) : 999999;
  const oriAge = lastOrient ? (t - lastOrient.ts) : 999999;

  const gpsS = clamp01(1 - (gpsAge / 8000));
  const motS = clamp01(1 - (motAge / 1500));
  const oriS = clamp01(1 - (oriAge / 2500));
  return 0.45*gpsS + 0.35*motS + 0.20*oriS;
}

function motionQualityScore(jitter) {
  return clamp01(1 - (jitter / 0.70));
}

function mountState(posture, jitter, speedMps) {
  const moving = (isFinite(speedMps) && speedMps > 1.2);

  if (posture === "flat") {
    if (!moving && jitter < 0.10) return "desk";
    return "flat";
  }
  if (!moving) return "parked";
  if (jitter > 0.55) return "hand";
  if (posture === "portrait" || posture === "landscape") return "mounted";
  return "unknown";
}

function computeConfidence(mount, freshness, motionQ) {
  let mountW = 0.35;
  if (mount === "mounted") mountW = 1.00;
  else if (mount === "desk") mountW = 0.95;
  else if (mount === "flat") mountW = 0.55;
  else if (mount === "parked") mountW = 0.60;
  else if (mount === "hand") mountW = 0.15;
  else mountW = 0.40;

  const c = 0.50*mountW + 0.25*freshness + 0.25*motionQ;
  return clamp01(c);
}

function computeAnalyzable(moving, mount, conf) {
  return (moving && mount === "mounted" && conf >= 0.70) ? 1 : 0;
}

function computePointsEligible(analyzable, conf) {
  return (analyzable === 1 && conf >= 0.80) ? 1 : 0;
}

function qualityNote(moving, mount, conf) {
  if (!moving) return "not_moving";
  if (mount === "hand") return "in_hand";
  if (mount === "flat") return "flat_not_mounted";
  if (mount !== "mounted") return "not_mounted";
  if (conf < 0.70) return "low_confidence";
  return "ok";
}

async function notifyOnce(key, title, body) {
  const t = nowMs();
  if ((t - lastNotifyMs) < 120000 && lastNotifyKey === key) return; // 2 min per same alert
  lastNotifyMs = t;
  lastNotifyKey = key;

  try {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") return;
    if (Notification.permission !== "granted") return;
    new Notification(title, { body });
  } catch (_) {}
}

async function ensureNotifications() {
  try {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") {
      const p = await Notification.requestPermission();
      log("Notifications: " + p);
    } else {
      log("Notifications: " + Notification.permission);
    }
  } catch (e) {
    log("Notification permission error: " + String(e));
  }
}

async function requestWakeLock() {
  try {
    if (!("wakeLock" in navigator)) {
      log("WakeLock not supported (iOS Safari often).");
      return;
    }
    wakeLock = await navigator.wakeLock.request("screen");
    log("WakeLock acquired ✅");
    wakeLock.addEventListener("release", () => log("WakeLock released"));
  } catch (e) {
    log("WakeLock error: " + String(e));
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && window.running) {
    requestWakeLock();
  }
});

async function sendAggregate(payload) {
  try {
    setText("sendState", "Sending…");
    const r = await fetch("/v1/ingest/aggregates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    setText("sendState", "Sent ✓");
    setText("lastSent", new Date().toLocaleTimeString());
    setText("lastErr", "-");
    lastSendMs = nowMs();
  } catch (e) {
    setText("sendState", "Error");
    setText("lastErr", String(e));
  }
}

function updateUILive() {
  const t = nowMs();

  const lat = lastGPS?.lat;
  const lon = lastGPS?.lon;
  const speedMps = lastGPS?.speedMps;
  const headingDeg = lastGPS?.headingDeg;

  const posture = lastOrient ? postureFromOrientation(lastOrient.beta, lastOrient.gamma) : "unknown";
  const jitter = clamp01(gJitterEMA);
  const mount = mountState(posture, jitter, speedMps);

  const moving = (
    (isFinite(speedMps) && speedMps > 1.2) ||
    (jitter > 0.18 && lastMotion && (t - lastMotion.ts) < 1500)
  );

  const fresh = sensorFreshnessScore();
  const motionQ = motionQualityScore(jitter);
  const conf = computeConfidence(mount, fresh, motionQ);

  const analyzable = computeAnalyzable(moving, mount, conf);
  const points = computePointsEligible(analyzable, conf);
  const note = qualityNote(moving, mount, conf);

  // top live tab updates
  setText("status", window.running ? (moving ? "Driving" : "Running") : "Ready");
  setText("speed", isFinite(speedMps) ? (speedMps*2.236936).toFixed(1) + " mph" : "-");
  setText("gridKey", (isFinite(lat) && isFinite(lon)) ? gridKeyFor(lat, lon) : "-");
  setText("conf", conf.toFixed(2));
  setText("shocks", (lastMotion ? (lastMotion.g > 1.75 ? "Shock!" : "0") : "-"));
  setText("samples", motionEvents);

  // eligibility badge (green/orange)
  if (!window.running) setBadge("off", "Idle");
  else if (!moving) setBadge("off", "Not moving");
  else if (analyzable) setBadge("ok", "Eligible");
  else setBadge("warn", "Not eligible");

  // keep the existing rough field but make it meaningful
  setText("rough", analyzable ? "Eligible" : (moving ? "Not eligible" : "Not moving"));

  // notify: moving but not analyzable (mount/hand/flat)
  if (window.running && moving && analyzable === 0) {
    notifyOnce(
      "unanalyzable",
      "RoadState: data may be unanalyzable",
      `Mount: ${mount}. Tip: use a firm windshield/dash mount, avoid in-hand.`
    );
  }

  return { lat, lon, speedMps, headingDeg, posture, mount, moving, jitter, conf, analyzable, points, note };
}

function updateSensorsBox(st) {
  setSensors(
    `gps: ${isFinite(st.lat)?st.lat.toFixed(5):"-"}, ${isFinite(st.lon)?st.lon.toFixed(5):"-"}\n` +
    `speed_mps: ${isFinite(st.speedMps)?st.speedMps.toFixed(2):"-"}  heading: ${isFinite(st.headingDeg)?st.headingDeg.toFixed(0):"-"}\n` +
    `posture: ${st.posture}  mount: ${st.mount}  moving: ${st.moving}\n` +
    `jitter: ${st.jitter.toFixed(2)}  g_ema: ${gEMA.toFixed(2)}  freshness: ${sensorFreshnessScore().toFixed(2)}\n` +
    `confidence: ${st.conf.toFixed(2)}  analyzable: ${st.analyzable}  points: ${st.points}\n` +
    `note: ${st.note}`
  );
}

function currentBucketStart(seconds=5) {
  const ms = Date.now();
  const bucketMs = seconds * 1000;
  const floored = Math.floor(ms / bucketMs) * bucketMs;
  const d = new Date(floored);
  return d.toISOString().replace(".000Z","Z");
}

async function tickSendLoop() {
  if (!window.running) return;

  const st = updateUILive();

  // SEND POLICY:
  // - If moving: send at most every 5s
  // - If not moving: send only every 30s (heartbeat), to avoid spam
  const t = nowMs();
  const minMovingInterval = 5000;
  const minNotMovingInterval = 30000;

  if (st.moving) {
    if ((t - lastSendMs) < minMovingInterval) return;
  } else {
    if ((t - lastNotMovingSendMs) < minNotMovingInterval) return;
    lastNotMovingSendMs = t;
  }

  const lat = st.lat, lon = st.lon;
  const gridKey = (isFinite(lat) && isFinite(lon)) ? gridKeyFor(lat, lon) : "g100:x0:y0";

  const payload = {
    node_id: "ios_web",
    items: [{
      bucket_start: currentBucketStart(5),
      bucket_seconds: 5,
      grid_key: gridKey,
      direction: (isFinite(st.headingDeg) ? String(Math.round(st.headingDeg)) : "unk"),
      speed_band: speedBand(st.speedMps),
      road_roughness: null,
      shock_events: (lastMotion && lastMotion.g > 1.75) ? 1 : 0,
      confidence: Number(st.conf.toFixed(2)),
      sample_count: 1,

      lat: isFinite(lat) ? lat : null,
      lon: isFinite(lon) ? lon : null,

      analyzable: st.analyzable,
      points_eligible: st.points,
      quality_note: st.note,

      // extra fields (future-proof; DB can ignore or we can add columns later)
      mount_state: st.mount,
      moving: st.moving ? 1 : 0,
      speed_mps: isFinite(st.speedMps) ? st.speedMps : null,
      heading_deg: isFinite(st.headingDeg) ? st.headingDeg : null,
      motion_g: lastMotion ? lastMotion.g : null,
      motion_rms: Number(st.jitter.toFixed(3)),
      device_posture: st.posture
    }]
  };

  await sendAggregate(payload);
}

async function startDrive() {
  if (window.running) return;
  window.running = true;
  log("Start ✅");

  await ensureNotifications();
  await requestWakeLock();

  setText("sendState", "Idle");
  setText("lastErr", "-");

  geoWatchId = navigator.geolocation.watchPosition(
    (pos) => {
      lastGPS = {
        lat: pos.coords.latitude,
        lon: pos.coords.longitude,
        speedMps: (pos.coords.speed ?? NaN),
        headingDeg: (pos.coords.heading ?? NaN),
        ts: nowMs()
      };
    },
    (err) => log("GPS error: " + err.message),
    { enableHighAccuracy: true, maximumAge: 0, timeout: 10000 }
  );

  motionEvents = 0;
  gEMA = 0; gJitterEMA = 0; lastG = 1.0;

  window.addEventListener("devicemotion", (e) => {
    if (!window.running) return;
    const a = e.accelerationIncludingGravity;
    if (!a) return;

    const g = Math.sqrt((a.x||0)**2 + (a.y||0)**2 + (a.z||0)**2) / 9.80665;
    motionEvents++;

    const alpha = 0.10;
    gEMA = (1-alpha)*gEMA + alpha*g;

    const dg = Math.abs(g - lastG);
    lastG = g;
    gJitterEMA = (1-alpha)*gJitterEMA + alpha*dg;

    lastMotion = { g, ts: nowMs() };
  }, { passive: true });

  window.addEventListener("deviceorientation", (e) => {
    if (!window.running) return;
    lastOrient = { alpha: e.alpha, beta: e.beta, gamma: e.gamma, ts: nowMs() };
  }, { passive: true });

  // UI: fast tick (snappy)
  tickUI = setInterval(() => updateUILive(), 250);

  // Debug box: slower
  tickSensors = setInterval(() => {
    const st = updateUILive();
    updateSensorsBox(st);
  }, 1000);

  // Send loop: checks rules each second, but still max 5s while moving
  tickSend = setInterval(() => tickSendLoop(), 1000);

  // Buttons
  if ($("btnStart")) $("btnStart").disabled = true;
  if ($("btnStop")) $("btnStop").disabled = false;
  if ($("btnUpload")) { $("btnUpload").disabled = true; $("btnUpload").title = "Auto-sending"; }

  const st0 = updateUILive();
  updateSensorsBox(st0);
  setTimeout(() => tickSendLoop(), 1200);
}

function stopDrive() {
  if (!window.running) return;
  window.running = false;
  log("Stop ⛔️");

  if (geoWatchId !== null) navigator.geolocation.clearWatch(geoWatchId);
  geoWatchId = null;

  if (tickUI) clearInterval(tickUI);
  if (tickSensors) clearInterval(tickSensors);
  if (tickSend) clearInterval(tickSend);
  tickUI = null; tickSensors = null; tickSend = null;

  try { if (wakeLock) wakeLock.release(); } catch(_) {}
  wakeLock = null;

  if ($("btnStart")) $("btnStart").disabled = false;
  if ($("btnStop")) $("btnStop").disabled = true;
  if ($("btnUpload")) $("btnUpload").disabled = true;

  const st = updateUILive();
  updateSensorsBox(st);
}

window.addEventListener("load", () => {
  if ($("btnStart")) $("btnStart").addEventListener("click", startDrive);
  if ($("btnStop")) $("btnStop").addEventListener("click", stopDrive);
  if ($("btnUpload")) { $("btnUpload").disabled = true; $("btnUpload").title = "Auto-sending"; }

  const st = updateUILive();
  updateSensorsBox(st);
});
