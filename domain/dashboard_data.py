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


def _build_svg_area_path(points: list[tuple[float, float]], baseline_y: float) -> str:
    if not points:
        return ""
    line_path = _build_svg_smooth_path(points)
    first_x = points[0][0]
    last_x = points[-1][0]
    return f"{line_path} L {last_x:.1f},{baseline_y:.1f} L {first_x:.1f},{baseline_y:.1f} Z"


def _nice_number(value: float) -> float:
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    fraction = value / (10 ** exponent)
    if fraction <= 1:
        nice_fraction = 1
    elif fraction <= 2:
        nice_fraction = 2
    elif fraction <= 5:
        nice_fraction = 5
    else:
        nice_fraction = 10
    return nice_fraction * (10 ** exponent)


def _format_axis_value(value: float) -> int | float:
    if abs(value - round(value)) < 0.05:
        return int(round(value))
    return round(value, 1)


def _build_axis_ticks(
    min_value: float,
    max_value: float,
    *,
    max_ticks: int = 5,
    include_zero: bool = False,
) -> tuple[list[float], float, float]:
    if include_zero:
        min_value = min(0.0, min_value)
        max_value = max(0.0, max_value)
    if max_value <= min_value:
        max_value = min_value + 1.0

    target_slots = max(max_ticks - 1, 1)
    step = _nice_number((max_value - min_value) / target_slots)
    start = math.floor(min_value / step) * step
    end = math.ceil(max_value / step) * step
    ticks = []
    value = start
    for _ in range(max_ticks + 3):
        if value > end + (step * 0.5):
            break
        ticks.append(round(value, 6))
        value += step
    return ticks, start, end


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
    height = 248
    left_pad = 62
    right_pad = 58
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
    wind_only_values = [float(slot.get("wind_kts")) for slot in weather_timeline if slot.get("wind_kts") is not None]
    gust_only_values = [float(slot.get("gust_kts")) for slot in weather_timeline if slot.get("gust_kts") is not None]
    max_wind = max(wind_values, default=10.0)
    wind_ticks, _wind_floor, wind_ceiling = _build_axis_ticks(0.0, max(10.0, max_wind), max_ticks=5, include_zero=True)

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
    actual_temp_min = min(temp_values, default=0.0)
    actual_temp_max = max(temp_values, default=1.0)
    temp_ticks, temp_min, temp_max = _build_axis_ticks(
        actual_temp_min - 1.0,
        actual_temp_max + 1.0,
        max_ticks=5,
    )
    precip_ticks, _precip_floor, precip_ceiling = _build_axis_ticks(
        0.0,
        max(1.0, max(precip_values, default=0.0)),
        max_ticks=4,
        include_zero=True,
    )
    if precip_ceiling <= 1.0:
        precip_ticks = [0.0, 1.0]

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
        "left_pad": left_pad,
        "right_pad": right_pad,
        "top_pad": top_pad,
        "bottom_pad": bottom_pad,
        "wind": {
            "max_kts": round(wind_ceiling, 1),
            "summary": {
                "avg_wind_kts": round(sum(wind_only_values) / len(wind_only_values), 1) if wind_only_values else 0.0,
                "max_wind_kts": round(max(wind_only_values, default=0.0), 1),
                "max_gust_kts": round(max(gust_only_values, default=0.0), 1),
            },
            "wind_area_d": _build_svg_area_path(wind_points, height - bottom_pad),
            "wind_path_d": _build_svg_smooth_path(wind_points),
            "gust_path_d": _build_svg_smooth_path(gust_points),
            "samples": wind_samples,
            "day_dividers": dividers,
            "y_ticks": [
                {"value": _format_axis_value(value), "y": round(wind_to_y(value), 1)}
                for value in wind_ticks
            ],
        },
        "temp_precip": {
            "temp_min_c": _format_axis_value(temp_min),
            "temp_max_c": _format_axis_value(temp_max),
            "precip_max_mm": round(precip_ceiling, 1),
            "summary": {
                "temp_span_c": round(actual_temp_max - actual_temp_min, 1) if temp_values else 0.0,
                "precip_total_mm": round(sum(precip_values), 1),
                "wet_slots": sum(1 for value in precip_values if value > 0),
            },
            "temp_area_d": _build_svg_area_path(temp_points, height - bottom_pad),
            "temp_path_d": _build_svg_smooth_path(temp_points),
            "precip_bars": precip_bars,
            "samples": temp_samples,
            "day_dividers": dividers,
            "temp_ticks": [
                {"value": _format_axis_value(value), "y": round(temp_to_y(value), 1)}
                for value in temp_ticks
            ],
            "precip_ticks": [
                {"value": _format_axis_value(value), "y": round(height - bottom_pad - precip_to_height(value), 1)}
                for value in precip_ticks
            ],
        },
    }
