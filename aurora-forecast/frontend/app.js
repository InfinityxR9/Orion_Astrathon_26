/**
 * Aurora Forecast Platform — Frontend Application v2
 * Leaflet map + aurora heatmap + day/night terminator + Kp chart +
 * community sightings + WebSocket push + photography advisor.
 */

const API = window.location.origin;
const POLL_MS = 60000;

// ─── State ──────────────────────────────────────────────────────────────────

let map, heatLayer, terminatorLayer, nightOverlay;
let userMarker, clickMarker, sightingMarkers = [];
let selectedLat = 64.0, selectedLon = -21.0;
let ws = null;

// ─── Boot ───────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    initMap();
    loadAll();
    setInterval(loadAll, POLL_MS);
    connectWebSocket();

    document.getElementById("btn-locate").addEventListener("click", geolocate);
    document.getElementById("btn-report").addEventListener("click", openSightingModal);
    document.getElementById("btn-cancel-sighting").addEventListener("click", closeSightingModal);
    document.getElementById("btn-submit-sighting").addEventListener("click", submitSighting);
    document.getElementById("report-intensity").addEventListener("input", (e) => {
        document.getElementById("report-intensity-label").textContent = e.target.value;
    });
});

function loadAll() {
    loadAuroraGrid();
    loadSolarWind();
    loadAlerts();
    loadVisibility(selectedLat, selectedLon);
    loadTerminator();
    loadKpChart();
    loadSightings();
}

// ═══════════════════════════════════════════════════════════════════════════
// Map
// ═══════════════════════════════════════════════════════════════════════════

function initMap() {
    map = L.map("map", {
        center: [65, -20], zoom: 3, minZoom: 2, maxZoom: 10,
        zoomControl: true, worldCopyJump: true,
    });

    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
        subdomains: "abcd", maxZoom: 19,
    }).addTo(map);

    heatLayer = L.heatLayer([], {
        radius: 18, blur: 22, maxZoom: 6, max: 100,
        gradient: {
            0.0: "transparent", 0.1: "#003300", 0.25: "#00cc44",
            0.45: "#aaff00", 0.6: "#ffdd00", 0.75: "#ff8800",
            0.9: "#ff2200", 1.0: "#ff0044",
        },
    }).addTo(map);

    // Night-side polygon (filled behind terminator)
    nightOverlay = L.polygon([], {
        color: "transparent", fillColor: "#000011", fillOpacity: 0.35,
        interactive: false,
    }).addTo(map);

    // Terminator line
    terminatorLayer = L.polyline([], {
        color: "#ffcc00", weight: 1.5, opacity: 0.6, dashArray: "6,4",
        interactive: false,
    }).addTo(map);

    map.on("click", (e) => {
        selectedLat = Math.round(e.latlng.lat * 1000) / 1000;
        selectedLon = Math.round(e.latlng.lng * 1000) / 1000;
        setClickMarker(selectedLat, selectedLon);
        loadVisibility(selectedLat, selectedLon);
    });

    setClickMarker(selectedLat, selectedLon);
}

function setClickMarker(lat, lon) {
    if (clickMarker) {
        clickMarker.setLatLng([lat, lon]);
    } else {
        clickMarker = L.circleMarker([lat, lon], {
            radius: 8, color: "#00ffaa", fillColor: "#00ffaa",
            fillOpacity: 0.6, weight: 2,
        }).addTo(map);
    }
    clickMarker.bindPopup(`Checking visibility at<br>${lat.toFixed(2)}, ${lon.toFixed(2)}`);
}

// ═══════════════════════════════════════════════════════════════════════════
// Data loaders
// ═══════════════════════════════════════════════════════════════════════════

async function loadAuroraGrid() {
    try {
        const r = await fetch(`${API}/aurora-grid`);
        const d = await r.json();
        if (d.points && d.points.length) {
            heatLayer.setLatLngs(d.points.map(p => [p.lat, p.lon, p.prob]));
        }
        setOnline();
    } catch (e) { console.error("aurora-grid:", e); setOffline(); }
}

