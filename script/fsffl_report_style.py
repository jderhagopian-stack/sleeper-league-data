#!/usr/bin/env python3
"""Shared visual system for manager-facing FSFFL PDF reports."""
from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Flowable, Paragraph, Table, TableStyle

NAVY = colors.HexColor('#14213D')
BLUE = colors.HexColor('#1F5D9B')
RED = colors.HexColor('#C23B36')
GREEN = colors.HexColor('#2F7D4A')
GOLD = colors.HexColor('#D4A017')
GRAY = colors.HexColor('#5F6B76')
BLACK = colors.HexColor('#1C1F23')
WHITE = colors.white
LIGHT_BLUE = colors.HexColor('#EAF2F8')
LIGHT_RED = colors.HexColor('#FBEDEC')
LIGHT_GREEN = colors.HexColor('#EAF5EE')
LIGHT_GOLD = colors.HexColor('#FBF4DD')
LIGHT_GRAY = colors.HexColor('#F3F5F7')
MID_GRAY = colors.HexColor('#D8DDE3')


def clean(value):
    s = str(value or '').replace('—','-').replace('–','-')
    return ''.join(ch for ch in s if ord(ch) < 0x10000 and not (0x2600 <= ord(ch) <= 0x27BF))


def safe_float(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default


def styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name='FS_Title', parent=s['Title'], fontName='Helvetica-Bold', fontSize=18.5, leading=20.5, textColor=NAVY, spaceAfter=2))
    s.add(ParagraphStyle(name='FS_Sub', parent=s['Normal'], fontSize=8.2, leading=10, textColor=GRAY))
    s.add(ParagraphStyle(name='FS_Section', parent=s['Heading2'], fontName='Helvetica-Bold', fontSize=10.2, leading=11.8, textColor=NAVY, spaceBefore=3, spaceAfter=3))
    s.add(ParagraphStyle(name='FS_Body', parent=s['BodyText'], fontSize=7.9, leading=9.8, textColor=BLACK))
    s.add(ParagraphStyle(name='FS_Small', parent=s['BodyText'], fontSize=6.8, leading=8.3, textColor=GRAY))
    s.add(ParagraphStyle(name='FS_CardLabel', parent=s['Normal'], fontName='Helvetica-Bold', fontSize=6.9, leading=8, textColor=GRAY, alignment=1))
    s.add(ParagraphStyle(name='FS_CardValue', parent=s['Normal'], fontName='Helvetica-Bold', fontSize=12.2, leading=13.2, textColor=BLACK, alignment=1))
    s.add(ParagraphStyle(name='FS_WhiteLabel', parent=s['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=9.2, textColor=WHITE))
    return s


def P(s, text, style='FS_Body'):
    return Paragraph(clean(text), s[style])


def kpi_card(s, label, value, tone='neutral', width=1.22*inch):
    bg, fg = {
        'positive': (LIGHT_GREEN, GREEN),
        'negative': (LIGHT_RED, RED),
        'warning': (LIGHT_GOLD, GOLD),
        'neutral': (LIGHT_GRAY, BLACK),
        'blue': (LIGHT_BLUE, NAVY),
    }.get(tone, (LIGHT_GRAY, BLACK))
    value_style = ParagraphStyle(name='tmpv'+str(abs(hash((label,value)))), parent=s['FS_CardValue'], textColor=fg)
    t = Table([[P(s,label,'FS_CardLabel')],[Paragraph(clean(value), value_style)]], colWidths=[width], rowHeights=[0.23*inch,0.36*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),bg),('BOX',(0,0),(-1,-1),0.6,MID_GRAY),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2)
    ]))
    return t


class Rule(Flowable):
    def __init__(self, width, color=MID_GRAY, thickness=.6):
        super().__init__(); self.width=width; self.height=2; self.color=color; self.thickness=thickness
    def draw(self):
        self.canv.setStrokeColor(self.color); self.canv.setLineWidth(self.thickness); self.canv.line(0,1,self.width,1)


def footer(canvas, model_label):
    canvas.saveState(); canvas.setFont('Helvetica',6.3); canvas.setFillColor(GRAY)
    canvas.drawString(.55*inch,.30*inch, clean(model_label))
    canvas.drawRightString(7.95*inch,.30*inch,'FSFFL Decision Support')
    canvas.restoreState()
