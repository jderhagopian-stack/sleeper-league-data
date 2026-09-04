#!/usr/bin/env python3
from __future__ import annotations
import csv, json, re, sys, unicodedata, urllib.parse, urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_native_projection_nflverse_benchmark import fetch_csv, normalize_season

CBS_URL='https://docs.google.com/spreadsheets/d/e/2PACX-1vRSLbPOpHYIzmkYX2H2QPlQVb2kvJVvZ0GRF5EPkQ_EuqCNZ-YG8I3CGC6eINcxqkfwufN-pZUaRb3t/pub?gid=0&single=true&output=csv'
FFT_URL='https://www.fftoday.com/rankings/playerproj.php'
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
    rows=list(csv.reader(get(CBS_URL).decode('utf-8-sig').splitlines()))
    expected=['Rank','QB','FPTs','ATT','Y/A','YDS','TD','INT','RU','Y/A','RU YDS','TD']
    if not rows or rows[0][:12] != expected:
        raise SystemExit(f'CBS header changed/unverified: {rows[0][:12] if rows else None}')
    out={}
    for r in rows[1:]:
        if len(r)<12: continue
        name=norm_name(r[1])
        if not name: continue
        try:
            out[name]={'attempts':float(r[3]),'completions':float(r[18]),'passing_yards':float(r[5]),'passing_tds':float(r[6]),'interceptions':float(r[7]),'rushing_attempts':float(r[8]),'rushing_yards':float(r[10]),'rushing_tds':float(r[11])}
        except Exception: continue
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
    out={}; seen=set()
    for page in range(10):
        q=urllib.parse.urlencode({'LeagueID':1,'PosID':10,'Season':2024,'cur_page':page,'order_by':'FName','sort_order':'ASC'})
        html=get(FFT_URL+'?'+q).decode('latin-1','replace')
        if page==0:
            txt=re.sub(r'<[^>]+>',' ',html)
            if not re.search(r'Updated:\s*9/2/2024',txt,re.I): raise SystemExit('FFToday 2024 snapshot date changed/unverified')
        p=T(); p.feed(html)
        teams={'ARI','ATL','BAL','BUF','CAR','CHI','CIN','CLE','DAL','DEN','DET','GB','HOU','IND','JAC','JAX','KC','LAC','LAR','LV','MIA','MIN','NE','NO','NYG','NYJ','PHI','PIT','SEA','SF','TB','TEN','WAS'}
        added=0
        for row in p.rows:
            ti=next((i for i,x in enumerate(row) if x in teams),None)
            if ti is None or ti<1: continue
            name=norm_name(row[ti-1]); tail=row[ti+1:]
            try: vals=[float(re.search(r'-?\d+(?:\.\d+)?',tail[i]).group()) for i in range(1,9)]
            except Exception: continue
            if name and name not in seen:
                seen.add(name); out[name]=dict(zip(STATS,vals)); added+=1
        if page>0 and added==0: break
    return out

def actuals()->dict:
    rows=normalize_season(fetch_csv(2024),2024)
    out={}
    for r in rows:
        if r.get('position')!='QB': continue
        name=norm_name(r.get('player_name',''))
        if not name: continue
        out[name]={'attempts':float(r.get('attempts',0) or 0),'completions':float(r.get('completions',0) or 0),'passing_yards':float(r.get('passing_yards',0) or 0),'passing_tds':float(r.get('passing_tds',0) or 0),'interceptions':float(r.get('interceptions',0) or 0),'rushing_attempts':float(r.get('carries',r.get('rushing_attempts',0)) or 0),'rushing_yards':float(r.get('rushing_yards',0) or 0),'rushing_tds':float(r.get('rushing_tds',0) or 0)}
    return out

def mae(vals): return sum(abs(a-p) for p,a in vals)/len(vals)

def main():
    cbs,fft,act=parse_cbs(),parse_fft(),actuals(); common=sorted(set(cbs)&set(fft)&set(act))
    if len(common)<10: raise SystemExit(f'common-player join unexpectedly small: cbs={len(cbs)} fft={len(fft)} actual={len(act)} common={len(common)}')
    detail={}; wins={'CBS':0,'FFToday':0,'tie':0,'equal_weight':0}
    for s in STATS:
        triples=[(cbs[n][s],fft[n][s],act[n][s]) for n in common]
        c=mae([(x[0],x[2]) for x in triples]); f=mae([(x[1],x[2]) for x in triples]); b=mae([((x[0]+x[1])/2,x[2]) for x in triples])
        best=min(c,f,b); winner='CBS' if c==best else ('FFToday' if f==best else 'equal_weight'); wins[winner]+=1
        detail[s]={'n':len(triples),'cbs_mae':c,'fftoday_mae':f,'equal_weight_mae':b,'winner':winner}
    out={'season':2024,'position':'QB','status':'RESEARCH_ONLY','common_players':len(common),'detail':detail,'wins':wins,'production_authority':False,'precedence_note':'Recent raw-stat evidence outranks 2014 category evidence.'}
    Path('/tmp/cbs_fftoday_2024_qb_scorecard.json').write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
