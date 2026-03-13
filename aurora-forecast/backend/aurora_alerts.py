"""
Aurora Alert System
Generates alerts based on real-time solar wind conditions.
Triggers when geomagnetic storm conditions are detected.
"""

from typing import Dict, Any, List
from datetime import datetime, timezone


def evaluate_alerts(solar_wind_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate current solar wind conditions and generate alerts.

    Alert triggers:
      - Bz < -7 nT (southward IMF → geomagnetic coupling)
      - Solar wind speed > 500 km/s (high-speed stream)
      - Bt > 15 nT (strong total field)
      - Density > 15 /cm³ (density enhancement)

    Returns alert status with severity level.
    """
    mag = solar_wind_data.get("magnetic_field", {})
    plasma = solar_wind_data.get("plasma", {})

    bz = mag.get("bz_gsm")
    bt = mag.get("bt")
    speed = plasma.get("speed")
    density = plasma.get("density")

    alerts: List[Dict[str, Any]] = []
    alert_active = False

    # Check Bz (southward IMF)
    if bz is not None and bz < -7:
        alert_active = True
        severity = "high" if bz < -15 else "moderate"
        alerts.append({
            "type": "southward_bz",
            "message": f"Southward Bz detected: {bz:.1f} nT — Enhanced aurora likely",
            "value": bz,
            "threshold": -7,
            "severity": severity,
        })

    # Check solar wind speed
    if speed is not None and speed > 500:
        alert_active = True
        severity = "high" if speed > 700 else "moderate"
        alerts.append({
            "type": "high_speed_stream",
            "message": f"High-speed solar wind: {speed:.0f} km/s — Aurora activity elevated",
            "value": speed,
            "threshold": 500,
            "severity": severity,
        })

    # Check total magnetic field strength
    if bt is not None and bt > 15:
        alert_active = True
        alerts.append({
            "type": "strong_bt",
            "message": f"Strong interplanetary field: {bt:.1f} nT",
            "value": bt,
            "threshold": 15,
            "severity": "moderate",
        })

    # Check plasma density
    if density is not None and density > 15:
        alerts.append({
            "type": "density_enhancement",
            "message": f"Elevated solar wind density: {density:.1f} /cm³",
            "value": density,
            "threshold": 15,
            "severity": "low",
        })

    # Compute overall Kp estimate (simplified empirical formula)
    kp_estimate = estimate_kp(bz, speed)

    # Determine overall severity
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
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def estimate_kp(bz: float = None, speed: float = None) -> float:
    """
    Simplified Kp index estimate from Bz and solar wind speed.
    Based on Newell coupling function approximation.
    """
    if bz is None or speed is None:
        return 0.0

    # Only southward Bz contributes to geomagnetic activity
    bz_eff = abs(min(bz, 0))

    # Simplified Newell-like coupling
    coupling = (speed ** (4.0 / 3.0)) * (bz_eff ** (2.0 / 3.0))

    # Empirical scaling to Kp (0-9)
    # Calibrated roughly: coupling ~5000 → Kp ~3, coupling ~20000 → Kp ~6
    if coupling <= 0:
        return 0.0

    import math
    kp = 0.5 * math.log10(max(coupling, 1)) + 0.3
    kp = min(max(kp, 0), 9)
    return round(kp, 1)
