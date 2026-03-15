/**
 * Aurora Forecast Platform - Frontend Application
 * Leaflet map + aurora heatmap + day/night terminator + Kp chart +
 * community sightings + WebSocket push + on-demand better spot search.
 */

const API = window.location.origin;
const POLL_MS = 60000;
const WORLD_WRAP_OFFSETS = [-360, 0, 360];
const MAP_VERTICAL_BOUNDS = [[-85, -720], [85, 720]];
const AURORA_OVERLAY_MIN_VISIBLE = 2;
const DEFAULT_BETTER_SPOT_STATUS =
    "Search nearby only when needed to find the nearest location with a meaningfully stronger visibility score.";

let map;
let heatLayer;
let terminatorLayers;
let nightOverlays;
let ws = null;

let userMarkers = [];
let clickMarkers = [];
let sightingMarkers = [];
let recommendationMarkers = [];
let routePreviewLayers = [];

let selectedLat = 64.0;
let selectedLon = -21.0;
let currentVisibilitySnapshot = null;
let betterSpotResult = null;
let betterSpotRequestToken = 0;

document.addEventListener("DOMContentLoaded", () => {
    initMap();
    resetBetterSpotState();
    loadAll();
    connectWebSocket();

    // PWA offline / online event listeners
    window.addEventListener("online", () => { setOnline(); loadAll(); });
    window.addEventListener("offline", () => setOffline());

    document.getElementById("btn-locate").addEventListener("click", geolocate);
    document.getElementById("btn-go-coordinates").addEventListener("click", goToCoordinates);
    document.getElementById("coord-lat").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            goToCoordinates();
        }
    });
    document.getElementById("coord-lon").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            goToCoordinates();
        }
    });
    document.getElementById("btn-better-spot").addEventListener("click", findBetterViewingSpot);
    document.getElementById("btn-route").addEventListener("click", getDirections);
    document.getElementById("btn-report").addEventListener("click", openSightingModal);
    document.getElementById("btn-cancel-sighting").addEventListener("click", closeSightingModal);
    document.getElementById("btn-submit-sighting").addEventListener("click", submitSighting);
    document.getElementById("report-intensity").addEventListener("input", (event) => {
        document.getElementById("report-intensity-label").textContent = event.target.value;
    });
    document.getElementById("better-radius").addEventListener("input", (event) => {
        document.getElementById("better-radius-label").textContent = `${event.target.value} km`;
    });
    document.getElementById("better-improvement").addEventListener("input", (event) => {
        document.getElementById("better-improvement-label").textContent = `+${event.target.value}`;
    });
});

async function loadAll() {
    await Promise.allSettled([
        loadAuroraGrid(),
        loadSolarWind(),
        loadAlerts(),
        loadVisibility(selectedLat, selectedLon),
        loadTerminator(),
        loadKpChart(),
        loadSightings(),
    ]);
    // Schedule the next poll only after the current one fully completes,
    // preventing overlapping requests when the server is slow.
    setTimeout(loadAll, POLL_MS);
}

function initMap() {
    map = L.map("map", {
        center: [65, -20],
        zoom: 3,
        minZoom: 2,
        maxZoom: 10,
        zoomControl: false,
        worldCopyJump: true,
        preferCanvas: true,
        maxBounds: MAP_VERTICAL_BOUNDS,
        maxBoundsViscosity: 1.0,
    });

    // Custom zoom buttons at vertical center of map - wired below after DOM elements exist
    document.getElementById("zoom-in").addEventListener("click", () => map.zoomIn());
    document.getElementById("zoom-out").addEventListener("click", () => map.zoomOut());

    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        attribution:
            '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
        subdomains: "abcd",
        maxZoom: 19,
        noWrap: false,
        keepBuffer: 8,
    }).addTo(map);

    heatLayer = L.heatLayer([], {
        radius: 13,
        blur: 11,
        minOpacity: 0.07,
        maxZoom: 7,
        max: 1,
        gradient: {
            0.0: "transparent",
            0.06: "rgba(8, 43, 49, 0.16)",
            0.14: "rgba(13, 85, 92, 0.34)",
            0.28: "#10a7a5",
            0.46: "#29ef9d",
            0.66: "#c9ff63",
            0.84: "#ffd36f",
            0.94: "#ff9950",
            1.0: "#ff5f83",
        },
    }).addTo(map);

    nightOverlays = WORLD_WRAP_OFFSETS.map(() =>
        L.polygon([], {
            color: "transparent",
            fillColor: "#000011",
            fillOpacity: 0.35,
            interactive: false,
            noClip: true,
        }).addTo(map)
    );

    terminatorLayers = WORLD_WRAP_OFFSETS.map(() =>
        L.polyline([], {
            color: "#ffcc00",
            weight: 1.5,
            opacity: 0.6,
            dashArray: "6,4",
            interactive: false,
            noClip: true,
        }).addTo(map)
    );

    map.on("click", (event) => {
        selectedLat = roundCoord(event.latlng.lat);
        selectedLon = roundCoord(event.latlng.lng);
        syncCoordinateInputs(selectedLat, selectedLon);
        setClickMarker(selectedLat, selectedLon);
        resetBetterSpotState();
        loadVisibility(selectedLat, selectedLon);
    });

    syncCoordinateInputs(selectedLat, selectedLon);
    setClickMarker(selectedLat, selectedLon);
}

