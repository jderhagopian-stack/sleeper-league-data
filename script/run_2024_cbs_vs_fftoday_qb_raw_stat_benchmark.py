#!/usr/bin/env python3
from __future__ import annotations
import csv, json, re, sys, unicodedata, urllib.parse, urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_native_projection_nflverse_benchmark import fetch_csv, normalize_season

CBS_URL='https://docs.google.com/spreadsheets/d/e/2PACX-1vRSLbPOpHYIzmkYX2H2QPlQVb2kvJVvZ0GRF5EPkQ_EuqCNZ-YG8I3CGC6eINcxqkfwufN-pZUaRb3t/pub?gid=0&single=true&output=csv'
FFT_URL='https://www.fftoday.com/rankings/playerproj.php'
STATS=['completions','attempts','passing_yards','passing_tds','interceptions','rushing_attempts','rushing_yards','rushing_tds']
TEAM_CODES={'ARI','ATL','BAL','BUF','CAR','CHI','CIN','CLE','DAL','DEN','DET','GB','HOU','IND','JAC','JAX','KC','LAC','LAR','LV','MIA','MIN','NE','NO','NYG','NYJ','PHI','PIT','SEA','SF','TB','TEN','WAS'}
LAYOUT=[('completions',1),('attempts',2),('passing_yards',3),('passing_tds',4),('interceptions',5),('rushing_attempts',6),('rushing_yards',7),('rushing_tds',8)]

def norm_name(v:str)->str:
    v=unicodedata.normalize('NFKD',v or '')
    v=''.join(c for c in v if not unicodedata.combining(c)).lower()
    v=re.sub(r'\b(jr|sr|ii|iii|iv|v)\b\.?','',v)
    return re.sub(r'[^a-z0-9]+','',v)

def num(v:str)->float:
    m=re.search(r'-?\d+(?:\.\d+)?',(v or '').replace(',',''))
    if not m: raise ValueError(v)
    return float(m.group())

def get(url:str)->bytes:
    req=urllib.request.Request(url,headers={
        'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36',
        'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language':'en-US,en;q=0.9','Referer':'https://www.fftoday.com/','Connection':'close'})
    with urllib.request.urlopen(req,timeout=90) as r: return r.read()

def parse_cbs()->dict:
    rows=list(csv.reader(get(CBS_URL).decode('utf-8-sig').splitlines()))
    expected=['Rank','QB','FPTs','ATT','Y/A','YDS','TD','INT','RU','Y/A','RU YDS','TD']
    if not rows or rows[0][:12] != expected:
        raise SystemExit(f'CBS header changed/unverified: {rows[0][:12] if rows else None}')
    out={}
    for r in rows[1:]:
        if len(r)<19: continue
        name=norm_name(r[1])
        if not name: continue
        try:
            out[name]={'attempts':float(r[3]),'completions':float(r[18]),'passing_yards':float(r[5]),'passing_tds':float(r[6]),'interceptions':float(r[7]),'rushing_attempts':float(r[8]),'rushing_yards':float(r[10]),'rushing_tds':float(r[11])}
        except Exception: continue
    return out

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows=[]; self.row=None; self.cell=None; self.in_cell=False
    def handle_starttag(self,tag,attrs):
        if tag=='tr': self.row=[]
        elif tag in ('td','th') and self.row is not None:
            self.cell={'text':[],'anchors':[]}; self.in_cell=True
        elif tag=='a' and self.in_cell and self.cell is not None:
            self.cell['_anchor']=[]
    def handle_data(self,data):
        if self.in_cell and self.cell is not None:
            self.cell['text'].append(data)
            if '_anchor' in self.cell: self.cell['_anchor'].append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.in_cell and self.cell is not None and '_anchor' in self.cell:
            txt=' '.join(' '.join(self.cell.pop('_anchor')).split())
            if txt: self.cell['anchors'].append(txt)
        elif tag in ('td','th') and self.in_cell and self.row is not None and self.cell is not None:
            self.cell['text']=' '.join(' '.join(self.cell['text']).split()); self.row.append(self.cell); self.cell=None; self.in_cell=False
        elif tag=='tr' and self.row is not None:
            if self.row: self.rows.append(self.row)
            self.row=None

def player_from_cell(cell:dict)->str:
    bad={'upside','risk','player','sort first','last'}
    for a in cell.get('anchors',[]):
        x=' '.join(a.split())
        if any(ch.isalpha() for ch in x) and x.lower() not in bad and len(x)>2: return x
    text=cell.get('text','')
    text=re.split(r'\b(?:Upside|Risk):?',text,maxsplit=1)[0]
    return re.sub(r'\bImage:?\s*$','',text).strip()