async function loadSolarWind() {
    try {
        const r = await fetch(`${API}/solar-wind`);
        const d = await r.json();
        const m = d.magnetic_field || {}, p = d.plasma || {};

        setText("sw-bz", fmtV(m.bz_gsm, " nT", 1));
        setText("sw-speed", fmtV(p.speed, " km/s", 0));
        setText("sw-density", fmtV(p.density, " /cm\u00b3", 1));
        setText("sw-bt", fmtV(m.bt, " nT", 1));
        setText("sw-dbz", d.dbz_dt != null ? d.dbz_dt.toFixed(2) + " nT/min" : "-- nT/min");

        // Show data source
        const src = d.source || {};
        setText("data-source", src.mag ? `Source: ${src.mag}` : "");

        const bzEl = document.getElementById("sw-bz");
        if (m.bz_gsm != null) {
            bzEl.classList.toggle("val-negative", m.bz_gsm < -5);
            bzEl.classList.toggle("val-positive", m.bz_gsm >= 0);
        }

        // Data-gap warning
        if (d.data_gap) {
            showToast("Data gap detected \u2014 solar wind readings may be stale", "warn");
        }
    } catch (e) { console.error("solar-wind:", e); }
}

async function loadAlerts() {
    try {
        const r = await fetch(`${API}/alerts`);
        const d = await r.json();
        const banner = document.getElementById("alert-banner");
        const msgs = document.getElementById("alert-messages");

        if (d.kp_estimate != null) setText("sw-kp", d.kp_estimate.toFixed(1));

        if (d.alert_active) {
            banner.classList.remove("hidden");
            banner.className = `alert-${d.overall_severity}`;
            msgs.innerHTML = d.alerts.map(a => `<div class="alert-msg">${a.message}</div>`).join("");
        } else {
            banner.classList.add("hidden");
        }
    } catch (e) { console.error("alerts:", e); }
}

async function loadVisibility(lat, lon) {
    try {
        setText("loc-lat", lat.toFixed(3));
        setText("loc-lon", lon.toFixed(3));
        const r = await fetch(`${API}/visibility-score?lat=${lat}&lon=${lon}`);
        const d = await r.json();
        updateScoreDisplay(d);
        updateTimestamp();
    } catch (e) { console.error("visibility:", e); }
}

async function loadTerminator() {
    try {
        const r = await fetch(`${API}/terminator`);
        const d = await r.json();
        if (!d.points || !d.points.length) return;
        const pts = d.points.map(p => [p.lat, p.lon]);
        terminatorLayer.setLatLngs(pts);

        // Determine which pole is in night by checking solar elevation at (90,0)
        // If midpoint of terminator line average lat > 0, sun is in northern hemisphere
        // → night polygon extends to south pole; else to north pole.
        const avgLat = pts.reduce((s, p) => s + p[0], 0) / pts.length;
        const nightPole = avgLat > 0 ? -90 : 90;

        // Build polygon: terminator line + close down to the night pole
        const nightPoly = [
            ...pts,
            [nightPole, pts[pts.length - 1][1]],
            [nightPole, pts[0][1]],
        ];
        nightOverlay.setLatLngs(nightPoly);
    } catch (e) { console.error("terminator:", e); }
}

async function loadKpChart() {
    try {
        const r = await fetch(`${API}/kp-timeline`);
        const d = await r.json();
        drawKpChart(d.history || []);
    } catch (e) { console.error("kp-timeline:", e); }
}

async function loadSightings() {
    try {
        const r = await fetch(`${API}/sightings`);
        const d = await r.json();
        renderSightings(d.sightings || []);
    } catch (e) { console.error("sightings:", e); }
}

// ═══════════════════════════════════════════════════════════════════════════
// UI updaters
// ═══════════════════════════════════════════════════════════════════════════