function setClickMarker(lat, lon) {
    const displayLon = normalizeLongitude(lon);
    clickMarkers = syncWrappedMarkers(clickMarkers, lat, lon, createSelectedMarker);
    bindPopupToMarkerSet(
        clickMarkers,
        `Checking visibility at<br>${lat.toFixed(2)}, ${displayLon.toFixed(2)}`
    );
}

function setRecommendationMarker(recommendation) {
    const { lat, lon, visibility_score: score, distance_km: distance } = recommendation;
    const displayLon = normalizeLongitude(lon);
    recommendationMarkers = syncWrappedMarkers(
        recommendationMarkers,
        lat,
        lon,
        createRecommendationMarker
    );
    bindPopupToMarkerSet(
        recommendationMarkers,
        `<b>Suggested better spot</b><br>Score ${score.toFixed(1)}<br>${distance.toFixed(0)} km away<br>${lat.toFixed(2)}, ${displayLon.toFixed(2)}`
    );
}

function clearRecommendationMapLayers() {
    recommendationMarkers.forEach((marker) => map.removeLayer(marker));
    recommendationMarkers = [];
    clearRoutePreview();
}

async function loadAuroraGrid() {
    try {
        const data = await fetchJson(`${API}/aurora-grid`, "aurora-grid");
        if (data.points && data.points.length) {
            heatLayer.setLatLngs(buildWrappedHeatPoints(data.points));
        } else {
            heatLayer.setLatLngs([]);
            showToast("Aurora grid returned no active points right now.", "warn");
        }
        setOnline();
    } catch (error) {
        console.error("aurora-grid:", error);
        setOffline();
    }
}

async function loadSolarWind() {
    try {
        const data = await fetchJson(`${API}/solar-wind`, "solar-wind");
        const magnetic = data.magnetic_field || {};
        const plasma = data.plasma || {};

        setText("sw-bz", fmtV(magnetic.bz_gsm, " nT", 1));
        setText("sw-speed", fmtV(plasma.speed, " km/s", 0));
        setText("sw-density", fmtV(plasma.density, " /cm\u00b3", 1));
        setText("sw-bt", fmtV(magnetic.bt, " nT", 1));
        setText(
            "sw-dbz",
            data.dbz_dt != null ? `${data.dbz_dt.toFixed(2)} nT/min` : "-- nT/min"
        );

        const source = data.source || {};
        const sources = [source.mag, source.plasma].filter(Boolean);
        setText(
            "data-source",
            sources.length ? `NOAA ${[...new Set(sources)].join(" / ")}` : "NOAA feed"
        );
        setText("sw-summary", buildSolarWindSummary(data));

        const bzEl = document.getElementById("sw-bz");
        if (magnetic.bz_gsm != null) {
            bzEl.classList.toggle("val-negative", magnetic.bz_gsm < -5);
            bzEl.classList.toggle("val-positive", magnetic.bz_gsm >= 0);
        }

        if (data.data_gap) {
            showToast("Data gap detected - solar wind readings may be stale", "warn");
        }
    } catch (error) {
        console.error("solar-wind:", error);
    }
}

async function loadAlerts() {
    try {
        const data = await fetchJson(`${API}/alerts`, "alerts");
        renderAlerts(data);
    } catch (error) {
        console.error("alerts:", error);
    }
}

function renderAlerts(data) {
    const banner = document.getElementById("alert-banner");
    const messages = document.getElementById("alert-messages");
    const meta = document.getElementById("alert-meta");

    if (data.kp_estimate != null) {
        setText("sw-kp", data.kp_estimate.toFixed(1));
    }

    if (data.alert_active && data.alerts && data.alerts.length > 0) {
        banner.classList.remove("hidden");
        banner.className = `alert-banner alert-${data.overall_severity}`;
        setText("alert-title", `Kp ${data.kp_estimate != null ? data.kp_estimate.toFixed(1) : '--'} — ${capitalize(data.overall_severity)} Aurora Alert`);
        setText(
            "alert-meta",
            `${formatDashboardTime(data.timestamp)} | ${data.alerts.length} trigger${
                data.alerts.length === 1 ? "" : "s"
            }`
        );
        messages.innerHTML = data.alerts.map(renderAlertMessage).join("");
    } else {
        banner.classList.remove("hidden");
        banner.className = "alert-banner alert-quiet";
        setText("alert-title", "No Active Alerts");
        const summary = data.summary || "Geomagnetically quiet. No aurora alerts at this time.";
        meta.textContent = formatDashboardTime(data.timestamp || new Date().toISOString());
        messages.innerHTML = `<div class="alert-msg"><div class="alert-msg-title">${escHtml(summary)}</div></div>`;
    }
}

