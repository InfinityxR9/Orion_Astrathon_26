/**
 * Aurora Forecast Platform — Frontend Application
 * Leaflet map with aurora heatmap overlay + visibility scoring
 */

const API_BASE = window.location.origin;
const POLL_INTERVAL = 60000; // 60 seconds

// ─── State ──────────────────────────────────────────────────────────────────

let map;
let heatLayer;
let userMarker;
let clickMarker;
let selectedLat = 64.0;
let selectedLon = -21.0;

// ─── Initialization ─────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    initMap();
    loadAuroraGrid();
    loadSolarWind();
    loadAlerts();
    loadVisibility(selectedLat, selectedLon);

    // Auto-refresh
    setInterval(() => {
        loadAuroraGrid();
        loadSolarWind();
        loadAlerts();
        loadVisibility(selectedLat, selectedLon);
    }, POLL_INTERVAL);

    // Locate button
    document.getElementById("btn-locate").addEventListener("click", geolocate);
});

// ─── Map Setup ──────────────────────────────────────────────────────────────

function initMap() {
    map = L.map("map", {
        center: [65, -20],
        zoom: 3,
        minZoom: 2,
        maxZoom: 10,
        zoomControl: true,
        worldCopyJump: true,
    });

    // Dark tile layer for night-time readability
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
        subdomains: "abcd",
        maxZoom: 19,
    }).addTo(map);

    // Initialize empty heat layer
    heatLayer = L.heatLayer([], {
        radius: 18,
        blur: 22,
        maxZoom: 6,
        max: 100,
        gradient: {
            0.0: "transparent",
            0.1: "#003300",
            0.25: "#00cc44",
            0.45: "#aaff00",
            0.6: "#ffdd00",
            0.75: "#ff8800",
            0.9: "#ff2200",
            1.0: "#ff0044",
        },
    }).addTo(map);

    // Click to check visibility
    map.on("click", (e) => {
        selectedLat = Math.round(e.latlng.lat * 1000) / 1000;
        selectedLon = Math.round(e.latlng.lng * 1000) / 1000;
        setClickMarker(selectedLat, selectedLon);
        loadVisibility(selectedLat, selectedLon);
    });

    // Default marker
    setClickMarker(selectedLat, selectedLon);
}

function setClickMarker(lat, lon) {
    if (clickMarker) {
        clickMarker.setLatLng([lat, lon]);
    } else {
        clickMarker = L.circleMarker([lat, lon], {
            radius: 8,
            color: "#00ffaa",
            fillColor: "#00ffaa",
            fillOpacity: 0.6,
            weight: 2,
        }).addTo(map);
    }
    clickMarker.bindPopup(`Checking visibility at<br>${lat.toFixed(2)}, ${lon.toFixed(2)}`);
}

// ─── Data Loaders ───────────────────────────────────────────────────────────

async function loadAuroraGrid() {
    try {
        const resp = await fetch(`${API_BASE}/aurora-grid`);
        const data = await resp.json();
        if (data.points && data.points.length > 0) {
            const heatData = data.points.map((p) => [p.lat, p.lon, p.prob]);
            heatLayer.setLatLngs(heatData);
        }
        setOnline();
    } catch (err) {
        console.error("Failed to load aurora grid:", err);
        setOffline();
    }
}

async function loadSolarWind() {
    try {
        const resp = await fetch(`${API_BASE}/solar-wind`);
        const data = await resp.json();
        const mag = data.magnetic_field || {};
        const plasma = data.plasma || {};

        setText("sw-bz", formatVal(mag.bz_gsm, " nT", 1));
        setText("sw-speed", formatVal(plasma.speed, " km/s", 0));
        setText("sw-density", formatVal(plasma.density, " /cm\u00b3", 1));
        setText("sw-bt", formatVal(mag.bt, " nT", 1));

        // Color Bz based on value
        const bzEl = document.getElementById("sw-bz");
        if (mag.bz_gsm !== null && mag.bz_gsm !== undefined) {
            bzEl.classList.toggle("val-negative", mag.bz_gsm < -5);
            bzEl.classList.toggle("val-positive", mag.bz_gsm >= 0);
        }
    } catch (err) {
        console.error("Failed to load solar wind:", err);
    }
}

