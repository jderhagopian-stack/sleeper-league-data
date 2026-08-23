#!/usr/bin/env python3
"""Render an FSFFL Counter & Market Sweep JSON result as a one-page PDF.

Presentation-only layer: this script does not score, simulate, or change a trade.
It renders the already-computed Decision Lab / Market Sweep result.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MODEL_VERSION = "FSFFL-Trade-Decision-Report-1.0"

NAVY = colors.HexColor("#14213D")
RED = colors.HexColor("#C23B36")
LIGHT_RED = colors.HexColor("#FBEDEC")
GREEN = colors.HexColor("#2F7D4A")
LIGHT_GREEN = colors.HexColor("#EAF5EE")
GRAY = colors.HexColor("#5F6B76")
LIGHT_GRAY = colors.HexColor("#F3F5F7")
MID_GRAY = colors.HexColor("#D8DDE3")
LIGHT_BLUE = colors.HexColor("#EAF2F8")
WHITE = colors.white
BLACK = colors.HexColor("#1C1F23")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str:
    s = str(value or "")
    s = s.replace("—", "-").replace("–", "-")
    return "".join(ch for ch in s if ord(ch) < 0x10000 and not (0x2600 <= ord(ch) <= 0x27BF))


def signed_points(value: Any) -> str:
    return f"{safe_float(value) * 100:+.0f} pts"


def signed(value: Any, digits: int = 0) -> str:
    return f"{safe_float(value):+,.{digits}f}"


def verdict_label(action: str) -> str:
    mapping = {
        "ACCEPT_NOW": "ACCEPT",
        "COUNTER_CURRENT_OFFEROR": "COUNTER",
        "SHOP_BEFORE_ACCEPTING": "SHOP FIRST",
        "DECLINE": "DECLINE",
    }
    return mapping.get(str(action or "").upper(), clean_text(action).replace("_", " ").upper() or "REVIEW")


def roster_name(report: Dict[str, Any], uid: str) -> str:
    current = report.get("current_offer_evaluation") or {}
    if str(current.get("buyer_user_id")) == str(uid):
        return clean_text(current.get("buyer_team"))
    for row in report.get("top_5_alternatives") or []:
        if str(row.get("buyer_user_id")) == str(uid):
            return clean_text(row.get("buyer_team"))
    return str(uid)


def offer_summary(current: Dict[str, Any]):
    sent = ", ".join(clean_text(x) for x in current.get("outgoing_asset_names") or []) or "assets"
    rec = ", ".join(clean_text(x) for x in current.get("return_asset_names") or []) or "assets"
    return sent, rec


def best_reason(report: Dict[str, Any], current: Dict[str, Any]) -> str:
    sim = current.get("simulation") or {}
    fd = sim.get("focus_delta") or {}
    strategic = sim.get("strategic") or {}
    action = str(report.get("recommended_next_action") or "").upper()
    champ = safe_float(fd.get("championship_probability"))
    wins = safe_float(fd.get("expected_wins"))
    dyn = safe_float(strategic.get("market_dynasty_delta"))
    ext = safe_float(sim.get("net_title_equity_swing_against_focus"))
    if action == "DECLINE":
        return (
            f"The current offer changes expected wins {wins:+.2f} and title odds {champ * 100:+.0f} points. "
            f"It adds {dyn:+,.0f} dynasty-market value, but the modeled competitive externality swings "
            f"{ext * 100:+.0f} title-equity points against the focal team."
        )
    if action == "ACCEPT_NOW":
        return (
            f"The current offer improves the focal team's modeled utility without violating its competitive-state guardrails. "
            f"Expected wins move {wins:+.2f}, title odds {champ * 100:+.0f} points, and dynasty-market value {dyn:+,.0f}."
        )
    if action == "COUNTER_CURRENT_OFFEROR":
        return (
            f"The current structure is not the best fit, but the same partner has a stronger modeled counter path. "
            f"Current-offer title odds move {champ * 100:+.0f} points and dynasty-market value {dyn:+,.0f}."
        )
    return (
        f"The offer is viable enough to keep open, but the league-wide sweep found a stronger modeled market path. "
        f"Current-offer title odds move {champ * 100:+.0f} points and dynasty-market value {dyn:+,.0f}."
    )


class Rule(Flowable):
    def __init__(self, width: float, color=MID_GRAY, thickness: float = 0.6):
        super().__init__()
        self.width = width
        self.height = 2
        self.color = color
        self.thickness = thickness

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 1, self.width, 1)


def render(report: Dict[str, Any], output: Path) -> None:
    current = report.get("current_offer_evaluation") or {}
    sim = current.get("simulation") or {}
    before = sim.get("focus_before") or {}
    after = sim.get("focus_after") or {}
    delta = sim.get("focus_delta") or {}
    strategic = sim.get("strategic") or {}
    buyer = current.get("buyer_team") or roster_name(report, report.get("current_offer_partner_user_id"))
    sent, received = offer_summary(current)
    action = str(report.get("recommended_next_action") or "REVIEW")
    verdict = verdict_label(action)
    top = list(report.get("top_5_alternatives") or [])[:5]

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleFS", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=18.5, leading=20.5, textColor=NAVY, spaceAfter=2))
    styles.add(ParagraphStyle(name="SubFS", parent=styles["Normal"], fontSize=8.2, leading=10.2, textColor=GRAY))
    styles.add(ParagraphStyle(name="SectionFS", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=10.2, leading=11.8, textColor=NAVY, spaceBefore=3, spaceAfter=3))
    styles.add(ParagraphStyle(name="BodyFS", parent=styles["BodyText"], fontSize=7.9, leading=9.8, textColor=BLACK))
    styles.add(ParagraphStyle(name="SmallFS", parent=styles["BodyText"], fontSize=6.8, leading=8.3, textColor=GRAY))
    styles.add(ParagraphStyle(name="CardLabel", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=6.9, leading=8, textColor=GRAY, alignment=1))
    styles.add(ParagraphStyle(name="CardValue", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12.5, leading=13.5, textColor=BLACK, alignment=1))
    styles.add(ParagraphStyle(name="Verdict", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=14.2, leading=15.5, textColor=WHITE, alignment=1))
    styles.add(ParagraphStyle(name="TradeText", parent=styles["Normal"], fontSize=7.45, leading=9.0, textColor=BLACK))
    styles.add(ParagraphStyle(name="BottomLabel", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=7.9, leading=9.2, textColor=WHITE))
    styles.add(ParagraphStyle(name="BottomBody", parent=styles["Normal"], fontSize=7.9, leading=9.8, textColor=BLACK))

    def P(text: str, style: str = "BodyFS") -> Paragraph:
        return Paragraph(clean_text(text), styles[style])

    def card(label: str, value: str, tint, value_color):
        vs = ParagraphStyle(name=f"v-{re.sub('[^A-Za-z0-9]','',label)}", parent=styles["CardValue"], textColor=value_color)
        t = Table([[P(label, "CardLabel")], [Paragraph(clean_text(value), vs)]], colWidths=[1.22 * inch], rowHeights=[0.23 * inch, 0.36 * inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), tint), ("BOX", (0, 0), (-1, -1), 0.6, MID_GRAY),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return t

    def trade_line(row: Dict[str, Any], idx: int) -> Paragraph:
        buyer_name = clean_text(row.get("buyer_team"))
        out_names = ", ".join(clean_text(x) for x in row.get("outgoing_asset_names") or [])
        ret_names = ", ".join(clean_text(x) for x in row.get("return_asset_names") or [])
        acc = clean_text(row.get("acceptance_likelihood"))
        role = clean_text(row.get("report_role")).replace("_", " ").title()
        comp = (row.get("comparison_to_current_offer") or {}).get("metric_deltas_vs_current_offer") or {}
        text = (
            f"<b>{idx}. {buyer_name}</b> <font color='#5F6B76'>[{role}; {acc} acceptance fit]</font><br/>"
            f"Send: {out_names} &nbsp; | &nbsp; Receive: {ret_names}<br/>"
            f"<font color='#2F7D4A'>vs current: {signed_points(comp.get('championship_probability'))} title, {safe_float(comp.get('expected_wins')):+.2f} wins</font>"
        )
        return Paragraph(text, styles["TradeText"])

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 6.3)
        canvas.setFillColor(GRAY)
        quick = safe_float((report.get("simulation") or {}).get("quick_sims"), 0)
        canvas.drawString(0.55 * inch, 0.30 * inch, f"{MODEL_VERSION} | Counter & Market Sweep {report.get('model_version','')} | {int(quick) if quick else 'quick'}-sim screen; acceptance fit is heuristic")
        canvas.drawRightString(7.95 * inch, 0.30 * inch, "FSFFL Decision Support")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(output), pagesize=letter, rightMargin=0.48 * inch, leftMargin=0.48 * inch, topMargin=0.40 * inch, bottomMargin=0.44 * inch)
    story: List[Any] = []
    story.extend([P("FSFFL TRADE DECISION REPORT", "TitleFS"), P(f"Incoming offer from {clean_text(buyer)} - focal team sends {sent}", "SubFS"), Spacer(1, 4)])

    verdict_color = GREEN if verdict == "ACCEPT" else RED if verdict == "DECLINE" else NAVY
    verdict_tbl = Table([[Paragraph(f"MODEL VERDICT:<br/>{verdict}", styles["Verdict"]), P(f"<b>Receive:</b> {received}", "BodyFS")]], colWidths=[2.15 * inch, 5.28 * inch], rowHeights=[0.62 * inch])
    verdict_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), verdict_color), ("BACKGROUND", (1, 0), (1, 0), LIGHT_GRAY), ("BOX", (0, 0), (-1, -1), 0.8, MID_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([verdict_tbl, Spacer(1, 6)])

    def arrow_metric(key: str, percent: bool = False) -> str:
        b = safe_float(before.get(key)); a = safe_float(after.get(key))
        if percent:
            return f"{b*100:.0f}% -> {a*100:.0f}%"
        return f"{b:.2f} -> {a:.2f}"

    cards = [
        card("Expected Wins", arrow_metric("expected_wins"), LIGHT_RED if safe_float(delta.get("expected_wins")) < 0 else LIGHT_GREEN, RED if safe_float(delta.get("expected_wins")) < 0 else GREEN),
        card("Expected PF", f"{safe_float(before.get('expected_points_for')):.0f} -> {safe_float(after.get('expected_points_for')):.0f}", LIGHT_RED if safe_float(delta.get("expected_points_for")) < 0 else LIGHT_GREEN, RED if safe_float(delta.get("expected_points_for")) < 0 else GREEN),
        card("Playoff Odds", arrow_metric("playoff_probability", percent=True), LIGHT_RED if safe_float(delta.get("playoff_probability")) < 0 else LIGHT_GREEN, RED if safe_float(delta.get("playoff_probability")) < 0 else GREEN),
        card("Championship Odds", arrow_metric("championship_probability", percent=True), LIGHT_RED if safe_float(delta.get("championship_probability")) < 0 else LIGHT_GREEN, RED if safe_float(delta.get("championship_probability")) < 0 else GREEN),
        card("Dynasty Market", signed(strategic.get("market_dynasty_delta")), LIGHT_GREEN if safe_float(strategic.get("market_dynasty_delta")) >= 0 else LIGHT_RED, GREEN if safe_float(strategic.get("market_dynasty_delta")) >= 0 else RED),
        card("Break-Glass", signed(strategic.get("break_glass_delta")), LIGHT_GREEN if safe_float(strategic.get("break_glass_delta")) >= 0 else LIGHT_RED, GREEN if safe_float(strategic.get("break_glass_delta")) >= 0 else RED),
    ]
    ct = Table([cards[:3], cards[3:]], colWidths=[2.47 * inch] * 3, rowHeights=[0.64 * inch, 0.64 * inch], hAlign="LEFT")
    ct.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    story.extend([ct, Spacer(1, 5)])

    left: List[Any] = [P("MODEL READ", "SectionFS"), P(best_reason(report, current), "BodyFS"), Spacer(1, 3)]
    br = current.get("buyer_rationality") or {}
    acceptance = br.get("heuristic_acceptance_fit")
    if acceptance:
        left.extend([P(f"<b>Counterparty fit:</b> {acceptance}. {clean_text((br.get('owner_behavior') or {}).get('reason'))}", "BodyFS"), Spacer(1, 3)])
    if sim.get("opponent_delta"):
        od = sim.get("opponent_delta") or {}
        left.extend([P(f"<b>Opponent effect:</b> {safe_float(od.get('expected_wins')):+.2f} expected wins and {signed_points(od.get('championship_probability'))} championship probability.", "BodyFS"), Spacer(1, 3)])
    left.append(P("<b>Interpretation:</b> quick simulation percentages are directional screening estimates. Confirm a finalist at higher simulation depth before acting when the decision is material.", "SmallFS"))

    right: List[Any] = [P("MARKET SWEEP - 5 NEGOTIATION PATHS", "SectionFS")]
    for i, row in enumerate(top, 1):
        right.append(trade_line(row, i))
        if i != len(top):
            right.extend([Spacer(1, 2), Rule(3.55 * inch, colors.HexColor("#E5E8EB"), 0.4), Spacer(1, 2)])
    if not top:
        right.append(P("No alternative packages cleared the current market-sweep filters.", "BodyFS"))

    cols = Table([[left, right]], colWidths=[3.72 * inch, 3.72 * inch], hAlign="LEFT")
    cols.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, 0), 0), ("RIGHTPADDING", (0, 0), (0, 0), 9),
        ("LEFTPADDING", (1, 0), (1, 0), 9), ("RIGHTPADDING", (1, 0), (1, 0), 0), ("LINEBEFORE", (1, 0), (1, 0), 0.7, MID_GRAY),
    ]))
    story.extend([cols, Spacer(1, 4), Rule(7.44 * inch), Spacer(1, 3)])

    if action == "DECLINE":
        rec_text = "Reject as structured. Use the market-sweep alternatives as negotiation targets; preserve the focal team's competitive-state guardrails."
    elif action == "COUNTER_CURRENT_OFFEROR":
        rec_text = "Counter the current offeror with the best same-partner path identified by the model before shopping elsewhere."
    elif action == "SHOP_BEFORE_ACCEPTING":
        rec_text = "Keep the current offer open, but shop the strongest alternatives before accepting."
    else:
        rec_text = "Accept if the offer remains available; the market sweep did not identify a sufficiently superior actionable alternative."
    bottom = Table([[Paragraph("Recommended<br/>move", styles["BottomLabel"]), Paragraph(clean_text(rec_text), styles["BottomBody"])]], colWidths=[1.35 * inch, 6.09 * inch])
    bottom.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), NAVY), ("BACKGROUND", (1, 0), (1, 0), LIGHT_BLUE), ("BOX", (0, 0), (-1, -1), 0.7, MID_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(bottom)
    doc.build(story, onFirstPage=footer)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Counter & Market Sweep JSON report")
    ap.add_argument("--output", required=True, help="Destination PDF path")
    args = ap.parse_args()
    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    render(report, output)
    print(json.dumps({"renderer_model_version": MODEL_VERSION, "pdf": str(output), "source_model_version": report.get("model_version")}, indent=2))


if __name__ == "__main__":
    main()
