from __future__ import annotations

import math
import re


def _build_svg_smooth_path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    if len(points) == 1:
        x, y = points[0]
        return f"M {x:.1f},{y:.1f}"
    commands = [f"M {points[0][0]:.1f},{points[0][1]:.1f}"]
    for index in range(len(points) - 1):
        p0 = points[index - 1] if index > 0 else points[index]
        p1 = points[index]
        p2 = points[index + 1]
        p3 = points[index + 2] if index + 2 < len(points) else p2
        cp1x = p1[0] + (p2[0] - p0[0]) / 6
        cp1y = p1[1] + (p2[1] - p0[1]) / 6
        cp2x = p2[0] - (p3[0] - p1[0]) / 6
        cp2y = p2[1] - (p3[1] - p1[1]) / 6
        commands.append(
            f"C {cp1x:.1f},{cp1y:.1f} {cp2x:.1f},{cp2y:.1f} {p2[0]:.1f},{p2[1]:.1f}"
        )
    return " ".join(commands)


def _build_chart_day_dividers(slots: list[dict]) -> list[dict]:
    dividers = []
    seen_dates = set()
    for slot in slots:
        date_key = slot.get("date")
        if not date_key or date_key in seen_dates:
            continue
        seen_dates.add(date_key)
        label = slot.get("date_label") or date_key
        if re.match(r"^\d{2}/\d{2}/\d{4}$", label):
            label = label[:5]
        dividers.append({
            "x": round(slot.get("x", 0.0), 1),
            "label": label,
        })
    return dividers


def build_weather_charts(weather_timeline: list[dict]) -> dict:
    if not weather_timeline:
        return {}

    width = 1180
    height = 228
    left_pad = 44
    right_pad = 28
    top_pad = 18
    bottom_pad = 34
    usable_width = width - left_pad - right_pad
    usable_height = height - top_pad - bottom_pad
    total_slots = max(len(weather_timeline) - 1, 1)

    def to_x(index: int) -> float:
        return left_pad + (index / total_slots) * usable_width

    wind_values = [
        float(value)
        for slot in weather_timeline
        for value in (slot.get("wind_kts"), slot.get("gust_kts"))
        if value is not None
    ]
    max_wind = max(wind_values, default=10.0)
    wind_ceiling = max(10.0, math.ceil(max_wind / 5.0) * 5.0)

    def wind_to_y(value: float | None) -> float:
        amount = float(value or 0.0)
        return height - bottom_pad - ((amount / wind_ceiling) * usable_height)

    wind_points = []
    gust_points = []
    wind_samples = []
    for index, slot in enumerate(weather_timeline):
        x = to_x(index)
        wind_y = wind_to_y(slot.get("wind_kts"))
        gust_y = wind_to_y(slot.get("gust_kts"))
        wind_points.append((x, wind_y))
        gust_points.append((x, gust_y))
        wind_samples.append({
            "x": round(x, 1),
            "date": slot.get("date"),
            "time_label": f"{slot.get('date_label')} {slot.get('time')}",
            "time": slot.get("time"),
            "date_label": slot.get("date_label"),
            "wind_kts": slot.get("wind_kts"),
            "gust_kts": slot.get("gust_kts"),
            "wind_dir": slot.get("wind_dir"),
            "wind_y": round(wind_y, 1),
            "gust_y": round(gust_y, 1),
        })

    temp_values = [float(slot.get("temp_c")) for slot in weather_timeline if slot.get("temp_c") is not None]
    precip_values = [float(slot.get("precip_mm") or 0.0) for slot in weather_timeline]
    temp_min = math.floor(min(temp_values, default=0.0) - 1.0)
    temp_max = math.ceil(max(temp_values, default=1.0) + 1.0)
    if temp_max - temp_min < 4:
        temp_max = temp_min + 4
    precip_ceiling = max(1.0, math.ceil(max(precip_values, default=0.0)))

    def temp_to_y(value: float | None) -> float:
        amount = float(value or 0.0)
        span = max(temp_max - temp_min, 1.0)
        return height - bottom_pad - (((amount - temp_min) / span) * usable_height)

    def precip_to_height(value: float | None) -> float:
        amount = float(value or 0.0)
        return (amount / precip_ceiling) * usable_height

    temp_points = []
    precip_bars = []
    temp_samples = []
    step_width = usable_width / max(len(weather_timeline), 1)
    bar_width = min(18.0, max(step_width * 0.56, 6.0))
    for index, slot in enumerate(weather_timeline):
        x = to_x(index)
        temp_y = temp_to_y(slot.get("temp_c"))
        precip_height = precip_to_height(slot.get("precip_mm"))
        precip_y = height - bottom_pad - precip_height
        temp_points.append((x, temp_y))
        precip_bars.append({
            "x": round(x - (bar_width / 2), 1),
            "y": round(precip_y, 1),
            "width": round(bar_width, 1),
            "height": round(precip_height, 1),
        })
        temp_samples.append({
            "x": round(x, 1),
            "date": slot.get("date"),
            "time_label": f"{slot.get('date_label')} {slot.get('time')}",
            "time": slot.get("time"),
            "date_label": slot.get("date_label"),
            "temp_c": slot.get("temp_c"),
            "precip_mm": slot.get("precip_mm"),
            "chance_of_rain": slot.get("chance_of_rain"),
            "temp_y": round(temp_y, 1),
            "precip_y": round(precip_y, 1),
            "precip_height": round(precip_height, 1),
            "bar_x": round(x - (bar_width / 2), 1),
            "bar_width": round(bar_width, 1),
        })

    dividers = _build_chart_day_dividers(wind_samples)

    return {
        "width": width,
        "height": height,
        "top_pad": top_pad,
        "bottom_pad": bottom_pad,
        "wind": {
            "max_kts": round(wind_ceiling, 1),
            "wind_path_d": _build_svg_smooth_path(wind_points),
            "gust_path_d": _build_svg_smooth_path(gust_points),
            "samples": wind_samples,
            "day_dividers": dividers,
            "y_ticks": [
                {"value": 0, "y": round(wind_to_y(0), 1)},
                {"value": round(wind_ceiling / 2, 1), "y": round(wind_to_y(wind_ceiling / 2), 1)},
                {"value": round(wind_ceiling, 1), "y": round(wind_to_y(wind_ceiling), 1)},
            ],
        },
        "temp_precip": {
            "temp_min_c": temp_min,
            "temp_max_c": temp_max,
            "precip_max_mm": round(precip_ceiling, 1),
            "temp_path_d": _build_svg_smooth_path(temp_points),
            "precip_bars": precip_bars,
            "samples": temp_samples,
            "day_dividers": dividers,
            "temp_ticks": [
                {"value": temp_min, "y": round(temp_to_y(temp_min), 1)},
                {"value": round((temp_min + temp_max) / 2, 1), "y": round(temp_to_y((temp_min + temp_max) / 2), 1)},
                {"value": temp_max, "y": round(temp_to_y(temp_max), 1)},
            ],
            "precip_ticks": [
                {"value": 0, "y": round(height - bottom_pad, 1)},
                {"value": round(precip_ceiling / 2, 1), "y": round(height - bottom_pad - precip_to_height(precip_ceiling / 2), 1)},
                {"value": round(precip_ceiling, 1), "y": round(height - bottom_pad - precip_to_height(precip_ceiling), 1)},
            ],
        },
    }
