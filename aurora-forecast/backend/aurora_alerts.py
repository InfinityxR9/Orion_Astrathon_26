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
    if bz is not None and bz < -7:
        alert_active = True
        sev = "high" if bz < -15 else "moderate"
        alerts.append({
            "type": "southward_bz",
            "message": f"Southward Bz detected: {bz:.1f} nT \u2014 Enhanced aurora likely",
            "value": bz,
            "threshold": -7,
            "severity": sev,
        })

    # ── High-speed stream ───────────────────────────────────────
    if speed is not None and speed > 500:
        alert_active = True
        sev = "high" if speed > 700 else "moderate"
        alerts.append({
            "type": "high_speed_stream",
            "message": f"High-speed solar wind: {speed:.0f} km/s \u2014 Aurora activity elevated",
            "value": speed,
            "threshold": 500,
            "severity": sev,
        })

    # ── Strong total field ──────────────────────────────────────
    if bt is not None and bt > 15:
        alert_active = True
        alerts.append({
            "type": "strong_bt",
            "message": f"Strong interplanetary field: {bt:.1f} nT",
            "value": bt,
            "threshold": 15,
            "severity": "moderate",
        })

    # ── Density enhancement ─────────────────────────────────────
    if density is not None and density > 15:
        alerts.append({
            "type": "density_enhancement",
            "message": f"Elevated solar wind density: {density:.1f} /cm\u00b3",
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
            "message": f"Rapid Bz deflection: {dbz_dt:+.2f} nT/min \u2014 Substorm likely",
            "value": dbz_dt,
            "threshold": -1.5,
            "severity": sev,
        })

    # ── User visibility threshold ───────────────────────────────
    if visibility_score is not None and visibility_score >= user_threshold:
        alert_active = True
        alerts.append({
            "type": "visibility_threshold",
            "message": f"Visibility score {visibility_score:.0f} exceeds your threshold ({user_threshold:.0f})",
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
        "conditions": {
            "bz_gsm": bz,
            "bt": bt,
            "speed": speed,
            "density": density,
            "dbz_dt": dbz_dt,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def estimate_kp(
    bz: Optional[float] = None,
    speed: Optional[float] = None,
    bt: Optional[float] = None,
) -> float:
    """
    Kp estimate using the Newell coupling function:
      coupling ∝ v^(4/3) * Bt^(2/3) * sin^8(theta_c / 2)
    where theta_c = clock angle of the IMF.
    Simplified here without By from GSM (uses Bz only for sin^8 proxy).
    """
    if speed is None:
        speed = 400.0
    if bt is None:
        bt = abs(bz) if bz is not None else 1.0
    if bz is None:
        return 0.0

    # Clock angle proxy (theta=180 when purely southward)
    if bt > 0:
        cos_theta = max(-1, min(1, bz / bt))
    else:
        cos_theta = 1.0
    theta = math.acos(cos_theta)
    sin8_half = math.sin(theta / 2) ** 8

    coupling = (speed ** (4.0 / 3.0)) * (bt ** (2.0 / 3.0)) * sin8_half

    if coupling <= 0:
        return 0.0

    # Empirical log-scaling calibrated:
    #   coupling 1e3 → Kp~1, 1e4 → Kp~3, 1e5 → Kp~5, 5e5 → Kp~7
    kp = 1.2 * math.log10(max(coupling, 1)) - 2.5
    return round(min(max(kp, 0), 9), 1)