function updateScoreDisplay(d) {
    const score = d.visibility_score;
    const arc = document.getElementById("score-arc");
    const circ = 2 * Math.PI * 52;
    arc.style.strokeDasharray = circ;
    arc.style.strokeDashoffset = circ * (1 - score / 100);
    arc.style.stroke = scoreColor(score);

    setText("score-value", Math.round(score));
    setText("score-label", d.rating);
    document.getElementById("score-value").style.color = scoreColor(score);

    setBar("bar-aurora", "val-aurora", d.aurora_probability);
    setBar("bar-darkness", "val-darkness", d.darkness_score);
    setBar("bar-cloud", "val-cloud", d.cloud_score);

    // Weather & location details
    const w = d.weather || {};
    setText("loc-temp", w.temperature_c != null ? `${w.temperature_c}\u00b0C` : "--");
    setText("loc-cloud", w.cloud_cover_pct != null ? `${w.cloud_cover_pct}%` : "--");
    setText("loc-bortle", d.bortle_class != null ? `${d.bortle_class}` : "--");
    setText("loc-gmlat", d.geomagnetic_latitude != null ? `${d.geomagnetic_latitude}\u00b0` : "--");
    setText("loc-moon", d.moon_illumination_pct != null ? `${d.moon_illumination_pct}%` : "--");
    setText("loc-vis", w.visibility_km != null ? `${w.visibility_km} km` : "--");

    // Photography advisor
    const ph = d.photo_settings || {};
    setText("photo-iso", ph.iso || "--");
    setText("photo-aperture", ph.aperture || "--");
    setText("photo-shutter", ph.shutter_sec != null ? `${ph.shutter_sec}s` : "--");
    setText("photo-wb", ph.wb_kelvin != null ? `${ph.wb_kelvin}K` : "--");
    setText("photo-tip", ph.tip || "");
}

function setBar(barId, valId, value) {
    const bar = document.getElementById(barId);
    const val = document.getElementById(valId);
    if (bar && val) {
        const pct = Math.min(Math.max(value || 0, 0), 100);
        bar.style.width = `${pct}%`;
        bar.style.backgroundColor = scoreColor(pct);
        val.textContent = `${Math.round(pct)}`;
    }
}

function scoreColor(s) {
    if (s >= 70) return "#00ff88";
    if (s >= 50) return "#aaff00";
    if (s >= 30) return "#ffcc00";
    if (s >= 15) return "#ff8800";
    return "#ff3344";
}

function setOnline() {
    document.getElementById("status-indicator").className = "status-dot online";
    setText("status-text", "Live");
}
function setOffline() {
    document.getElementById("status-indicator").className = "status-dot offline";
    setText("status-text", "Offline");
}
function updateTimestamp() {
    setText("last-update", `Updated: ${new Date().toLocaleTimeString()}`);
}

// ═══════════════════════════════════════════════════════════════════════════
// Kp mini-chart (canvas)
// ═══════════════════════════════════════════════════════════════════════════

