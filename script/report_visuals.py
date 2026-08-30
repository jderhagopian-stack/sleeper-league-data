#!/usr/bin/env python3
"""Evidence-backed visual primitives for the FSFFL Reporting module.

Charts are presentation-only. They use already-authoritative report inputs and
never create or modify model scores, rankings or recommendations.
"""
from __future__ import annotations
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.lib import colors
from reportlab.lib.units import inch
from report_context import team_context

NAVY=colors.HexColor("#14213D")
BLUE=colors.HexColor("#1F5D9B")
GRAY=colors.HexColor("#5F6B76")
LIGHT=colors.HexColor("#D8DDE3")


def _sf(v,d=0.0):
    try: return float(v)
    except (TypeError,ValueError): return d


def _title(drawing,text):
    drawing.add(String(0,drawing.height-10,text,fontName="Helvetica-Bold",fontSize=9,fillColor=NAVY))


def position_need_chart(uid, width=3.45*inch, height=1.85*inch):
    """Horizontal comparison of authoritative GM3 position-need scores."""
    ctx=team_context(uid)
    needs=(ctx or {}).get("needs") or {}
    rows=[(p,_sf(v)) for p,v in needs.items() if p in {"QB","RB","WR","TE"}]
    if len(rows)<2:
        return None
    rows=sorted(rows,key=lambda x:x[1])
    d=Drawing(width,height)
    _title(d,"Roster need by position")
    chart=HorizontalBarChart()
    chart.x=42; chart.y=16; chart.width=width-52; chart.height=height-38
    chart.data=[[v for _,v in rows]]
    chart.categoryAxis.categoryNames=[p for p,_ in rows]
    chart.valueAxis.valueMin=0; chart.valueAxis.valueMax=1
    chart.valueAxis.valueStep=.25
    chart.valueAxis.labelTextFormat=lambda x:f"{x:.2f}"
    chart.bars[0].fillColor=BLUE
    chart.bars[0].strokeColor=None
    chart.categoryAxis.labels.fontName="Helvetica"
    chart.categoryAxis.labels.fontSize=7
    chart.valueAxis.labels.fontName="Helvetica"
    chart.valueAxis.labels.fontSize=6
    chart.valueAxis.strokeColor=LIGHT
    chart.categoryAxis.strokeColor=LIGHT
    d.add(chart)
    return d


def probability_change_chart(before, after, width=3.55*inch, height=1.85*inch):
    """Before/after playoff, bye and championship probabilities."""
    metrics=[
        ("Playoff",_sf((before or {}).get("playoff_probability"))*100,_sf((after or {}).get("playoff_probability"))*100),
        ("Bye",_sf((before or {}).get("bye_probability"))*100,_sf((after or {}).get("bye_probability"))*100),
        ("Title",_sf((before or {}).get("championship_probability"))*100,_sf((after or {}).get("championship_probability"))*100),
    ]
    if not any(abs(a-b)>=0.05 for _,a,b in metrics):
        return None
    d=Drawing(width,height)
    _title(d,"Season odds: before vs. after")
    chart=VerticalBarChart()
    chart.x=34; chart.y=18; chart.width=width-44; chart.height=height-42
    chart.data=[
        [b for _,b,_ in metrics],
        [a for _,_,a in metrics],
    ]
    chart.categoryAxis.categoryNames=[x[0] for x in metrics]
    chart.valueAxis.valueMin=0
    mx=max([x for row in chart.data for x in row] or [1])
    chart.valueAxis.valueMax=max(10,((int(mx/10)+1)*10))
    chart.valueAxis.valueStep=max(5,chart.valueAxis.valueMax/4)
    chart.bars[0].fillColor=colors.HexColor("#AAB7C4")
    chart.bars[1].fillColor=BLUE
    chart.bars[0].strokeColor=None; chart.bars[1].strokeColor=None
    chart.categoryAxis.labels.fontName="Helvetica"; chart.categoryAxis.labels.fontSize=7
    chart.valueAxis.labels.fontName="Helvetica"; chart.valueAxis.labels.fontSize=6
    chart.valueAxis.labelTextFormat=lambda x:f"{x:.0f}%"
    chart.valueAxis.strokeColor=LIGHT; chart.categoryAxis.strokeColor=LIGHT
    d.add(chart)
    d.add(String(width-102,height-10,"Before",fontName="Helvetica",fontSize=6.5,fillColor=GRAY))
    d.add(String(width-57,height-10,"After",fontName="Helvetica",fontSize=6.5,fillColor=BLUE))
    return d