async function loadVisibility(lat, lon) {
    try {
        const normalizedLon = normalizeLongitude(lon);
        syncCoordinateInputs(lat, normalizedLon);
        setText("loc-lat", lat.toFixed(3));
        setText("loc-lon", normalizedLon.toFixed(3));
        setText(
            "location-focus",
            `Selected observation point at ${lat.toFixed(3)}, ${normalizedLon.toFixed(3)}. Local conditions refresh continuously for fast field decisions.`
        );
        const data = await fetchJson(
            `${API}/visibility-score?lat=${lat}&lon=${normalizedLon}`,
            "visibility-score"
        );
        currentVisibilitySnapshot = data;
        updateScoreDisplay(data);
        updateTimestamp(data.timestamp);
    } catch (error) {
        console.error("visibility:", error);
    }
}

async function loadTerminator() {
    try {
        const data = await fetchJson(`${API}/terminator`, "terminator");
        if (!data.points || !data.points.length) {
            return;
        }

        const pts = data.points.map((point) => [point.lat, point.lon]);

        // Determine night direction from sub-solar point.
        // Night pole is opposite to where the sun is: if sun is over
        // the northern hemisphere, night extends toward the south pole.
        const subSolarLat = data.sub_solar_lat || 0;
        const nightPole = subSolarLat >= 0 ? -90 : 90;

        WORLD_WRAP_OFFSETS.forEach((offset, idx) => {
            const shiftedPts = pts.map(([latValue, lonValue]) => [latValue, lonValue + offset]);
            terminatorLayers[idx].setLatLngs(shiftedPts);

            // Build a closed polygon from the terminator line to the night pole
            const nightPoly = [
                ...shiftedPts,
                [nightPole, shiftedPts[shiftedPts.length - 1][1]],
                [nightPole, shiftedPts[0][1]],
            ];
            nightOverlays[idx].setLatLngs(nightPoly);
        });
    } catch (error) {
        console.error("terminator:", error);
    }
}

async function loadKpChart() {
    try {
        const data = await fetchJson(`${API}/kp-timeline`, "kp-timeline");
        drawKpChart(data.history || []);
    } catch (error) {
        console.error("kp-timeline:", error);
    }
}

async function loadSightings() {
    try {
        const data = await fetchJson(`${API}/sightings`, "sightings");
        renderSightings(data.sightings || []);
    } catch (error) {
        console.error("sightings:", error);
    }
}

async function findBetterViewingSpot() {
    if (document.getElementById("btn-better-spot").disabled) {
        return;
    }

    const radius = Number(document.getElementById("better-radius").value || 180);
    const minImprovement = Number(document.getElementById("better-improvement").value || 15);
    const normalizedLon = normalizeLongitude(selectedLon);

    clearRecommendationMapLayers();
    betterSpotResult = null;
    document.getElementById("better-spot-result").classList.add("is-hidden");
    document.getElementById("btn-route").classList.add("is-hidden");
    document.getElementById("route-note").classList.add("is-hidden");
    setBetterSpotBusy(true);
    setText("better-spot-status", "Scanning nearby rings for a materially better viewing spot...");
    const requestToken = ++betterSpotRequestToken;
    const requestStarted = performance.now();

    try {
        const response = await fetch(
            `${API}/better-viewing-spot?lat=${selectedLat}&lon=${normalizedLon}&search_radius_km=${radius}&min_improvement=${minImprovement}`
        );
        if (!response.ok) {
            throw new Error(`better-viewing-spot failed (${response.status})`);
        }
        const data = await response.json();
        if (requestToken !== betterSpotRequestToken) {
            return;
        }
        data.client_elapsed_ms = Math.round(performance.now() - requestStarted);
        betterSpotResult = data;
        renderBetterSpotResult(data);

        if (data.found_better_spot && data.destination) {
            setRecommendationMarker(data.destination);
            showToast("Suggested nearby spot plotted on the map.", "ok");
        } else {
            showToast("No materially better nearby spot found.", "warn");
        }
    } catch (error) {
        console.error("better-viewing-spot:", error);
        if (requestToken === betterSpotRequestToken) {
            setText(
                "better-spot-status",
                "Unable to search nearby spots right now. Try again in a moment."
            );
            showToast("Failed to search nearby spots.", "warn");
        }
    } finally {
        if (requestToken === betterSpotRequestToken) {
            setBetterSpotBusy(false);
        }
    }
}