async function loadAlerts() {
    try {
        const resp = await fetch(`${API_BASE}/alerts`);
        const data = await resp.json();
        const banner = document.getElementById("alert-banner");
        const msgs = document.getElementById("alert-messages");

        if (data.kp_estimate !== undefined) {
            setText("sw-kp", data.kp_estimate.toFixed(1));
        }

        if (data.alert_active) {
            banner.classList.remove("hidden");
            banner.className = `alert-${data.overall_severity}`;
            msgs.innerHTML = data.alerts
                .map((a) => `<div class="alert-msg">${a.message}</div>`)
                .join("");
        } else {
            banner.classList.add("hidden");
        }
    } catch (err) {
        console.error("Failed to load alerts:", err);
    }
}

async function loadVisibility(lat, lon) {
    try {
        setText("loc-lat", lat.toFixed(3));
        setText("loc-lon", lon.toFixed(3));

        const resp = await fetch(`${API_BASE}/visibility-score?lat=${lat}&lon=${lon}`);
        const data = await resp.json();

        updateScoreDisplay(data);
        updateTimestamp();
    } catch (err) {
        console.error("Failed to load visibility:", err);
    }
}

// ─── UI Updaters ────────────────────────────────────────────────────────────

function updateScoreDisplay(data) {
    const score = data.visibility_score;
    const rating = data.rating;

    // Score ring animation
    const arc = document.getElementById("score-arc");
    const circumference = 2 * Math.PI * 52;
    arc.style.strokeDasharray = circumference;
    arc.style.strokeDashoffset = circumference * (1 - score / 100);
    arc.style.stroke = scoreColor(score);

    setText("score-value", Math.round(score));
    setText("score-label", rating);

    document.getElementById("score-value").style.color = scoreColor(score);

    // Breakdown bars
    setBar("bar-aurora", "val-aurora", data.aurora_probability);
    setBar("bar-darkness", "val-darkness", data.darkness_score);
    setBar("bar-cloud", "val-cloud", data.cloud_score);

    // Weather info
    const weather = data.weather || {};
    setText("loc-temp", weather.temperature_c !== null && weather.temperature_c !== undefined
        ? `${weather.temperature_c}\u00b0C` : "--");
    setText("loc-cloud", weather.cloud_cover_pct !== null && weather.cloud_cover_pct !== undefined
        ? `${weather.cloud_cover_pct}%` : "--");
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

function scoreColor(score) {
    if (score >= 70) return "#00ff88";
    if (score >= 50) return "#aaff00";
    if (score >= 30) return "#ffcc00";
    if (score >= 15) return "#ff8800";
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
    const now = new Date();
    setText("last-update", `Updated: ${now.toLocaleTimeString()}`);
}

// ─── Geolocation ────────────────────────────────────────────────────────────

function geolocate() {
    if (!navigator.geolocation) {
        alert("Geolocation is not supported by your browser.");
        return;
    }
    navigator.geolocation.getCurrentPosition(
        (pos) => {
            selectedLat = Math.round(pos.coords.latitude * 1000) / 1000;
            selectedLon = Math.round(pos.coords.longitude * 1000) / 1000;
            map.setView([selectedLat, selectedLon], 5);
            setClickMarker(selectedLat, selectedLon);

            if (userMarker) {
                userMarker.setLatLng([selectedLat, selectedLon]);
            } else {
                userMarker = L.marker([selectedLat, selectedLon]).addTo(map);
                userMarker.bindPopup("You are here").openPopup();
            }

            loadVisibility(selectedLat, selectedLon);
        },
        () => {
            alert("Unable to get your location. Click on the map to select a location.");
        }
    );
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function formatVal(val, suffix, decimals) {
    if (val === null || val === undefined) return "--" + suffix;
    return val.toFixed(decimals) + suffix;
}
