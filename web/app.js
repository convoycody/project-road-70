/* Project Road 70 v0.1.0 – iPhone Safari web app (aggregates only) */

const $ = (id) => document.getElementById(id);

const API_KEY = ""; // optional later: set server env API_KEY + put same here
const BUCKET_SECONDS = 60;
const GRID_M = 250;

function clamp(x,a,b){ return Math.max(a, Math.min(b, x)); }
function mean(arr){ return arr.length ? arr.reduce((a,b)=>a+b,0)/arr.length : 0; }
function rms(arr){ return arr.length ? Math.sqrt(arr.reduce((a,b)=>a+b*b,0)/arr.length) : 0; }
function std(arr){
  if (arr.length < 2) return 0;
  const m = mean(arr);
  const v = mean(arr.map(x => (x-m)*(x-m)));
  return Math.sqrt(v);
}

function getNodeId() {
  const k = "pr70_node_id";
  let v = localStorage.getItem(k);
  if (!v) {
    v = "node_" + crypto.randomUUID();
    localStorage.setItem(k, v);
  }
  return v;
}
const NODE_ID = getNodeId();

function gridKeyFromLatLon(lat, lon, gridM) {
  const metersPerDegLat = 111320.0;
  const metersPerDegLon = 111320.0 * Math.cos(lat * Math.PI / 180.0);

  const xM = lon * metersPerDegLon;
  const yM = lat * metersPerDegLat;

  const gx = Math.floor(xM / gridM);
  const gy = Math.floor(yM / gridM);
  return `g${gridM}:x${gx}:y${gy}`;
}

function speedBandMph(mph) {
  if (!isFinite(mph)) return "UNK";
  const bands = [[0,10],[10,20],[20,30],[30,45],[45,60],[60,75],[75,90],[90,200]];
  for (const [a,b] of bands) if (mph >= a && mph < b) return `${a}-${b}`;
  return "90+";
}

function directionBinFromHeading(h) {
  if (!isFinite(h)) return "UNK";
  const dirs = ["N","NE","E","SE","S","SW","W","NW"];
  const idx = Math.round(((h % 360) / 45)) % 8;
  return dirs[idx];
}

function isoZ(d){ return d.toISOString().replace(".000Z","Z"); }

// ---- state ----
let running = false;
let motionListener = null;
let geoWatchId = null;

let lastLat = null, lastLon = null;
let lastSpeedMps = null, lastHeading = null;

let bucketStart = null;
let bucketSamples = 0;

let verticalAccel = [];
let rotMag = [];
let gravStability = [];
let shockCount = 0;

let uploadQueue = [];

async function requestMotionPermissionIfNeeded() {
  if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
    const res = await DeviceMotionEvent.requestPermission();
    if (res !== "granted") throw new Error("Motion permission not granted");
  }
}

function resetBucket(now) {
  bucketStart = new Date(Math.floor(now.getTime() / (BUCKET_SECONDS * 1000)) * BUCKET_SECONDS * 1000);
  bucketSamples = 0;
  verticalAccel = [];
  rotMag = [];
  gravStability = [];
  shockCount = 0;

  $("bStart").textContent = isoZ(bucketStart);
  $("samples").textContent = "0";
}

function computeConfidence() {
  const rotStd = std(rotMag);
  const gravStd = std(gravStability);

  const rotPenalty = clamp(rotStd / 30.0, 0, 1);
  const gravPenalty = clamp(gravStd / 1.5, 0, 1);

  const conf = 1.0 - (0.65 * rotPenalty + 0.35 * gravPenalty);
  return clamp(conf, 0, 1);
}

function computeRoughness() {
  return rms(verticalAccel);
}

function maybeFlushBucket(now) {
  if (!bucketStart) return;
  const end = bucketStart.getTime() + BUCKET_SECONDS * 1000;
  if (now.getTime() < end) return;

  const conf = computeConfidence();
  const rough = computeRoughness();
  const shocks = shockCount;

  const mph = (lastSpeedMps ?? 0) * 2.236936;
  const band = speedBandMph(mph);
  const dir = directionBinFromHeading(lastHeading);
  const gk = (lastLat != null && lastLon != null) ? gridKeyFromLatLon(lastLat, lastLon, GRID_M) : `g${GRID_M}:x0:y0`;

  const item = {
    bucket_start: isoZ(bucketStart),
    bucket_seconds: BUCKET_SECONDS,
    grid_key: gk,
    direction: dir,
    speed_band: band,
    road_roughness: Number(rough.toFixed(4)),
    shock_events: shocks,
    confidence: Number(conf.toFixed(4)),
    sample_count: bucketSamples
  };

  uploadQueue.push(item);
  $("queue").textContent = String(uploadQueue.length);
  $("btnUpload").disabled = uploadQueue.length === 0;

  resetBucket(now);
}