function updateScoreDisplay(data) {
    const score = data.visibility_score;
    const arc = document.getElementById("score-arc");
    const circumference = 2 * Math.PI * 52;
    arc.style.strokeDasharray = circumference;
    arc.style.strokeDashoffset = circumference * (1 - score / 100);
    arc.style.stroke = scoreColor(score);

    setText("score-value", Math.round(score));
    setText("score-label", data.rating);
    document.getElementById("score-value").style.color = scoreColor(score);
    updateScoreChip(score, data.rating);
    setText("score-summary", buildScoreSummary(data));

    setBar("bar-aurora", "val-aurora", data.aurora_probability);
    setBar("bar-darkness", "val-darkness", data.darkness_score);
    setBar("bar-cloud", "val-cloud", data.cloud_score);

    const weather = data.weather || {};
    setText("loc-temp", weather.temperature_c != null ? `${weather.temperature_c}\u00b0C` : "--");
    setText("loc-cloud", weather.cloud_cover_pct != null ? `${weather.cloud_cover_pct}%` : "--");
    setText("loc-bortle", data.bortle_class != null ? `${data.bortle_class}` : "--");
    setText(
        "loc-gmlat",
        data.geomagnetic_latitude != null ? `${data.geomagnetic_latitude}\u00b0` : "--"
    );
    setText(
        "loc-moon",
        data.moon_illumination_pct != null ? `${data.moon_illumination_pct}%` : "--"
    );
    setText("loc-vis", weather.visibility_km != null ? `${weather.visibility_km} km` : "--");

    const photo = data.photo_settings || {};
    setText("photo-iso", photo.iso || "--");
    setText("photo-aperture", photo.aperture || "--");
    setText("photo-shutter", photo.shutter_sec != null ? `${photo.shutter_sec}s` : "--");
    setText("photo-wb", photo.wb_kelvin != null ? `${photo.wb_kelvin}K` : "--");
    setText("photo-tip", photo.tip || "");
}

function renderBetterSpotResult(data) {
    const statusText = data.message || DEFAULT_BETTER_SPOT_STATUS;
    setText("better-spot-status", statusText);

    if (!data.found_better_spot || !data.destination) {
        document.getElementById("better-spot-result").classList.add("is-hidden");
        document.getElementById("btn-route").classList.add("is-hidden");
        document.getElementById("route-note").classList.add("is-hidden");

        // Show origin score context so the user knows why no spot was found
        if (data.origin) {
            const originScore = data.origin.visibility_score;
            const note = originScore >= 60
                ? `Your current score is ${formatScore(originScore)} — already competitive for viewing.`
                : `Your current score is ${formatScore(originScore)}. Nearby conditions are similarly limited.`;
            setText("better-spot-status", `${statusText} ${note}`);
        }
        return;
    }

    const destination = data.destination;
    document.getElementById("better-spot-result").classList.remove("is-hidden");
    document.getElementById("btn-route").classList.remove("is-hidden");
    document.getElementById("route-note").classList.remove("is-hidden");

    setText(
        "better-spot-meta",
        `${data.evaluated_candidates} full checks after ${data.screened_candidates} candidate screens | API ${Math.round(data.processing_ms || 0)} ms, client ${Math.round(data.client_elapsed_ms || 0)} ms`
    );
    setText("better-current-score", formatScore(data.origin.visibility_score));
    setText("better-destination-score", formatScore(destination.visibility_score));
    setText("better-improvement-value", `+${formatScore(destination.improvement)}`);
    setText("better-distance", `${formatDistance(destination.distance_km)} km`);
    setText(
        "better-direction",
        `${destination.direction} (${Math.round(destination.bearing_deg)}\u00b0)`
    );
    setText("better-lat", destination.lat.toFixed(3));
    setText("better-lon", destination.lon.toFixed(3));
    setText("better-aurora", `${formatScore(destination.aurora_probability)}%`);
    setText("better-spot-reason", destination.reason || "Better local viewing balance nearby.");

    const badge = document.getElementById("better-spot-badge");
    badge.textContent = `+${Math.round(destination.improvement)}`;
    badge.className = `card-chip ${destination.improvement >= 20 ? "chip-positive" : "chip-watch"}`;
}

function setBetterSpotBusy(isBusy) {
    const button = document.getElementById("btn-better-spot");
    button.disabled = isBusy;
    button.textContent = isBusy ? "Scanning Nearby Spots..." : "Find Better Viewing Spot";
    button.classList.toggle("is-busy", isBusy);
}

function resetBetterSpotState() {
    betterSpotRequestToken += 1;
    betterSpotResult = null;
    clearRecommendationMapLayers();
    document.getElementById("better-spot-result").classList.add("is-hidden");
    document.getElementById("btn-route").classList.add("is-hidden");
    document.getElementById("route-note").classList.add("is-hidden");
    setBetterSpotBusy(false);
    setText("better-spot-status", DEFAULT_BETTER_SPOT_STATUS);
}

