from __future__ import annotations
from matplotlib.figure import Figure
from analyzer import Report, THRESHOLDS

# Colors!!
SURFACE_COLOR = "#fcfcfb"
PRIMARY_INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
MUTED_INK = "#898781"
GRID_COLOR = "#e1e0d9"
SERIES_BLUE = "#2a78d6"
SERIES_ORANGE = "#eb6834"
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_CRITICAL = "#d03b3b"

BAR_HEIGHT = 0.55
MAX_AUTHOR_WORLDS_SHOWN = 8


# helper funcs
def _style_axes(axes) -> None:
    axes.set_facecolor(SURFACE_COLOR)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(GRID_COLOR)
    axes.tick_params(colors=MUTED_INK, length=0, labelsize=8)
    axes.xaxis.grid(True, color=GRID_COLOR, linewidth=0.8)
    axes.set_axisbelow(True)


def _set_title(axes, title: str) -> None:
    axes.set_title(title, loc="left", fontsize=11, color=PRIMARY_INK, pad=10)


def _shorten(text: str, max_length: int = 24) -> str:
    text = text or "?"
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


def _format_compact_number(number: float) -> str:
    #this is just a number formater lol
    number = float(number)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(int(number))


def _show_empty_message(axes, message: str) -> None:
    axes.text(0.5, 0.5, message, ha="center", va="center", color=MUTED_INK, transform=axes.transAxes)
    axes.set_xticks([])
    axes.set_yticks([])


# the stuffs
def _draw_risk_meter(axes, report: Report) -> None:
    safe_end = THRESHOLDS["safe_max"] + 1
    danger_start = THRESHOLDS["danger_min"]
    zones = [
        (0, safe_end, STATUS_GOOD, "Possibly safe"),
        (safe_end, danger_start, STATUS_WARNING, "Hmmmmm..."),
        (danger_start, 100, STATUS_CRITICAL, "Don't ffs!"),
    ]

    for zone_start, zone_end, zone_color, zone_label in zones:
        axes.barh(0, zone_end - zone_start, left=zone_start, height=0.5,
                  color=zone_color, edgecolor=SURFACE_COLOR, linewidth=2)
        # Text labels so you know what each means
        label_color = PRIMARY_INK if zone_color == STATUS_WARNING else "#ffffff"
        axes.text((zone_start + zone_end) / 2, 0, zone_label,
                  ha="center", va="center", fontsize=8, color=label_color)

    axes.plot([report.score], [0.25], marker="v", markersize=11, color=PRIMARY_INK,
              markeredgecolor=SURFACE_COLOR, clip_on=False)

    axes.set_xlim(0, 100)
    axes.set_ylim(-0.4, 0.7)
    axes.set_yticks([])
    axes.set_xticks([0, safe_end, danger_start, 100])
    axes.tick_params(colors=MUTED_INK, length=0, labelsize=8)
    for spine in axes.spines.values():
        spine.set_visible(False)
    axes.set_facecolor(SURFACE_COLOR)
    _set_title(axes, f"Risk score {report.score}/100")


def _draw_signal_breakdown(axes, report: Report) -> None:
    _style_axes(axes)
    _set_title(axes, "Why this score??")

    if not report.signals:
        _show_empty_message(axes, "No notable signals either way")
        return

    #Looks like I'm writting SQL here lmao
    signals_low_to_high = sorted(report.signals, key=lambda signal: signal.points)
    labels = [_shorten(signal.label, 34) for signal in signals_low_to_high]
    point_values = [signal.points for signal in signals_low_to_high]
    bar_colors = [STATUS_GOOD if points < 0 else STATUS_CRITICAL for points in point_values]
    row_positions = list(range(len(signals_low_to_high)))

    axes.barh(row_positions, point_values, color=bar_colors, height=BAR_HEIGHT, edgecolor=SURFACE_COLOR)
    axes.set_yticks(row_positions)
    axes.set_yticklabels(labels, fontsize=8, color=SECONDARY_INK)
    axes.axvline(0, color=MUTED_INK, linewidth=1)

    for row, points in zip(row_positions, point_values):
        is_positive = points > 0
        axes.annotate(f"{points:+d}", (points, row),
                      xytext=(4 if is_positive else -4, 0), textcoords="offset points",
                      ha="left" if is_positive else "right", va="center",
                      fontsize=8, color=SECONDARY_INK)

    lowest, highest = min(point_values + [0]), max(point_values + [0])
    padding = max(6, (highest - lowest) * 0.15)
    axes.set_xlim(lowest - padding, highest + padding)
    axes.set_xlabel("points (red raises risk, green lowers it)", fontsize=8, color=MUTED_INK)


def _worlds_to_compare(report: Report) -> list[dict]:
    #The author's most visited worlds, including the one being analyzed ofc.
    worlds = [world for world in report.author_worlds if world["visits"] > 0]

    is_current_world_listed = any(world["id"] == report.world_id for world in worlds)
    if not is_current_world_listed:
        worlds.append({
            "id": report.world_id,
            "name": report.name,
            "visits": report.stats["visits"],
            "favorites": report.stats["favorites"],
        })

    most_visited_first = sorted(worlds, key=lambda world: -world["visits"])[:MAX_AUTHOR_WORLDS_SHOWN]
    return [world for world in most_visited_first if world["visits"] > 0]


def _draw_author_comparison(axes, report: Report) -> None:
    _style_axes(axes)
    _set_title(axes, "Author's worlds: favs per 100 visits")

    worlds = _worlds_to_compare(report)
    if not worlds:
        _show_empty_message(axes, "No visit data available...")
        return

    worlds_top_to_bottom = worlds[::-1]  # barh draws the first item at the bottom
    favorites_per_100_visits = [100 * world["favorites"] / world["visits"] for world in worlds_top_to_bottom]
    labels = [
        f"{_shorten(world['name'], 22)} ({_format_compact_number(world['visits'])})"
        for world in worlds_top_to_bottom
    ]
    bar_colors = [
        SERIES_ORANGE if world["id"] == report.world_id else SERIES_BLUE
        for world in worlds_top_to_bottom
    ]
    row_positions = list(range(len(worlds_top_to_bottom)))

    axes.barh(row_positions, favorites_per_100_visits, color=bar_colors, height=BAR_HEIGHT, edgecolor=SURFACE_COLOR)
    axes.set_yticks(row_positions)
    axes.set_yticklabels(labels, fontsize=8, color=SECONDARY_INK)

    for row, value in zip(row_positions, favorites_per_100_visits):
        axes.annotate(f"{value:.1f}", (value, row), xytext=(4, 0), textcoords="offset points",
                      va="center", fontsize=8, color=SECONDARY_INK)

    axes.set_xlim(0, max(favorites_per_100_visits + [1]) * 1.15)
    axes.set_xlabel("favs per 100 visits (visits in brackets, orange = this world)",
                    fontsize=8, color=MUTED_INK)


# Alr, all data here? Gimme graph!
def build_figure(report: Report, width: float = 7.2, height: float = 8.4) -> Figure:
    figure = Figure(figsize=(width, height), dpi=100, facecolor=SURFACE_COLOR)
    grid = figure.add_gridspec(
        3, 1,
        height_ratios=[0.55, 2, 2.2], hspace=0.75,
        left=0.30, right=0.95, top=0.94, bottom=0.07,
    )

    _draw_risk_meter(figure.add_subplot(grid[0]), report)
    _draw_signal_breakdown(figure.add_subplot(grid[1]), report)
    _draw_author_comparison(figure.add_subplot(grid[2]), report)
    return figure
