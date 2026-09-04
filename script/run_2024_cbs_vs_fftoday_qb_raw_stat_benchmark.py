#!/usr/bin/env python3
from __future__ import annotations
import csv, json, re, unicodedata, urllib.parse, urllib.request
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

CBS_URL='https://docs.google.com/spreadsheets/d/e/2PACX-1vRSLbPOpHYIzmkYX2H2QPlQVb2kvJVvZ0GRF5EPkQ_EuqCNZ-YG8I3CGC6eINcxqkfwufN-pZUaRb3t/pub?gid=0&single=true&output=csv'
FFT_URL='https://www.fftoday.com/rankings/playerproj.php'
NFLVERSE='https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats.csv'

STATS=['attempts','completions','passing_yards','passing_tds','interceptions','rushing_attempts','rushing_yards','rushing_tds']


def norm_name(v:str)->str:
    v=unicodedata.normalize('NFKD',v or '')
    v=''.join(c for c in v if not unicodedata.combining(c)).lower()
    v=re.sub(r'\b(jr|sr|ii|iii|iv|v)\b\.?','',v)
    return re.sub(r'[^a-z0-9]+','',v)

def get(url:str)->bytes:
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'*/*'})
    with urllib.request.urlopen(req,timeout=90) as r: return r.read()

def parse_cbs()->dict:
    rows=list(csv.DictReader(get(CBS_URL).decode('utf-8-sig').splitlines()))
    out={}
    for r in rows:
        name=norm_name(r.get('QB',''))
        if not name: continue
        try:
            out[name]={
                'attempts':float(r['ATT']),'completions':float(r['COMP']),'passing_yards':float(r['YDS']),
                'passing_tds':float(r['TD']),'interceptions':float(r['INT']),'rushing_attempts':float(r['RU']),
                'rushing_yards':float(r['RU YDS']),'rushing_tds':float(r['TD.1']),
            }
        except Exception:
            continue
    return out

class T(HTMLParser):
    def __init__(self): super().__init__(); self.rows=[]; self.row=None; self.cell=None; self.incell=False
    def handle_starttag(self,tag,attrs):
        if tag=='tr': self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=[]; self.incell=True
    def handle_data(self,data):
        if self.incell and self.cell is not None: self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.incell and self.row is not None:
            self.row.append(' '.join(' '.join(self.cell).split())); self.cell=None; self.incell=False
        elif tag=='tr' and self.row is not None:
            if self.row:self.rows.append(self.row)
            self.row=None

def parse_fft()->dict:
    q=urllib.parse.urlencode({'LeagueID':1,'PosID':10,'Season':2024,'cur_page':0,'order_by':'FName','sort_order':'ASC'})
    html=get(FFT_URL+'?'+q).decode('latin-1','replace')
    txt=re.sub(r'<[^>]+>',' ',html)
    if not re.search(r'Updated:\s*9/2/2024',txt,re.I): raise SystemExit('FFToday 2024 snapshot date changed/unverified')
    p=T(); p.feed(html)
    teams={'ARI','ATL','BAL','BUF','CAR','CHI','CIN','CLE','DAL','DEN','DET','GB','HOU','IND','JAC','JAX','KC','LAC','LAR','LV','MIA','MIN','NE','NO','NYG','NYJ','PHI','PIT','SEA','SF','TB','TEN','WAS'}
    out={}
    for row in p.rows:
        ti=next((i for i,x in enumerate(row) if x in teams),None)
        if ti is None or ti<1: continue
        name=norm_name(row[ti-1])
        tail=row[ti+1:]
        try:
            vals=[float(re.search(r'-?\d+(?:\.\d+)?',tail[i]).group()) for i in range(1,9)]
        except Exception: continue
        out[name]=dict(zip(STATS,vals))
    return out

def actuals()->dict:
    text=get(NFLVERSE).decode('utf-8-sig','replace')
    rows=csv.DictReader(text.splitlines())
    agg=defaultdict(lambda:defaultdict(float)); names={}
    aliases={'completions':['completions'],'attempts':['attempts'],'passing_yards':['passing_yards'],'passing_tds':['passing_tds'],'interceptions':['interceptions'],'rushing_attempts':['carries','rushing_attempts'],'rushing_yards':['rushing_yards'],'rushing_tds':['rushing_tds']}
    for r in rows:
        if str(r.get('season'))!='2024': continue
        nm=r.get('player_display_name') or r.get('player_name') or r.get('player') or ''
        k=norm_name(nm)
        if not k: continue
        names[k]=nm
        for stat,cols in aliases.items():
            for c in cols:
                if c in r and r[c] not in ('',None):
                    try: agg[k][stat]+=float(r[c]); break
                    except: pass
    return dict(agg)

def mae(vals): return sum(abs(a-p) for p,a in vals)/len(vals)

def main():
    cbs,fft,act=parse_cbs(),parse_fft(),actuals()
    common=sorted(set(cbs)&set(fft)&set(act))
    detail={}; wins={'CBS':0,'FFToday':0,'tie':0,'equal_weight':0}
    for s in STATS:
        triples=[]; blend=[]
        for n in common:
            if s not in act[n]: continue
            a=act[n][s]; triples.append((cbs[n][s],fft[n][s],a)); blend.append(((cbs[n][s]+fft[n][s])/2,a))
        if not triples: continue
        c=mae([(x[0],x[2]) for x in triples]); f=mae([(x[1],x[2]) for x in triples]); b=mae(blend)
        best=min(c,f,b)
        winner='CBS' if c==best else ('FFToday' if f==best else 'equal_weight')
        wins[winner]+=1
        detail[s]={'n':len(triples),'cbs_mae':c,'fftoday_mae':f,'equal_weight_mae':b,'winner':winner}
    out={'season':2024,'position':'QB','status':'RESEARCH_ONLY','common_players':len(common),'detail':detail,'wins':wins,'production_authority':False,'precedence_note':'Recent raw-stat evidence outranks 2014 category evidence.'}
    Path('/tmp/cbs_fftoday_2024_qb_scorecard.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=2))
if __name__=='__main__': main()
