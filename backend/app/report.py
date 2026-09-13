import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable,
)
from reportlab.graphics.shapes import Drawing, Rect

from .config import settings

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# ---------------------------------------------------------------- theme -----
NAVY = HexColor("#152447")
NAVY_SOFT = HexColor("#2B3E6B")
GOLD = HexColor("#C9A227")
CREAM = HexColor("#FBF8F1")
PAPER = HexColor("#F4F2EC")
INK = HexColor("#26262B")
MUTED = HexColor("#767468")
SUCCESS = HexColor("#2F7D5C")
WARN = HexColor("#B4741F")
LINE = HexColor("#E4E0D3")
ZEBRA = HexColor("#F5F6FA")

_fonts_registered = False


def _register_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont("PTSerif", os.path.join(FONT_DIR, "PTSerif-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("PTSerif-Bold", os.path.join(FONT_DIR, "PTSerif-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("PTSerif-Italic", os.path.join(FONT_DIR, "PTSerif-Italic.ttf")))
    pdfmetrics.registerFont(TTFont("PTSans", os.path.join(FONT_DIR, "PTSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("PTSans-Bold", os.path.join(FONT_DIR, "PTSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("PTSans-Italic", os.path.join(FONT_DIR, "PTSans-Italic.ttf")))
    pdfmetrics.registerFontFamily(
        "PTSerif", normal="PTSerif", bold="PTSerif-Bold",
        italic="PTSerif-Italic", boldItalic="PTSerif-Bold",
    )
    pdfmetrics.registerFontFamily(
        "PTSans", normal="PTSans", bold="PTSans-Bold",
        italic="PTSans-Italic", boldItalic="PTSans-Bold",
    )
    _fonts_registered = True


def _styles():
    return {
        "h2": ParagraphStyle("h2", fontName="PTSerif-Bold", fontSize=14.5, textColor=NAVY, spaceBefore=16, spaceAfter=6),
        "body": ParagraphStyle("body", fontName="PTSans", fontSize=9.5, textColor=INK, leading=14),
        "muted": ParagraphStyle("muted", fontName="PTSans", fontSize=8.3, textColor=MUTED, leading=12),
        "stagehead": ParagraphStyle("stagehead", fontName="PTSans-Bold", fontSize=9.5, textColor=NAVY, leading=13),
        "cell": ParagraphStyle("cell", fontName="PTSans", fontSize=8.7, textColor=INK, leading=12.5),
        "cellbold": ParagraphStyle("cellbold", fontName="PTSans-Bold", fontSize=9, textColor=NAVY, leading=12.5),
        "headcell": ParagraphStyle("headcell", fontName="PTSans-Bold", fontSize=8.5, textColor=CREAM, leading=11),
        "caption": ParagraphStyle("caption", fontName="PTSans-Bold", fontSize=8, textColor=MUTED, alignment=TA_CENTER, spaceBefore=4),
    }


def _badge(text: str, bg, fg) -> Table:
    t = Table([[text]], colWidths=[None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("TEXTCOLOR", (0, 0), (-1, -1), fg),
        ("FONTNAME", (0, 0), (-1, -1), "PTSans-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _score_bar(score: float, max_score: float = 10, width: float = 66, height: float = 8) -> Drawing:
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=PAPER, strokeColor=LINE, strokeWidth=0.4))
    fill_w = max(2, width * (score / max_score))
    color = GOLD if score >= 8 else NAVY_SOFT if score >= 6 else MUTED
    d.add(Rect(0, 0, fill_w, height, fillColor=color, strokeColor=None))
    return d


def _header_footer(goal_short: str):
    def _draw(c: pdf_canvas.Canvas, doc):
        c.saveState()
        page_w, page_h = letter
        c.setFillColor(NAVY)
        c.rect(0, page_h - 78, page_w, 78, fill=1, stroke=0)
        c.setFillColor(GOLD)
        c.setFont("PTSans-Bold", 8.5)
        c.drawString(0.75 * inch, page_h - 30, "S C O U T   A G E N T")
        c.setFillColor(CREAM)
        c.setFont("PTSerif-Bold", 18)
        c.drawString(0.75 * inch, page_h - 55, "Autonomous research & action report")
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.2)
        c.line(0, page_h - 78, page_w, page_h - 78)

        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(0.75 * inch, 0.55 * inch, page_w - 0.75 * inch, 0.55 * inch)
        c.setFillColor(MUTED)
        c.setFont("PTSans", 7.6)
        c.drawString(0.75 * inch, 0.38 * inch, goal_short)
        c.drawRightString(page_w - 0.75 * inch, 0.38 * inch, f"Page {doc.page}")
        c.restoreState()
    return _draw


def build_pdf(session) -> str:
    _register_fonts()
    st = _styles()
    path = os.path.join(settings.REPORTS_DIR, f"scout_report_{session.id}.pdf")

    doc = SimpleDocTemplate(
        path, pagesize=letter,
        topMargin=1.15 * inch, bottomMargin=0.85 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        title=f"Scout report — {session.goal[:60]}",
    )
    story = []

    total_time = session.timings.get("total") or sum(v for k, v in session.timings.items() if k != "total")
    meta_table = Table(
        [[Paragraph(f"<b>Goal</b><br/>{session.goal}", st["body"]),
          Paragraph(f"<b>Total runtime</b><br/>{total_time:.1f}s", st["body"]),
          Paragraph(f"<b>Research rounds</b><br/>{session.iteration}", st["body"])]],
        colWidths=[3.4 * inch, 1.3 * inch, 1.3 * inch],
    )
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CREAM),
        ("BOX", (0, 0), (-1, -1), 0.75, GOLD),
        ("LINEAFTER", (0, 0), (0, 0), 0.5, LINE),
        ("LINEAFTER", (1, 0), (1, 0), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 18))

    story.append(Paragraph("Reasoning trace", st["h2"]))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=8))

    for entry in session.reasoning_log:
        label = entry["stage"].replace("_", " ").title()
        head_row = Table(
            [[_badge(f"{entry['elapsed_s']}s", NAVY, CREAM), Paragraph(f"<b>{label}</b>", st["stagehead"])]],
            colWidths=[0.7 * inch, 5.6 * inch],
        )
        head_row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))

        detail_lines = []
        if entry.get("queries"):
            detail_lines.append(f"<b>Queries:</b> {', '.join(entry['queries'])}")
        if entry.get("findings"):
            detail_lines.append(entry["findings"])
        if entry.get("winner"):
            detail_lines.append(f"<b>Winner:</b> {entry['winner']}")

        cell_content = [head_row]
        if detail_lines:
            cell_content.append(Spacer(1, 3))
            cell_content.append(Paragraph("<br/>".join(detail_lines), st["body"]))

        wrapper = Table([[cell_content]], colWidths=[6.5 * inch])
        wrapper.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PAPER),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ]))
        story.append(wrapper)
        story.append(Spacer(1, 8))

    if session.decision and not session.decision.get("options"):
        story.append(Spacer(1, 6))
        story.append(Paragraph("Comparison", st["h2"]))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=8))
        story.append(Paragraph(
            session.decision.get("note") or "No evidence-backed candidates were found for this goal.",
            st["body"],
        ))

    elif session.decision:
        story.append(Spacer(1, 6))
        story.append(Paragraph("Comparison", st["h2"]))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=8))

        rows = [[
            Paragraph("OPTION", st["headcell"]),
            Paragraph("SCORE", st["headcell"]),
            "",
            Paragraph("WHY IT SCORED THIS WAY", st["headcell"]),
        ]]
        options = sorted(session.decision.get("options", []), key=lambda o: -o.get("score", 0))
        winner_idx = None
        for i, opt in enumerate(options):
            if opt.get("name") == session.decision.get("winner"):
                winner_idx = i + 1
            just = opt.get("justification", "")
            if opt.get("source_url"):
                just += f' <a href="{opt["source_url"]}" color="#152447"><u>source</u></a>'
            rows.append([
                Paragraph(f"<b>{opt.get('name','')}</b>", st["cellbold"]),
                Paragraph(f"{opt.get('score','')}/10", st["cell"]),
                _score_bar(opt.get("score", 0)),
                Paragraph(just, st["cell"]),
            ])

        table = Table(rows, colWidths=[1.45 * inch, 0.65 * inch, 0.85 * inch, 3.55 * inch])
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), CREAM),
            ("FONTNAME", (0, 0), (-1, 0), "PTSans-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("TOPPADDING", (0, 0), (-1, 0), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
            ("TOPPADDING", (0, 1), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("BOX", (0, 0), (-1, -1), 0.75, NAVY),
        ]
        for r in range(1, len(rows)):
            if r % 2 == 0:
                style.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
        if winner_idx:
            style.append(("BACKGROUND", (0, winner_idx), (-1, winner_idx), CREAM))
            style.append(("LINEABOVE", (0, winner_idx), (-1, winner_idx), 1, GOLD))
            style.append(("LINEBELOW", (0, winner_idx), (-1, winner_idx), 1, GOLD))
        table.setStyle(TableStyle(style))
        story.append(table)
        story.append(Spacer(1, 6))
        story.append(_badge(f"WINNER — {session.decision.get('winner', 'n/a')}", GOLD, NAVY))

    if session.action_result:
        story.append(Spacer(1, 18))
        story.append(Paragraph("Action taken", st["h2"]))
        story.append(HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=8))

        ok = session.action_result.get("success")
        badge = _badge("FORM SUBMITTED", SUCCESS, CREAM) if ok else _badge("ACTION SKIPPED", WARN, CREAM)
        meta_line = Paragraph(
            f"Target: <a href='{session.action_result.get('final_url','')}' color='#152447'>"
            f"<u>{session.action_result.get('final_url','')}</u></a>"
            + (f"<br/>{session.action_result.get('reason','')}" if not ok else ""),
            st["body"],
        )
        head = Table([[badge, meta_line]], colWidths=[1.7 * inch, 4.8 * inch])
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        story.append(head)
        story.append(Spacer(1, 10))

        shots = []
        for label, key in [("Before", "before_screenshot"), ("After", "after_screenshot")]:
            p = session.action_result.get(key)
            if p and os.path.exists(p):
                shots.append([Image(p, width=2.75 * inch, height=1.72 * inch), Paragraph(label.upper(), st["caption"])])
        if shots:
            frame = Table([shots], colWidths=[3.1 * inch] * len(shots))
            frame.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.75, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (-1, -1), CREAM),
            ]))
            story.append(frame)

    doc.build(story, onFirstPage=_header_footer(session.goal[:70]), onLaterPages=_header_footer(session.goal[:70]))
    return path