function getDirections() {
    if (!betterSpotResult || !betterSpotResult.destination) {
        showToast("Find a better viewing spot first.", "warn");
        return;
    }

    clearRoutePreview();

    const originDisplayLon = selectedLon;
    const destinationDisplayLon = alignWrappedLongitude(
        betterSpotResult.destination.lon,
        originDisplayLon
    );
    const routePoints = buildRoutePreviewPoints(
        selectedLat,
        originDisplayLon,
        betterSpotResult.destination.lat,
        destinationDisplayLon
    );

    const glow = L.polyline(routePoints, {
        color: "rgba(94, 212, 255, 0.22)",
        weight: 10,
        opacity: 1,
        lineCap: "round",
        interactive: false,
        noClip: true,
    }).addTo(map);

    const route = L.polyline(routePoints, {
        color: "#7dfbe2",
        weight: 3,
        opacity: 0.95,
        dashArray: "10,8",
        lineCap: "round",
        interactive: false,
        noClip: true,
    }).addTo(map);

    routePreviewLayers = [glow, route];
    map.fitBounds(L.latLngBounds(routePoints).pad(0.35), {
        maxZoom: 8,
        animate: true,
    });

    window.open(
        buildDirectionsUrl(
            selectedLat,
            normalizeLongitude(selectedLon),
            betterSpotResult.destination.lat,
            betterSpotResult.destination.lon
        ),
        "_blank",
        "noopener"
    );
    showToast("Opened directions and drew a route preview.", "ok");
}

function clearRoutePreview() {
    routePreviewLayers.forEach((layer) => map.removeLayer(layer));
    routePreviewLayers = [];
}

function setBar(barId, valueId, value) {
    const bar = document.getElementById(barId);
    const valueEl = document.getElementById(valueId);
    if (!bar || !valueEl) {
        return;
    }

    const pct = Math.min(Math.max(value || 0, 0), 100);
    bar.style.width = `${pct}%`;
    bar.style.backgroundColor = scoreColor(pct);
    valueEl.textContent = `${Math.round(pct)}`;
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
    setText("status-text", "Live NOAA feed");
    _setOfflineBanner(false);
}

function setOffline() {
    document.getElementById("status-indicator").className = "status-dot offline";
    const label = navigator.onLine ? "Reconnecting" : "Offline \u2013 Cached Data";
    setText("status-text", label);
    _setOfflineBanner(!navigator.onLine);
}

function _setOfflineBanner(show) {
    const banner = document.getElementById("offline-banner");
    if (banner) banner.classList.toggle("hidden", !show);
}

function updateTimestamp(sourceTime = null) {
    const stamp = sourceTime ? new Date(sourceTime) : new Date();
    setText("last-update", `Updated ${formatDashboardTime(stamp)}`);
}

function drawKpChart(history) {
    const canvas = document.getElementById("kp-canvas");
    if (!canvas) {
        return;
    }

    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    if (history.length < 2) {
        ctx.fillStyle = "#556680";
        ctx.font = '11px "IBM Plex Sans", sans-serif';
        ctx.fillText("Collecting data...", 10, height / 2 + 4);
        return;
    }

    const maxKp = 9;
    const points = history.slice(-120);
    const dx = width / (points.length - 1);

    ctx.strokeStyle = "#2a3650";
    ctx.lineWidth = 0.5;
    for (let kp = 0; kp <= maxKp; kp += 3) {
        const y = height - (kp / maxKp) * height;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
        ctx.fillStyle = "#556680";
        ctx.font = "9px monospace";
        ctx.fillText(kp, 2, y - 2);
    }

    ctx.beginPath();
    ctx.strokeStyle = "#00ff88";
    ctx.lineWidth = 1.5;
    points.forEach((point, idx) => {
        const x = idx * dx;
        const y = height - ((point.kp || 0) / maxKp) * height;
        if (idx === 0) {
            ctx.moveTo(x, y);
        } else {
            ctx.lineTo(x, y);
        }
    });
    ctx.stroke();

    const y5 = height - (5 / maxKp) * height;
    ctx.beginPath();
    ctx.strokeStyle = "rgba(255, 51, 68, 0.3)";
    ctx.lineWidth = 0.5;
    ctx.setLineDash([3, 3]);
    ctx.moveTo(0, y5);
    ctx.lineTo(width, y5);
    ctx.stroke();
    ctx.setLineDash([]);
}