def parse_fft_page(html:str)->list[dict]:
    p=TableParser(); p.feed(html); out=[]
    for row in p.rows:
        cells=[c.get('text','').strip() for c in row]
        ti=next((i for i,x in enumerate(cells) if x in TEAM_CODES),None)
        if ti is None or ti<1 or ti+2>=len(cells): continue
        player=player_from_cell(row[ti-1])
        if not player or not any(ch.isalpha() for ch in player): continue
        tail=cells[ti+1:]
        try:
            vals={'player_name':player}
            for stat,idx in LAYOUT: vals[stat]=num(tail[idx])
        except (IndexError,ValueError): continue
        out.append(vals)
    dedup={norm_name(r['player_name']):r for r in out}
    return list(dedup.values())

def parse_fft()->dict:
    out={}; seen=set()
    for page in range(10):
        q=urllib.parse.urlencode({'LeagueID':1,'PosID':10,'Season':2024,'cur_page':page,'order_by':'FName','sort_order':'ASC'})
        html=get(FFT_URL+'?'+q).decode('latin-1','replace')
        if page==0:
            txt=re.sub(r'<[^>]+>',' ',html)
            m=re.search(r'Updated:\s*(\d{1,2}/\d{1,2}/\d{4})',txt,re.I)
            if not m or m.group(1)!='9/2/2024': raise SystemExit(f'FFToday 2024 snapshot date changed/unverified: {m.group(1) if m else None}')
        rows=parse_fft_page(html); added=0
        for r in rows:
            k=norm_name(r['player_name'])
            if k not in seen:
                seen.add(k); out[k]={s:float(r[s]) for s in STATS}; added+=1
        if page>0 and added==0: break
    if len(out)<30: raise SystemExit(f'FFToday QB parse unexpectedly small: {len(out)}')
    # Unit/column guards catch shifted-table parsing before any scorecard can be accepted.
    sample=list(out.values())
    med=lambda xs: sorted(xs)[len(xs)//2]
    if med([r['attempts'] for r in sample]) < 100 or med([r['completions'] for r in sample]) < 60 or med([r['passing_yards'] for r in sample]) < 1000:
        raise SystemExit('FFToday QB parser sanity check failed: likely shifted stat columns')
    return out

def actuals()->dict:
    rows=normalize_season(fetch_csv(2024),2024); out={}
    for r in rows:
        if r.get('position')!='QB': continue
        name=norm_name(r.get('player_name',''))
        if not name: continue
        out[name]={'attempts':float(r.get('attempts',0) or 0),'completions':float(r.get('completions',0) or 0),'passing_yards':float(r.get('passing_yards',0) or 0),'passing_tds':float(r.get('passing_tds',0) or 0),'interceptions':float(r.get('interceptions',0) or 0),'rushing_attempts':float(r.get('carries',r.get('rushing_attempts',0)) or 0),'rushing_yards':float(r.get('rushing_yards',0) or 0),'rushing_tds':float(r.get('rushing_tds',0) or 0)}
    return out

def mae(vals): return sum(abs(a-p) for p,a in vals)/len(vals)

def main():
    cbs,fft,act=parse_cbs(),parse_fft(),actuals(); common=sorted(set(cbs)&set(fft)&set(act))
    if len(common)<20: raise SystemExit(f'common-player join unexpectedly small: cbs={len(cbs)} fft={len(fft)} actual={len(act)} common={len(common)}')
    detail={}; wins={'CBS':0,'FFToday':0,'tie':0,'equal_weight':0}
    for s in STATS:
        triples=[(cbs[n][s],fft[n][s],act[n][s]) for n in common]
        c=mae([(x[0],x[2]) for x in triples]); f=mae([(x[1],x[2]) for x in triples]); b=mae([((x[0]+x[1])/2,x[2]) for x in triples])
        best=min(c,f,b); winner='CBS' if c==best else ('FFToday' if f==best else 'equal_weight'); wins[winner]+=1
        detail[s]={'n':len(triples),'cbs_mae':c,'fftoday_mae':f,'equal_weight_mae':b,'winner':winner,'equal_weight_improvement_vs_best_single_pct':100*(min(c,f)-b)/min(c,f)}
    out={'season':2024,'position':'QB','status':'RESEARCH_ONLY','common_players':len(common),'detail':detail,'wins':wins,'production_authority':False,'precedence_note':'Recent raw-stat evidence outranks 2014 category evidence. Single-season results remain insufficient for learned production weights.'}
    Path('/tmp/cbs_fftoday_2024_qb_scorecard.json').write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