function drawKpChart(hist) {
    const c = document.getElementById("kp-canvas");
    if (!c) return;
    const ctx = c.getContext("2d");
    const W = c.width, H = c.height;
    ctx.clearRect(0, 0, W, H);

    if (hist.length < 2) {
        ctx.fillStyle = "#556680";
        ctx.font = "11px Inter, sans-serif";
        ctx.fillText("Collecting data...", 10, H / 2 + 4);
        return;
    }

    const maxKp = 9;
    const pts = hist.slice(-120); // last 2 h
    const dx = W / (pts.length - 1);

    // Grid lines
    ctx.strokeStyle = "#2a3650";
    ctx.lineWidth = 0.5;
    for (let k = 0; k <= maxKp; k += 3) {
        const y = H - (k / maxKp) * H;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
        ctx.fillStyle = "#556680"; ctx.font = "9px monospace";
        ctx.fillText(k, 2, y - 2);
    }

    // Kp line
    ctx.beginPath();
    ctx.strokeStyle = "#00ff88";
    ctx.lineWidth = 1.5;
    pts.forEach((p, i) => {
        const x = i * dx;
        const y = H - ((p.kp || 0) / maxKp) * H;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Kp >=5 danger zone
    ctx.beginPath();
    ctx.strokeStyle = "rgba(255,51,68,0.3)";
    ctx.lineWidth = 0.5;
    ctx.setLineDash([3, 3]);
    const y5 = H - (5 / maxKp) * H;
    ctx.moveTo(0, y5); ctx.lineTo(W, y5); ctx.stroke();
    ctx.setLineDash([]);
}

// ═══════════════════════════════════════════════════════════════════════════
// Community Sightings
// ═══════════════════════════════════════════════════════════════════════════

function renderSightings(sightings) {
    const list = document.getElementById("sightings-list");

    // Clear old markers
    sightingMarkers.forEach(m => map.removeLayer(m));
    sightingMarkers = [];

    if (!sightings.length) {
        list.innerHTML = '<p class="text-muted">No recent sightings.</p>';
        return;
    }

    list.innerHTML = sightings.slice(-8).reverse().map(s => {
        const t = new Date(s.timestamp).toLocaleTimeString();
        const stars = "\u2605".repeat(s.intensity) + "\u2606".repeat(5 - s.intensity);
        return `<div class="sighting-item"><span class="sighting-stars">${stars}</span> <span class="sighting-msg">${escHtml(s.message)}</span> <span class="sighting-time">${t}</span></div>`;
    }).join("");

    // Plot on map
    sightings.forEach(s => {
        const m = L.circleMarker([s.lat, s.lon], {
            radius: 5, color: "#ff88ff", fillColor: "#ff88ff",
            fillOpacity: 0.5, weight: 1,
        }).addTo(map);
        m.bindPopup(`<b>Sighting</b><br>${escHtml(s.message)}<br>Intensity: ${s.intensity}/5`);
        sightingMarkers.push(m);
    });
}

function openSightingModal() {
    document.getElementById("sighting-modal").classList.remove("hidden");
}
function closeSightingModal() {
    document.getElementById("sighting-modal").classList.add("hidden");
}

async function submitSighting() {
    const intensity = document.getElementById("report-intensity").value;
    const message = document.getElementById("report-message").value || "Aurora spotted!";
    try {
        const r = await fetch(`${API}/sightings?lat=${selectedLat}&lon=${selectedLon}&intensity=${intensity}&message=${encodeURIComponent(message)}`, { method: "POST" });
        if (r.ok) {
            showToast("Sighting reported!", "ok");
            closeSightingModal();
            loadSightings();
        }
    } catch (e) { showToast("Failed to submit sighting", "warn"); }
}

// ═══════════════════════════════════════════════════════════════════════════
// WebSocket
// ═══════════════════════════════════════════════════════════════════════════

function connectWebSocket() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (e) => {
        try {
            const d = JSON.parse(e.data);
            if (d.type === "update") {
                // Refresh solar-wind panel from push
                if (d.solar_wind) {
                    const m = d.solar_wind.magnetic_field || {};
                    const p = d.solar_wind.plasma || {};
                    setText("sw-bz", fmtV(m.bz_gsm, " nT", 1));
                    setText("sw-speed", fmtV(p.speed, " km/s", 0));
                    setText("sw-density", fmtV(p.density, " /cm\u00b3", 1));
                    setText("sw-bt", fmtV(m.bt, " nT", 1));
                }
                if (d.kp_latest != null) setText("sw-kp", d.kp_latest.toFixed(1));
                if (d.last_updated) {
                    const t = new Date(d.last_updated).toLocaleTimeString();
                    setText("last-update", `Updated: ${t}`);
                }
                setOnline();
            }
        } catch {}
    };
    ws.onclose = () => { setTimeout(connectWebSocket, 5000); };
    ws.onerror = () => { ws.close(); };
}

// ═══════════════════════════════════════════════════════════════════════════
// Geolocation
// ═══════════════════════════════════════════════════════════════════════════

function geolocate() {
    if (!navigator.geolocation) { showToast("Geolocation not supported", "warn"); return; }
    navigator.geolocation.getCurrentPosition(
        (pos) => {
            selectedLat = Math.round(pos.coords.latitude * 1000) / 1000;
            selectedLon = Math.round(pos.coords.longitude * 1000) / 1000;
            map.setView([selectedLat, selectedLon], 5);
            setClickMarker(selectedLat, selectedLon);
            if (userMarker) userMarker.setLatLng([selectedLat, selectedLon]);
            else {
                userMarker = L.marker([selectedLat, selectedLon]).addTo(map);
                userMarker.bindPopup("You are here").openPopup();
            }
            loadVisibility(selectedLat, selectedLon);
        },
        () => showToast("Unable to get location. Click map instead.", "warn")
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// Toast notifications
// ═══════════════════════════════════════════════════════════════════════════

function showToast(msg, type = "ok") {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.className = `toast toast-${type}`;
    t.classList.remove("hidden");
    setTimeout(() => t.classList.add("hidden"), 4000);
}

// ═══════════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════════

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}
function fmtV(val, suffix, dec) {
    if (val == null) return "--" + suffix;
    return val.toFixed(dec) + suffix;
}
function escHtml(s) {
    const d = document.createElement("div"); d.textContent = s; return d.innerHTML;
}