function renderSightings(sightings) {
    const list = document.getElementById("sightings-list");

    sightingMarkers.forEach((marker) => map.removeLayer(marker));
    sightingMarkers = [];

    if (!sightings.length) {
        list.innerHTML = '<p class="text-muted">No recent sightings.</p>';
        return;
    }

    // Show only the most recent 8 in the list
    const recent = sightings.slice(-8);

    list.innerHTML = recent
        .slice()
        .reverse()
        .map((sighting) => {
            const timestamp = new Date(sighting.timestamp).toLocaleTimeString();
            const stars =
                "\u2605".repeat(sighting.intensity) +
                "\u2606".repeat(5 - sighting.intensity);
            return `<div class="sighting-item"><span class="sighting-stars">${stars}</span> <span class="sighting-msg">${escHtml(
                sighting.message
            )}</span> <span class="sighting-time">${timestamp}</span></div>`;
        })
        .join("");

    // Only pin markers for the same visible set to avoid accumulating hidden pins
    recent.forEach((sighting) => {
        WORLD_WRAP_OFFSETS.forEach((offset) => {
            const marker = L.circleMarker([sighting.lat, sighting.lon + offset], {
                radius: 5,
                color: "#ff88ff",
                fillColor: "#ff88ff",
                fillOpacity: 0.5,
                weight: 1,
            }).addTo(map);
            marker.bindPopup(
                `<b>Sighting</b><br>${escHtml(sighting.message)}<br>Intensity: ${sighting.intensity}/5`
            );
            sightingMarkers.push(marker);
        });
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
    const normalizedLon = normalizeLongitude(selectedLon);

    try {
        const response = await fetch(
            `${API}/sightings?lat=${selectedLat}&lon=${normalizedLon}&intensity=${intensity}&message=${encodeURIComponent(
                message
            )}`,
            { method: "POST" }
        );
        if (response.ok) {
            showToast("Sighting reported!", "ok");
            closeSightingModal();
            loadSightings();
        }
    } catch (error) {
        showToast("Failed to submit sighting", "warn");
    }
}

function connectWebSocket() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type !== "update") {
                return;
            }

            if (data.solar_wind) {
                const magnetic = data.solar_wind.magnetic_field || {};
                const plasma = data.solar_wind.plasma || {};
                setText("sw-bz", fmtV(magnetic.bz_gsm, " nT", 1));
                setText("sw-speed", fmtV(plasma.speed, " km/s", 0));
                setText("sw-density", fmtV(plasma.density, " /cm\u00b3", 1));
                setText("sw-bt", fmtV(magnetic.bt, " nT", 1));
                setText("sw-summary", buildSolarWindSummary(data.solar_wind));
            }
            if (data.alerts) {
                renderAlerts(data.alerts);
            } else if (data.kp_latest != null) {
                setText("sw-kp", data.kp_latest.toFixed(1));
            }
            if (data.last_updated) {
                setText("last-update", `Updated ${formatDashboardTime(data.last_updated)}`);
            }
            setOnline();
        } catch (error) {
            console.error("ws:", error);
        }
    };
    ws.onclose = () => {
        setTimeout(connectWebSocket, 5000);
    };
    ws.onerror = () => {
        ws.close();
    };
}

function geolocate() {
    if (!navigator.geolocation) {
        showToast("Geolocation not supported", "warn");
        return;
    }

    navigator.geolocation.getCurrentPosition(
        (position) => {
            selectedLat = roundCoord(position.coords.latitude);
            selectedLon = roundCoord(position.coords.longitude);
            syncCoordinateInputs(selectedLat, selectedLon);
            map.setView([selectedLat, selectedLon], 5);
            setClickMarker(selectedLat, selectedLon);
            userMarkers = syncWrappedMarkers(userMarkers, selectedLat, selectedLon, createUserMarker);
            bindPopupToMarkerSet(userMarkers, "You are here");
            if (userMarkers[1]) {
                userMarkers[1].openPopup();
            }
            resetBetterSpotState();
            loadVisibility(selectedLat, selectedLon);
        },
        () => showToast("Unable to get location. Click map instead.", "warn")
    );
}

function goToCoordinates() {
    const latInput = document.getElementById("coord-lat");
    const lonInput = document.getElementById("coord-lon");
    const lat = Number(latInput.value);
    const lon = Number(lonInput.value);

    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        showToast("Enter valid numeric coordinates.", "warn");
        return;
    }
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
        showToast("Latitude must be -90 to 90 and longitude -180 to 180.", "warn");
        return;
    }

    selectedLat = roundCoord(lat);
    selectedLon = roundCoord(normalizeLongitude(lon));
    syncCoordinateInputs(selectedLat, selectedLon);
    map.setView([selectedLat, selectedLon], 5);
    setClickMarker(selectedLat, selectedLon);
    resetBetterSpotState();
    loadVisibility(selectedLat, selectedLon);
    showToast("Jumped to coordinates.", "ok");
}

function showToast(message, type = "ok") {
    const toast = document.getElementById("toast");
    toast.textContent = message;
    toast.className = `toast toast-${type}`;
    toast.classList.remove("hidden");
    setTimeout(() => toast.classList.add("hidden"), 4000);
}

