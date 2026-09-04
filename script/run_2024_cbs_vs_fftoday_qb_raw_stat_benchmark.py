#!/usr/bin/env python3
from __future__ import annotations
import csv, json, re, sys, unicodedata, urllib.parse, urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_native_projection_nflverse_benchmark import fetch_csv, normalize_season

FFT_URL='https://www.fftoday.com/rankings/playerproj.php'
TEAM_CODES={'ARI','ATL','BAL','BUF','CAR','CHI','CIN','CLE','DAL','DEN','DET','GB','HOU','IND','JAC','JAX','KC','LAC','LAR','LV','MIA','MIN','NE','NO','NYG','NYJ','PHI','PIT','SEA','SF','TB','TEN','WAS'}

CBS_FILES={'QB':'/tmp/cbs_2024_qb.csv','RB':'/tmp/cbs_2024_rb.csv','WR':'/tmp/cbs_2024_wr.csv','TE':'/tmp/cbs_2024_te.csv'}
POS_ID={'QB':10,'RB':20,'WR':30,'TE':40}
STATS={
 'QB':['completions','attempts','passing_yards','passing_tds','interceptions','rushing_attempts','rushing_yards','rushing_tds'],
 'RB':['rushing_attempts','rushing_yards','rushing_tds','receptions','receiving_yards','receiving_tds'],
 'WR':['receptions','receiving_yards','receiving_tds','rushing_attempts','rushing_yards','rushing_tds'],
 'TE':['receptions','receiving_yards','receiving_tds'],
}
FFT_LAYOUT={
 'QB':[('completions',1),('attempts',2),('passing_yards',3),('passing_tds',4),('interceptions',5),('rushing_attempts',6),('rushing_yards',7),('rushing_tds',8)],
 'RB':[('rushing_attempts',1),('rushing_yards',2),('rushing_tds',4),('receptions',5),('receiving_yards',6),('receiving_tds',8)],
 'WR':[('receptions',1),('receiving_yards',2),('receiving_tds',4),('rushing_attempts',5),('rushing_yards',6),('rushing_tds',8)],
 'TE':[('receptions',1),('receiving_yards',2),('receiving_tds',4)],
}
ACTUAL_FIELDS={
 'completions':'completions','attempts':'attempts','passing_yards':'passing_yards','passing_tds':'passing_tds','interceptions':'interceptions',
 'rushing_attempts':'carries','rushing_yards':'rushing_yards','rushing_tds':'rushing_tds','receptions':'receptions','receiving_yards':'receiving_yards','receiving_tds':'receiving_tds'
}

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
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36','Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'en-US,en;q=0.9','Referer':'https://www.fftoday.com/','Connection':'close'})
    with urllib.request.urlopen(req,timeout=90) as r: return r.read()

def parse_cbs(position:str)->dict:
    rows=list(csv.reader(Path(CBS_FILES[position]).open(newline='',encoding='utf-8-sig')))
    out={}
    for r in rows[1:]:
        try:
            if position=='QB':
                name=norm_name(r[1]); vals={'attempts':float(r[3]),'completions':float(r[18]),'passing_yards':float(r[5]),'passing_tds':float(r[6]),'interceptions':float(r[7]),'rushing_attempts':float(r[8]),'rushing_yards':float(r[10]),'rushing_tds':float(r[11])}
            elif position=='RB':
                name=norm_name(r[2]); vals={'rushing_attempts':float(r[5]),'rushing_yards':float(r[7]),'rushing_tds':float(r[8]),'receptions':float(r[10]),'receiving_yards':float(r[12]),'receiving_tds':float(r[13])}
            elif position=='WR':
                name=norm_name(r[2]); vals={'receptions':float(r[6]),'receiving_yards':float(r[8]),'receiving_tds':float(r[9]),'rushing_attempts':float(r[10]),'rushing_yards':float(r[12]),'rushing_tds':float(r[13])}
            else:
                name=norm_name(r[2]); vals={'receptions':float(r[6]),'receiving_yards':float(r[8]),'receiving_tds':float(r[9])}
            if name: out[name]=vals
        except (ValueError,IndexError):
            continue
    minimum={'QB':30,'RB':70,'WR':90,'TE':45}[position]
    if len(out)<minimum: raise SystemExit(f'CBS {position} parse unexpectedly small: {len(out)}')
    return out

class TableParser(HTMLParser):
    def __init__(self): super().__init__(); self.rows=[]; self.row=None; self.cell=None; self.in_cell=False
    def handle_starttag(self,tag,attrs):
        if tag=='tr': self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell={'text':[],'anchors':[]}; self.in_cell=True
        elif tag=='a' and self.in_cell and self.cell is not None: self.cell['_anchor']=[]
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
    text=re.split(r'\b(?:Upside|Risk):?',cell.get('text',''),maxsplit=1)[0]
    return re.sub(r'\bImage:?\s*$','',text).strip()

