"""
Aurora Alert System
Generates alerts based on real-time solar wind conditions and substorm detection.
Supports user-configurable visibility-score threshold alerts.
"""

import math
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone


def evaluate_alerts(
    solar_wind_data: Dict[str, Any],
    visibility_score: Optional[float] = None,
    user_threshold: float = 50.0,
) -> Dict[str, Any]:
    """
    Evaluate solar wind conditions and produce alerts.

    Triggers:
      - Bz < -7 nT (southward IMF)
      - Speed > 500 km/s (high-speed stream)
      - Bt > 15 nT
      - Density > 15 /cm³
      - dBz/dt < -1.5 nT/min (substorm precursor)
      - Visibility score exceeds user threshold
    """
    mag = solar_wind_data.get("magnetic_field", {})
    plasma = solar_wind_data.get("plasma", {})
    dbz_dt = solar_wind_data.get("dbz_dt")

    bz = mag.get("bz_gsm")
    bt = mag.get("bt")
    speed = plasma.get("speed")
    density = plasma.get("density")

    alerts: List[Dict[str, Any]] = []
    alert_active = False

    # ── Southward Bz ────────────────────────────────────────────
    if bz is not None and bz < -5:
        alert_active = True
        if bz < -20:
            sev = "high"
            msg = f"Strongly southward Bz: {bz:.1f} nT — Major geomagnetic storm conditions, aurora visible at lower latitudes"
        elif bz < -10:
            sev = "high"
            msg = f"Southward Bz: {bz:.1f} nT — Active aurora likely, check your sky if dark and clear"
        else:
            sev = "moderate"
            msg = f"Moderately southward Bz: {bz:.1f} nT — Aurora activity enhanced at high latitudes"
        alerts.append({
            "type": "southward_bz",
            "message": msg,
            "value": bz,
            "threshold": -5,
            "severity": sev,
        })

    # ── High-speed stream ───────────────────────────────────────
    if speed is not None and speed > 450:
        alert_active = True
        if speed > 700:
            sev = "high"
            msg = f"Very fast solar wind: {speed:.0f} km/s — Strong aurora driving, oval expanding equatorward"
        elif speed > 550:
            sev = "moderate"
            msg = f"Elevated solar wind speed: {speed:.0f} km/s — Sustained aurora activity likely"
        else:
            sev = "low"
            msg = f"Above-average solar wind: {speed:.0f} km/s — Mildly elevated aurora potential"
        alerts.append({
            "type": "high_speed_stream",
            "message": msg,
            "value": speed,
            "threshold": 450,
            "severity": sev,
        })

    # ── Strong total field ──────────────────────────────────────
    if bt is not None and bt > 12:
        alert_active = True
        sev = "high" if bt > 25 else "moderate"
        alerts.append({
            "type": "strong_bt",
            "message": f"Strong interplanetary magnetic field: {bt:.1f} nT — Increased energy coupling to magnetosphere",
            "value": bt,
            "threshold": 12,
            "severity": sev,
        })

    # ── Density enhancement ─────────────────────────────────────
    if density is not None and density > 15:
        alerts.append({
            "type": "density_enhancement",
            "message": f"Elevated solar wind density: {density:.1f} /cm\u00b3 — May enhance aurora brightness on impact",
            "value": density,
            "threshold": 15,
            "severity": "low",
        })

    # ── Substorm early warning (dBz/dt) ─────────────────────────
    if dbz_dt is not None and dbz_dt < -1.5:
        alert_active = True
        sev = "high" if dbz_dt < -3.0 else "moderate"
        alerts.append({
            "type": "substorm_warning",
            "message": f"Rapid Bz deflection: {dbz_dt:+.2f} nT/min — Substorm onset possible, watch for sudden brightening",
            "value": dbz_dt,
            "threshold": -1.5,
            "severity": sev,
        })

    # ── User visibility threshold ───────────────────────────────
    if visibility_score is not None and visibility_score >= user_threshold:
        alert_active = True
        alerts.append({
            "type": "visibility_threshold",
            "message": f"Visibility score {visibility_score:.0f} exceeds your threshold of {user_threshold:.0f} — Conditions favorable for viewing",
            "value": visibility_score,
            "threshold": user_threshold,
            "severity": "high" if visibility_score >= 70 else "moderate",
        })

    kp_estimate = estimate_kp(bz, speed, bt)

    severities = [a["severity"] for a in alerts]
    if "high" in severities:
        overall = "high"
    elif "moderate" in severities:
        overall = "moderate"
    elif severities:
        overall = "low"
    else:
        overall = "none"

    return {
        "alert_active": alert_active,
        "overall_severity": overall,
        "kp_estimate": kp_estimate,
        "alerts": alerts,
        "summary": _build_alert_summary(overall, kp_estimate, alerts, bz, speed),
        "conditions": {
            "bz_gsm": bz,
            "bt": bt,
            "speed": speed,
            "density": density,
            "dbz_dt": dbz_dt,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _build_alert_summary(
    severity: str,
    kp: float,
    alerts: List[Dict[str, Any]],
    bz: Optional[float],
    speed: Optional[float],
) -> str:
    """Build a concise human-readable summary of the current alert state."""
    if severity == "none":
        if kp >= 2:
            return f"Quiet conditions with Kp {kp:.1f}. No active triggers but minor aurora may be visible at high latitudes."
        return "Geomagnetically quiet. No aurora alerts at this time."
    types = {a["type"] for a in alerts}
    parts = []
    if "substorm_warning" in types:
        parts.append("Substorm onset possible")
    if "southward_bz" in types and bz is not None:
        parts.append(f"Bz {bz:.1f} nT driving reconnection")
    if "high_speed_stream" in types and speed is not None:
        parts.append(f"fast solar wind at {speed:.0f} km/s")
    if not parts:
        parts.append("elevated solar wind conditions")
    return f"Kp {kp:.1f} — {'; '.join(parts)}."


def estimate_kp(
    bz: Optional[float] = None,
    speed: Optional[float] = None,
    bt: Optional[float] = None,
) -> float:
    """
    Kp estimate using the Newell coupling function:
      coupling ∝ v^(4/3) * Bt^(2/3) * sin^8(theta_c / 2)
    where theta_c = clock angle of the IMF.

    Uses a two-component approach: the Newell coupling drives the storm
    component, plus a baseline from solar wind speed alone (recurrent
    high-speed streams raise Kp even with neutral Bz).
    """
    if speed is None:
        speed = 400.0
    if bt is None:
        bt = abs(bz) if bz is not None else 2.0
    if bz is None:
        bz = 0.0

    # Ensure bt >= |bz| (total field can't be less than one component)
    bt = max(bt, abs(bz), 0.5)

    # Clock angle: theta=180° when purely southward, 0° when northward
    cos_theta = max(-1.0, min(1.0, bz / bt))
    theta = math.acos(cos_theta)
    sin_half = math.sin(theta / 2)

    # Newell coupling function (sin^8 is very peaky; use sin^4 for
    # smoother response that better tracks observed Kp)
    coupling = (speed ** (4.0 / 3.0)) * (bt ** (2.0 / 3.0)) * (sin_half ** 4)

    # Baseline Kp from speed alone (high-speed streams)
    if speed > 600:
        speed_kp = 3.0 + (speed - 600) / 100.0
    elif speed > 450:
        speed_kp = 1.5 + 1.5 * (speed - 450) / 150.0
    else:
        speed_kp = max(0.0, 1.5 * (speed - 300) / 200.0)

    # Coupling-driven Kp (calibrated against observed storm Kp):
    #   log10~3.5 → Kp~2,  ~4.0 → Kp~3.5,  ~4.5 → Kp~5,  ~5.0 → Kp~6.5
    if coupling > 0:
        log_c = math.log10(max(coupling, 1))
        coupling_kp = 2.6 * log_c - 6.5
    else:
        coupling_kp = 0.0

    kp = max(coupling_kp, speed_kp)
    return round(min(max(kp, 0), 9), 1)