function setText(id, text) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = text;
    }
}

function syncCoordinateInputs(lat, lon) {
    const latInput = document.getElementById("coord-lat");
    const lonInput = document.getElementById("coord-lon");
    if (latInput) {
        latInput.value = Number(lat).toFixed(3);
    }
    if (lonInput) {
        lonInput.value = Number(normalizeLongitude(lon)).toFixed(3);
    }
}

async function fetchJson(url, label) {
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`${label} request failed (${response.status})`);
    }
    const data = await response.json();
    if (data == null || typeof data !== "object") {
        throw new Error(`${label} response was not valid JSON object`);
    }
    return data;
}

function fmtV(value, suffix, decimals) {
    if (value == null) {
        return `--${suffix}`;
    }
    return `${Number(value).toFixed(decimals)}${suffix}`;
}

function escHtml(value) {
    const div = document.createElement("div");
    div.textContent = value;
    return div.innerHTML;
}

function buildWrappedHeatPoints(points) {
    return points.flatMap((point) => {
        const weight = heatValueToWeight(point.heat_value ?? point.prob);
        if (weight <= 0) {
            return [];
        }
        return WORLD_WRAP_OFFSETS.map((offset) => [point.lat, point.lon + offset, weight]);
    });
}

function normalizeLongitude(lon) {
    return ((lon + 180) % 360 + 360) % 360 - 180;
}

function heatValueToWeight(heatValue) {
    const value = Math.max(0, Math.min(heatValue || 0, 100));

    if (value < AURORA_OVERLAY_MIN_VISIBLE) {
        return 0;
    }
    if (value < 8) {
        return 0.08 + 0.14 * ((value - 2) / 6);
    }
    if (value < 18) {
        return 0.22 + 0.18 * ((value - 8) / 10);
    }
    if (value < 35) {
        return 0.40 + 0.28 * ((value - 18) / 17);
    }
    return 0.68 + 0.32 * Math.pow((value - 35) / 65, 0.82);
}

function syncWrappedMarkers(markerSet, lat, lon, factory) {
    return WORLD_WRAP_OFFSETS.map((offset, idx) => {
        let marker = markerSet[idx];
        if (!marker) {
            marker = factory();
            marker.addTo(map);
        }
        marker.setLatLng([lat, lon + offset]);
        return marker;
    });
}

function bindPopupToMarkerSet(markerSet, html) {
    markerSet.forEach((marker) => marker.bindPopup(html));
}

function createSelectedMarker() {
    return L.marker([0, 0], {
        icon: L.divIcon({
            className: "selected-point-icon",
            html: '<span class="marker-shell"><span class="marker-pulse"></span><span class="marker-core"></span></span>',
            iconSize: [34, 34],
            iconAnchor: [17, 17],
        }),
    });
}

function createUserMarker() {
    return L.marker([0, 0], {
        icon: L.divIcon({
            className: "user-location-icon",
            html: '<span class="user-marker-shell"><span class="user-marker-core"></span></span>',
            iconSize: [28, 28],
            iconAnchor: [14, 14],
        }),
    });
}

function createRecommendationMarker() {
    return L.marker([0, 0], {
        icon: L.divIcon({
            className: "recommendation-point-icon",
            html: '<span class="recommendation-shell"><span class="recommendation-halo"></span><span class="recommendation-core"></span></span>',
            iconSize: [32, 32],
            iconAnchor: [16, 16],
        }),
    });
}

function updateScoreChip(score, rating) {
    const chip = document.getElementById("score-chip");
    chip.textContent = rating;
    chip.className = `card-chip ${scoreChipClass(score)}`;
}

function scoreChipClass(score) {
    if (score >= 70) return "chip-positive";
    if (score >= 45) return "chip-watch";
    return "chip-caution";
}

