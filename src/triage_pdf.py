"""Generate a triage sheet PDF from a TriageSchedule dataclass."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from src.triage_scraper import TriageSchedule, TriageShift

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_W_MM = 215.9
PAGE_H_MM = 279.4
MARGIN_MM = 15.0
TABLE_W_MM = PAGE_W_MM - 2 * MARGIN_MM  # 185.9mm

COL_WIDTHS = [28.0, 32.0, 65.0, 60.9]  # Shift, Time, Provider(s), Responsibilities
COL_HEADERS = ["Shift", "Time", "Provider(s)", "Responsibilities"]

LINE_H = 5  # mm per text line inside cells

# RGB tuples
COLOR_HEADER_BG = (232, 232, 232)   # #E8E8E8
COLOR_ROW_WHITE = (255, 255, 255)   # #FFFFFF
COLOR_ROW_GRAY  = (245, 245, 245)   # #F5F5F5
COLOR_RULE_GRAY = (170, 170, 170)   # #AAAAAA


# ---------------------------------------------------------------------------
# Time parsing / sorting helpers
# ---------------------------------------------------------------------------

def _parse_time(raw: str) -> datetime | None:
    """Parse a time string like '7:00am' or '10:30pm' into a datetime for sorting.

    Returns None if the string is empty or unparseable.
    """
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    for fmt in ("%I:%M%p", "%I%p"):
        try:
            return datetime.strptime(raw.upper(), fmt)
        except ValueError:
            continue
    return None


def _sort_key(shift: TriageShift) -> tuple[int, datetime]:
    """Sort key: prior-day T3 first, main shifts next, next-day T1 last."""
    if shift.is_prior_day_t3:
        group = 0
    elif shift.is_next_day_t1:
        group = 2
    else:
        group = 1
    parsed = _parse_time(shift.start_time)
    if parsed is None:
        parsed = datetime(2000, 1, 1, 23, 59, 59)
    return (group, parsed)


# ---------------------------------------------------------------------------
# FPDF drawing helpers
# ---------------------------------------------------------------------------

def _draw_row(
    pdf: FPDF,
    texts: list[str],
    widths: list[float],
    bg_color: tuple[int, int, int],
    border: str = "1",
) -> None:
    """Draw a single table row with multi-line support.

    All cells in the row share the same height (sized to the tallest cell).
    """
    # Guard against page break mid-row: if there's not even one line's worth of
    # space left, force a new page before we capture y0.
    if pdf.get_y() + LINE_H > pdf.h - pdf.b_margin:
        pdf.add_page()

    x0 = pdf.l_margin

    # Measure the height needed for each cell to determine the row height
    max_lines = 1
    for text, w in zip(texts, widths):
        lines = pdf.multi_cell(w, LINE_H, text, dry_run=True, output="LINES")
        max_lines = max(max_lines, len(lines))
    row_h = max_lines * LINE_H

    # If the full row won't fit, move to a new page before drawing
    if pdf.get_y() + row_h > pdf.h - pdf.b_margin:
        pdf.add_page()

    y0 = pdf.get_y()

    # Draw each cell at the correct x position
    for i, (text, w) in enumerate(zip(texts, widths)):
        pdf.set_fill_color(*bg_color)
        pdf.set_xy(x0 + sum(widths[:i]), y0)
        pdf.multi_cell(
            w,
            row_h,
            text,
            border=border,
            align="L",
            fill=True,
            new_x="RIGHT",
            new_y="TOP",
            max_line_height=LINE_H,
        )

    pdf.set_y(y0 + row_h)


def _draw_horizontal_rule(pdf: FPDF, color: tuple[int, int, int], thickness: float) -> None:
    """Draw a full-table-width horizontal line at the current Y position."""
    pdf.set_draw_color(*color)
    pdf.set_line_width(thickness)
    x0 = pdf.l_margin
    y = pdf.get_y()
    pdf.line(x0, y, x0 + TABLE_W_MM, y)
    # Reset draw color and line width to defaults
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.2)


def _format_date(date_str: str) -> str:
    """Convert 'YYYY-MM-DD' to 'Wednesday, March 19, 2026'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%A, %B %-d, %Y")
    except (ValueError, TypeError):
        return date_str


def _format_time_range(start: str, end: str) -> str:
    """Return 'start - end' with hyphen, handling empty strings gracefully."""
    start = (start or "").strip()
    end = (end or "").strip()
    if start and end:
        return f"{start} - {end}"
    if start:
        return start
    if end:
        return end
    return ""


