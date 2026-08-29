#!/usr/bin/env python3
from __future__ import annotations
import json, urllib.parse, urllib.request

base='https://www.fftoday.com/rankings/playerproj.php?'
variants=[
    {'LeagueID':'1','PosID':'10','Season':'2021','cur_page':'0','order_by':'FName','sort_order':'ASC'},
    {'LeagueID':'1_1','PosID':'10','Season':'2021','cur_page':'0','order_by':'FName','sort_order':'ASC'},
    {'LeagueID':'','PosID':'10','Season':'2021','cur_page':'0','order_by':'FName','sort_order':'ASC'},
    {'LeagueID':'1/','PosID':'10','Season':'2021','cur_page':'0','order_by':'LName','sort_order':'ASC'},
]
headers={
    'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36',
    'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language':'en-US,en;q=0.9',
    'Referer':'https://www.fftoday.com/',
    'Connection':'close',
}
out=[]
for v in variants:
    url=base+urllib.parse.urlencode(v)
    try:
        req=urllib.request.Request(url,headers=headers)
        with urllib.request.urlopen(req,timeout=30) as r:
            body=r.read(1000).decode('latin-1',errors='replace')
            out.append({'params':v,'status':getattr(r,'status',200),'bytes_sample':len(body),'has_qb':('Quarterback Projections' in body)})
    except Exception as e:
        out.append({'params':v,'error':repr(e)})
print(json.dumps(out,indent=2))