def parse_fft_page(html:str,position:str)->list[dict]:
    p=TableParser(); p.feed(html); out=[]
    for row in p.rows:
        cells=[c.get('text','').strip() for c in row]
        ti=next((i for i,x in enumerate(cells) if x in TEAM_CODES),None)
        if ti is None or ti<1 or ti+2>=len(cells): continue
        player=player_from_cell(row[ti-1]); tail=cells[ti+1:]
        try:
            vals={'player_name':player}
            for stat,idx in FFT_LAYOUT[position]: vals[stat]=num(tail[idx])
        except (IndexError,ValueError): continue
        if player and any(ch.isalpha() for ch in player): out.append(vals)
    return list({norm_name(r['player_name']):r for r in out}.values())

def median(xs):
    xs=sorted(xs); return xs[len(xs)//2]

def sanity(position:str,out:dict):
    rows=list(out.values())
    if position=='QB':
        ok=median([r['attempts'] for r in rows])>100 and median([r['completions'] for r in rows])>60 and median([r['passing_yards'] for r in rows])>1000
    elif position=='RB':
        ok=median([r['rushing_attempts'] for r in rows])>30 and median([r['rushing_yards'] for r in rows])>100 and median([r['receptions'] for r in rows])>=5
    elif position=='WR':
        ok=median([r['receptions'] for r in rows])>15 and median([r['receiving_yards'] for r in rows])>150
    else:
        ok=median([r['receptions'] for r in rows])>10 and median([r['receiving_yards'] for r in rows])>100
    if not ok: raise SystemExit(f'FFToday {position} parser sanity check failed: likely shifted stat columns')

def parse_fft(position:str)->dict:
    out={}; seen=set()
    for page in range(12):
        q=urllib.parse.urlencode({'LeagueID':1,'PosID':POS_ID[position],'Season':2024,'cur_page':page,'order_by':'FName','sort_order':'ASC'})
        html=get(FFT_URL+'?'+q).decode('latin-1','replace')
        if page==0:
            txt=re.sub(r'<[^>]+>',' ',html); m=re.search(r'Updated:\s*(\d{1,2}/\d{1,2}/\d{4})',txt,re.I)
            if not m or m.group(1)!='9/2/2024': raise SystemExit(f'FFToday 2024 {position} snapshot date changed/unverified: {m.group(1) if m else None}')
        added=0
        for r in parse_fft_page(html,position):
            k=norm_name(r['player_name'])
            if k not in seen:
                seen.add(k); out[k]={s:float(r[s]) for s in STATS[position]}; added+=1
        if page>0 and added==0: break
    minimum={'QB':30,'RB':50,'WR':70,'TE':30}[position]
    if len(out)<minimum: raise SystemExit(f'FFToday {position} parse unexpectedly small: {len(out)}')
    sanity(position,out); return out

def actuals(position:str)->dict:
    rows=normalize_season(fetch_csv(2024),2024); out={}
    for r in rows:
        if r.get('position')!=position: continue
        name=norm_name(r.get('player_name',''))
        if name: out[name]={s:float(r.get(ACTUAL_FIELDS[s],0) or 0) for s in STATS[position]}
    return out

def mae(vals): return sum(abs(a-p) for p,a in vals)/len(vals)

def compare_position(position:str)->dict:
    cbs,fft,act=parse_cbs(position),parse_fft(position),actuals(position)
    common=sorted(set(cbs)&set(fft)&set(act)); min_common={'QB':20,'RB':35,'WR':45,'TE':20}[position]
    if len(common)<min_common: raise SystemExit(f'{position} common-player join unexpectedly small: cbs={len(cbs)} fft={len(fft)} actual={len(act)} common={len(common)}')
    detail={}; wins={'CBS':0,'FFToday':0,'equal_weight':0}
    for s in STATS[position]:
        triples=[(cbs[n][s],fft[n][s],act[n][s]) for n in common]
        c=mae([(x[0],x[2]) for x in triples]); f=mae([(x[1],x[2]) for x in triples]); b=mae([((x[0]+x[1])/2,x[2]) for x in triples])
        best=min(c,f,b); winner='CBS' if c==best else ('FFToday' if f==best else 'equal_weight'); wins[winner]+=1
        detail[s]={'n':len(triples),'cbs_mae':c,'fftoday_mae':f,'equal_weight_mae':b,'winner':winner,'equal_weight_improvement_vs_best_single_pct':100*(min(c,f)-b)/min(c,f)}
    return {'common_players':len(common),'wins':wins,'detail':detail}

def main():
    positions={p:compare_position(p) for p in ['QB','RB','WR','TE']}
    overall={'CBS':0,'FFToday':0,'equal_weight':0}
    for p in positions:
        for k,v in positions[p]['wins'].items(): overall[k]+=v
    out={'schema_version':'1.0','season':2024,'status':'RESEARCH_ONLY_SINGLE_SEASON_RECENT','sources':['CBS','FFToday'],'positions':positions,'overall_category_wins':overall,'production_authority':False,'precedence_note':'Recent raw-stat evidence outranks 2014 category evidence. Single-season results remain insufficient for learned production weights.','next_gate':'Add at least one additional recent season before production category promotion.'}
    Path('/tmp/cbs_fftoday_2024_all_position_scorecard.json').write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