# ---------------------------------------------------------------------------
# Minimal / error PDF builders
# ---------------------------------------------------------------------------

def _minimal_pdf(message: str) -> bytes:
    """Return a one-line PDF with the given message."""
    pdf = FPDF(unit="mm", format="Letter")
    pdf.add_page()
    pdf.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 8, message, align="L")
    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_triage_pdf(
    schedule: TriageSchedule,
    responsibilities: dict[str, str],
    hospital_name: str = "BSW Hospital Medicine Dallas",
) -> bytes:
    """Generate a triage sheet PDF and return it as bytes.

    Args:
        schedule: TriageSchedule dataclass produced by the triage scraper.
        responsibilities: Mapping of shift label -> responsibility text.
            Labels not present in the dict get an empty cell.
        hospital_name: Title shown at the top of the sheet.

    Returns:
        Raw PDF bytes.
    """
    try:
        if not schedule.shifts:
            return _minimal_pdf(f"No shifts found for {schedule.date}")

        # ----------------------------------------------------------------
        # Build ordered shift list: normal shifts first, next-day T1 last
        # ----------------------------------------------------------------
        sorted_shifts = sorted(schedule.shifts, key=_sort_key)

        # Identify section boundaries for separators
        main_start_idx: int | None = None    # first non-prior-day-T3 row
        next_day_start_idx: int | None = None  # first next-day T1 row
        for idx, shift in enumerate(sorted_shifts):
            if main_start_idx is None and not shift.is_prior_day_t3:
                main_start_idx = idx
            if next_day_start_idx is None and shift.is_next_day_t1:
                next_day_start_idx = idx

        # ----------------------------------------------------------------
        # Create PDF
        # ----------------------------------------------------------------
        pdf = FPDF(unit="mm", format="Letter")
        pdf.set_auto_page_break(auto=True, margin=MARGIN_MM)
        pdf.add_page()
        pdf.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)

        # ----------------------------------------------------------------
        # Header section
        # ----------------------------------------------------------------
        # Line 1: bold 14pt title
        pdf.set_font("Helvetica", style="B", size=14)
        pdf.cell(0, 8, f"{hospital_name} - Triage Sheet", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="L")

        # Line 2: regular 11pt date
        pdf.set_font("Helvetica", size=11)
        pdf.cell(0, 7, _format_date(schedule.date), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="L")

        # Thin horizontal rule
        pdf.ln(1)
        _draw_horizontal_rule(pdf, (0, 0, 0), 0.3)
        pdf.ln(4)

        # ----------------------------------------------------------------
        # Column header row
        # ----------------------------------------------------------------
        pdf.set_font("Helvetica", style="B", size=9)
        pdf.set_line_width(0.2)
        _draw_row(pdf, COL_HEADERS, COL_WIDTHS, COLOR_HEADER_BG, border="1")

        # ----------------------------------------------------------------
        # Data rows
        # ----------------------------------------------------------------
        pdf.set_font("Helvetica", size=9)

        row_colors = [COLOR_ROW_WHITE, COLOR_ROW_GRAY]

        for row_idx, shift in enumerate(sorted_shifts):
            # Insert separator after prior-day T3 section (before main shifts)
            if row_idx == main_start_idx and main_start_idx != 0:
                pdf.ln(4)
                _draw_horizontal_rule(pdf, COLOR_RULE_GRAY, 0.3)
                pdf.ln(3)
            # Insert separator before next-day T1 section
            elif row_idx == next_day_start_idx:
                pdf.ln(4)
                _draw_horizontal_rule(pdf, COLOR_RULE_GRAY, 0.3)
                pdf.ln(3)

            bg = row_colors[row_idx % 2]

            provider_text = "\n".join(shift.providers) if shift.providers else ""
            time_text = _format_time_range(shift.start_time, shift.end_time)
            resp_text = responsibilities.get(shift.label, "")

            texts = [
                shift.label,
                time_text,
                provider_text,
                resp_text,
            ]

            _draw_row(pdf, texts, COL_WIDTHS, bg, border="1")

        return bytes(pdf.output())

    except Exception:
        logger.exception("Failed to generate triage PDF")
        err_details = traceback.format_exc()
        # Return a minimal error PDF so callers always get bytes back
        return _minimal_pdf(
            f"Error generating triage PDF for {getattr(schedule, 'date', 'unknown date')}.\n\n"
            f"{err_details}"
        )