function buildScoreSummary(data) {
    const aurora = data.aurora_probability ?? 0;
    const darkness = data.darkness_score ?? 0;
    const cloud = data.cloud_score ?? 0;
    const score = data.visibility_score ?? 0;
    const lookup = data.aurora_lookup || {};
    const nearestDistance = lookup.nearest_distance_deg;
    const cutoff = lookup.distance_cutoff_deg;

    if (aurora <= 0 && nearestDistance != null && cutoff != null && nearestDistance > cutoff) {
        return `No OVATION aurora cell is within ${cutoff.toFixed(1)} deg of this point (nearest is ${nearestDistance.toFixed(1)} deg), so local aurora probability is zero right now.`;
    }

    if (aurora < 5) {
        return "No significant aurora probability at this location. The OVATION model shows minimal geomagnetic activity here. Try a location closer to the auroral oval.";
    }
    if (aurora < 20 && score < 30) {
        return "Aurora probability is weak here. Dark skies and clear weather alone cannot produce a high score — aurora signal must be present first.";
    }
    if (!data.is_dark && darkness < 20) {
        return "It is currently too bright at this location (sun above horizon or civil twilight). Aurora viewing requires astronomical darkness. Check back after sunset.";
    }
    if (cloud < 25) {
        return "Cloud obstruction is severely limiting visibility. Aurora signal is present, but you need clearer skies. Try the 'Better Viewing Spot' search for a less cloudy site nearby.";
    }
    if (darkness < 35 && aurora >= 20) {
        return "Aurora signal is present, but twilight, moonlight, or light pollution is reducing contrast. Find a darker location or wait for deeper darkness.";
    }
    if (score >= 75) {
        return "Excellent viewing conditions. Strong aurora probability combined with dark skies and clear weather — head outside now if you can!";
    }
    if (score >= 55) {
        return "Good conditions for aurora viewing. The score reflects a solid combination of aurora activity, darkness, and sky clarity.";
    }
    if (score >= 35) {
        return "Moderate conditions. Aurora is detectable but one or more factors (cloud, brightness, or weak aurora) is holding the score back. Photography may still capture it.";
    }
    return "Marginal conditions. The combined aurora signal is limited. Monitor the Kp chart for sudden increases, or search nearby for a better spot.";
}

function buildSolarWindSummary(data) {
    const magnetic = data.magnetic_field || {};
    const plasma = data.plasma || {};
    const bz = magnetic.bz_gsm;
    const speed = plasma.speed;

    if (data.data_gap) {
        return "Telemetry gap detected. Treat the current nowcast conservatively until fresh solar wind data arrives.";
    }
    if (bz != null && bz < -7 && speed != null && speed > 500) {
        return "Strong southward IMF and elevated solar wind speed are both supporting auroral expansion right now.";
    }
    if (bz != null && bz < -7) {
        return "Bz is strongly southward, which favors magnetic reconnection and rapid auroral brightening.";
    }
    if (speed != null && speed > 500) {
        return "Solar wind is fast, but local viewing still depends heavily on OVATION probability, darkness, and cloud clarity.";
    }
    return "Solar wind is relatively quiet, so local darkness and cloud conditions will dominate the visibility score.";
}

function renderAlertMessage(alert) {
    const current = formatAlertValue(alert.type, alert.value);
    const threshold = formatAlertThreshold(alert);
    return `
        <div class="alert-msg">
            <div class="alert-msg-title">${escHtml(alert.message)}</div>
            <div class="alert-msg-meta">Now ${current}${threshold ? ` | trigger ${threshold}` : ""}</div>
        </div>
    `;
}

function formatAlertThreshold(alert) {
    const threshold = formatAlertValue(alert.type, alert.threshold);
    if (!threshold) {
        return "";
    }
    if (alert.type === "southward_bz" || alert.type === "substorm_warning") {
        return `< ${threshold}`;
    }
    return `>= ${threshold}`;
}

function formatAlertValue(type, value) {
    if (value == null) {
        return "";
    }
    if (type === "southward_bz" || type === "strong_bt") {
        return `${Number(value).toFixed(1)} nT`;
    }
    if (type === "high_speed_stream") {
        return `${Math.round(Number(value))} km/s`;
    }
    if (type === "density_enhancement") {
        return `${Number(value).toFixed(1)} /cm^3`;
    }
    if (type === "substorm_warning") {
        return `${Number(value).toFixed(2)} nT/min`;
    }
    if (type === "visibility_threshold") {
        return `${Math.round(Number(value))} score`;
    }
    return `${value}`;
}

function formatDashboardTime(value) {
    const date = value instanceof Date ? value : new Date(value);
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function capitalize(value) {
    return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

function roundCoord(value) {
    return Math.round(value * 1000) / 1000;
}

function formatScore(value) {
    return Number(value).toFixed(1);
}

function formatDistance(value) {
    if (value >= 100) {
        return Math.round(value);
    }
    return Number(value).toFixed(1);
}

function alignWrappedLongitude(targetLon, referenceLon) {
    let aligned = targetLon;
    while (aligned - referenceLon > 180) {
        aligned -= 360;
    }
    while (aligned - referenceLon < -180) {
        aligned += 360;
    }
    return aligned;
}

function buildRoutePreviewPoints(startLat, startLon, endLat, endLon, segments = 24) {
    return Array.from({ length: segments + 1 }, (_, idx) => {
        const t = idx / segments;
        return [
            startLat + (endLat - startLat) * t,
            startLon + (endLon - startLon) * t,
        ];
    });
}

function buildDirectionsUrl(originLat, originLon, destinationLat, destinationLon) {
    return (
        "https://www.google.com/maps/dir/?api=1" +
        `&origin=${originLat},${originLon}` +
        `&destination=${destinationLat},${destinationLon}` +
        "&travelmode=driving"
    );
}