def league_title_odds_chart(teams, width=7.25*inch, height=2.2*inch, limit=8):
    """Horizontal title-odds ranking when a visual improves league comparison."""
    rows=sorted(
        [(str(x.get("team_name") or ""),_sf(x.get("championship_probability"))*100) for x in (teams or [])],
        key=lambda x:x[1],reverse=True,
    )[:limit]
    if len(rows)<3:
        return None
    # If essentially everyone is tied, a table is more useful than a chart.
    if rows and (rows[0][1]-rows[-1][1])<1.0:
        return None
    rows=list(reversed(rows))
    d=Drawing(width,height)
    _title(d,"Championship odds - leading teams")
    chart=HorizontalBarChart()
    chart.x=135; chart.y=18; chart.width=width-150; chart.height=height-42
    chart.data=[[v for _,v in rows]]
    chart.categoryAxis.categoryNames=[n[:25] for n,_ in rows]
    chart.valueAxis.valueMin=0
    mx=max(v for _,v in rows)
    chart.valueAxis.valueMax=max(10,((int(mx/10)+1)*10))
    chart.valueAxis.valueStep=max(5,chart.valueAxis.valueMax/4)
    chart.valueAxis.labelTextFormat=lambda x:f"{x:.0f}%"
    chart.bars[0].fillColor=BLUE; chart.bars[0].strokeColor=None
    chart.categoryAxis.labels.fontName="Helvetica"; chart.categoryAxis.labels.fontSize=6.5
    chart.valueAxis.labels.fontName="Helvetica"; chart.valueAxis.labels.fontSize=6
    chart.valueAxis.strokeColor=LIGHT; chart.categoryAxis.strokeColor=LIGHT
    d.add(chart)
    return d


def position_need_change_chart(roster_diagnosis, width=3.65*inch, height=1.95*inch):
    """Before/after GM3 positional need; lower scores indicate less roster need."""
    rd=roster_diagnosis or {}
    before=(rd.get("before") or {}).get("position_need") or {}
    after=(rd.get("after") or {}).get("position_need") or {}
    rows=[(p,_sf(before.get(p)),_sf(after.get(p))) for p in ("QB","RB","WR","TE") if p in before and p in after]
    if len(rows)<2:
        return None
    d=Drawing(width,height)
    _title(d,"Roster needs: before vs. after")
    chart=VerticalBarChart()
    chart.x=34; chart.y=20; chart.width=width-44; chart.height=height-46
    chart.data=[[b for _,b,_ in rows],[a for _,_,a in rows]]
    chart.categoryAxis.categoryNames=[p for p,_,_ in rows]
    chart.valueAxis.valueMin=0; chart.valueAxis.valueMax=1; chart.valueAxis.valueStep=.25
    chart.valueAxis.labelTextFormat=lambda x:f"{x:.2f}"
    chart.bars[0].fillColor=colors.HexColor("#AAB7C4")
    chart.bars[1].fillColor=BLUE
    chart.bars[0].strokeColor=None; chart.bars[1].strokeColor=None
    chart.categoryAxis.labels.fontName="Helvetica"; chart.categoryAxis.labels.fontSize=7
    chart.valueAxis.labels.fontName="Helvetica"; chart.valueAxis.labels.fontSize=6
    chart.valueAxis.strokeColor=LIGHT; chart.categoryAxis.strokeColor=LIGHT
    d.add(chart)
    d.add(String(width-118,height-10,"Before",fontName="Helvetica",fontSize=6.5,fillColor=GRAY))
    d.add(String(width-70,height-10,"After",fontName="Helvetica",fontSize=6.5,fillColor=BLUE))
    d.add(String(0,3,"Lower = less positional need",fontName="Helvetica",fontSize=6,fillColor=GRAY))
    return d