async function uploadNow() {
  if (!uploadQueue.length) return;

  const payload = { node_id: NODE_ID, items: uploadQueue };

  const res = await fetch("/v1/ingest/aggregates", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(API_KEY ? {"X-API-Key": API_KEY} : {})
    },
    body: JSON.stringify(payload)
  });

  const text = await res.text();
  $("lastUpload").textContent = `HTTP ${res.status}\n` + text;

  if (res.ok) {
    uploadQueue = [];
    $("queue").textContent = "0";
    $("btnUpload").disabled = true;
  }
}

function onMotion(e) {
  if (!running) return;
  const now = new Date();
  if (!bucketStart) resetBucket(now);

  const acc = e.acceleration || {};
  const accG = e.accelerationIncludingGravity || {};
  const rot = e.rotationRate || {};

  const ra = Number(rot.alpha ?? 0);
  const rb = Number(rot.beta ?? 0);
  const rg = Number(rot.gamma ?? 0);
  const rotM = Math.sqrt(ra*ra + rb*rb + rg*rg);
  rotMag.push(rotM);

  const magAccG = Math.sqrt((accG.x||0)**2 + (accG.y||0)**2 + (accG.z||0)**2);
  const magAcc = Math.sqrt((acc.x||0)**2 + (acc.y||0)**2 + (acc.z||0)**2);
  gravStability.push(Math.abs(magAccG - magAcc));

  let vz = 0;
  if (e.acceleration && (acc.z !== null && acc.z !== undefined)) {
    vz = Number(acc.z);
  } else {
    vz = Number(accG.z ?? 0) - 9.81;
  }
  verticalAccel.push(vz);

  if (Math.abs(vz) > 2.2) shockCount += 1;

  bucketSamples += 1;

  const conf = computeConfidence();
  const rough = computeRoughness();

  $("status").textContent = "Running";
  $("samples").textContent = String(bucketSamples);
  $("conf").textContent = conf.toFixed(2);
  $("rough").textContent = rough.toFixed(2);
  $("shocks").textContent = String(shockCount);

  maybeFlushBucket(now);
}

function startLocation() {
  geoWatchId = navigator.geolocation.watchPosition(
    (pos) => {
      lastLat = pos.coords.latitude;
      lastLon = pos.coords.longitude;
      lastHeading = pos.coords.heading;
      lastSpeedMps = pos.coords.speed;

      const mph = (lastSpeedMps ?? 0) * 2.236936;
      $("speed").textContent = isFinite(mph) ? `${mph.toFixed(1)} mph` : "—";
      $("gridKey").textContent = gridKeyFromLatLon(lastLat, lastLon, GRID_M);
    },
    (err) => console.warn("geo error", err),
    { enableHighAccuracy: true, maximumAge: 1000, timeout: 10000 }
  );
}

function stopLocation() {
  if (geoWatchId !== null) {
    navigator.geolocation.clearWatch(geoWatchId);
    geoWatchId = null;
  }
}

async function startDrive() {
  await requestMotionPermissionIfNeeded();
  running = true;

  $("btnStart").disabled = true;
  $("btnStop").disabled = false;
  $("status").textContent = "Starting…";

  bucketStart = null;

  motionListener = onMotion;
  window.addEventListener("devicemotion", motionListener, { passive: true });

  startLocation();
}

function stopDrive() {
  running = false;

  if (motionListener) {
    window.removeEventListener("devicemotion", motionListener);
    motionListener = null;
  }
  stopLocation();

  if (bucketStart && bucketSamples > 5) {
    const forced = new Date(bucketStart.getTime() + BUCKET_SECONDS * 1000 + 1);
    maybeFlushBucket(forced);
  }

  $("status").textContent = "Stopped";
  $("btnStart").disabled = false;
  $("btnStop").disabled = true;

  $("btnUpload").disabled = uploadQueue.length === 0;
}

function clearLocal() {
  uploadQueue = [];
  $("queue").textContent = "0";
  $("btnUpload").disabled = true;
  $("lastUpload").textContent = "Cleared local queue.";
}

$("btnStart").addEventListener("click", () => {
  startDrive().catch((e) => {
    $("status").textContent = "Permission denied / error";
    $("lastUpload").textContent = String(e);
    $("btnStart").disabled = false;
    $("btnStop").disabled = true;
  });
});

$("btnStop").addEventListener("click", () => stopDrive());
$("btnUpload").addEventListener("click", () => uploadNow().catch(err => $("lastUpload").textContent = String(err)));
$("btnClear").addEventListener("click", () => clearLocal());
