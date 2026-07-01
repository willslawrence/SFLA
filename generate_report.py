#!/usr/bin/env python3
"""
SFLA Monthly Report Generator (multi-area, v2).
Reads SFLA Sites v2, filters to one area, writes a THC-styled PDF to the
OneDrive "SFLA Monthly Reports" folder.

Run:
  python3 generate_report.py <area> [year] [month]
    <area> = uam | malham | city-tour | najd | all
    year/month optional -> defaults to the current month.
  e.g.  python3 generate_report.py uam 2026 6
        python3 generate_report.py all          (all four, current month)

Reads come from the Cloudflare Worker (token held server-side) — no Airtable token needed here.
"""
import json, sys, os
from datetime import datetime, timedelta
from fpdf import FPDF
import urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(HERE, 'thc_logo.png')
WORKER = 'https://sfla-write.thehelicopter.workers.dev'  # reads come from the Worker; no Airtable token in this client
OUT_DIR = ('/Users/willlawrence/Library/CloudStorage/OneDrive-TheHelicopterCompany/'
           'H125 Pilots - Documents/Missions/Riyadh UAM/SFLA Monthly Reports')

AREAS = {
    'uam':       ('UAM',       'UAM'),
    'malham':    ('Malham',    'Malham'),
    'city-tour': ('City Tour', 'City Tour'),
    'najd':      ('NAJD',      'NAJD'),
}

DARK=(40,40,40); MID=(120,120,120); LIGHT=(200,200,200); ROW_ALT=(248,248,248)
STATUS_COLORS={'Suitable':(76,175,80),'Unsuitable':(107,114,128),'New SFLA':(33,150,243),'Pending Recheck':(227,181,5)}

def _worker_get(url):
    # Cloudflare 403s the default Python-urllib UA, so present a normal one.
    req=urllib.request.Request(url, headers={'User-Agent':'thc-sfla-report/1.0'})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def worker_sites():
    """{name: {status,lastChecked,checkCount,notes,areas}} — live, from the Worker."""
    return _worker_get(WORKER).get('sites', {})

def worker_changelog(s, e):
    """Status-change log between s and e (datetimes), from the Worker."""
    qs=urllib.parse.urlencode({'log':'1','from':s.strftime('%Y-%m-%dT00:00:00'),'to':e.strftime('%Y-%m-%dT00:00:00')})
    return _worker_get(f'{WORKER}/?{qs}').get('changeLog', [])

def month_range(y,m):
    s=datetime(y,m,1); e=datetime(y+(1 if m==12 else 0),(m%12)+1,1); return s,e

def screenshot_map(slug):
    """Headless-Chrome capture of the area map (satellite + status-coloured pads + routes)."""
    import subprocess, tempfile
    chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome): return None
    out=tempfile.mktemp(suffix='.png')
    url=f"file://{HERE}/mapshot.html?area={slug}"
    try:
        subprocess.run([chrome,"--headless","--disable-gpu","--hide-scrollbars",
            f"--screenshot={out}","--window-size=1000,1000","--virtual-time-budget=12000",
            "--allow-file-access-from-files",url], capture_output=True, timeout=90)
    except Exception as ex:
        print("map shot failed:",ex); return None
    return out if os.path.exists(out) else None

class SFLAReport(FPDF):
    def __init__(self, title, month_str):
        super().__init__(); self.title_str=title; self.month_str=month_str
        self.set_auto_page_break(auto=True, margin=15)
    def header(self):
        if os.path.exists(LOGO_PATH): self.image(LOGO_PATH, x=172, y=8, w=25)
        self.set_xy(10,8); self.set_font('Helvetica','B',16); self.set_text_color(*DARK)
        self.cell(0,7,self.title_str, new_x="LMARGIN", new_y="NEXT")
        self.set_font('Helvetica','',10); self.set_text_color(*MID)
        self.cell(0,5,self.month_str, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*LIGHT); self.set_line_width(0.3)
        self.line(10,self.get_y()+2,165,self.get_y()+2); self.ln(6)
    def footer(self):
        self.set_y(-12); self.set_font('Helvetica','I',7); self.set_text_color(*MID)
        self.cell(95,8,f'Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} | THC SFLA Tracker')
        self.cell(95,8,f'Page {self.page_no()}', align='R')
    def section_title(self,t):
        self.set_font('Helvetica','B',11); self.set_text_color(*DARK)
        self.cell(0,7,t, new_x="LMARGIN", new_y="NEXT"); self.ln(1)
    def status_dot(self,status):
        r,g,b=STATUS_COLORS.get(status,MID); self.set_fill_color(r,g,b)
        self.ellipse(self.get_x()+1,self.get_y()+1.5,3,3,style='F'); self.set_x(self.get_x()+6)

def generate(area_slug, year, month):
    label, tag = AREAS[area_slug]
    s,e = month_range(year,month); month_str=s.strftime('%B %Y')
    print(f'Generating {label} SFLA report for {month_str}...')
    sites=worker_sites()
    sd=[]; total_checks=0; area_names=set()
    for nm,info in sites.items():
        if tag not in (info.get('areas') or []): continue
        area_names.add(nm); total_checks+=info.get('checkCount',0)
        sd.append({'name':nm,'status':info.get('status','Unknown'),'last_checked':info.get('lastChecked') or ''})
    sd.sort(key=lambda x:x['name'])

    changes=worker_changelog(s,e)  # both historical logs, filtered to the month, served by the Worker
    cd=[]
    for c in changes:
        if c.get('name') not in area_names: continue
        if (c.get('prev') or '')==(c.get('new') or ''): continue  # only ACTUAL status changes, not re-checks
        cd.append({'name':c.get('name',''),'timestamp':c.get('timestamp',''),'prev':c.get('prev',''),'new':c.get('new',''),'notes':c.get('notes','')})
    cd.sort(key=lambda x:x['timestamp'], reverse=True)

    counts={}
    for x in sd: counts[x['status']]=counts.get(x['status'],0)+1
    total=len(sd)

    pdf=SFLAReport(f'{label} SFLA Report', month_str); pdf.add_page()
    pdf.section_title('Summary'); pdf.set_font('Helvetica','',10); pdf.set_text_color(*DARK)
    pdf.cell(0,6,f'Area: {label}', new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0,6,f'Total SFLA Shapes: {total}', new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0,6,f'Total Status Changes: {len(cd)}', new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0,6,f'Total Checks Completed: {total_checks}', new_x="LMARGIN", new_y="NEXT"); pdf.ln(3)
    for st in ['Suitable','Unsuitable','Pending Recheck','New SFLA']:
        c=counts.get(st,0)
        if not c: continue
        pct=round(c/total*100,1) if total else 0
        pdf.status_dot(st); pdf.set_font('Helvetica','',10); pdf.set_text_color(*DARK)
        pdf.cell(0,6,f'{st}: {c} ({pct}%)', new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # === SITE MAP OVERVIEW ===
    shot=screenshot_map(area_slug)
    if shot:
        pdf.section_title('Site Map Overview')
        pdf.image(shot, x=(210-150)/2, w=150)
        pdf.ln(4)
        try: os.remove(shot)
        except OSError: pass

    pdf.section_title(f'Change Log - {month_str}')
    if not cd:
        pdf.set_font('Helvetica','I',9); pdf.set_text_color(*MID)
        pdf.cell(0,6,'No status changes this month.', new_x="LMARGIN", new_y="NEXT")
    else:
        clw=[30,18,25,25,92]
        pdf.set_font('Helvetica','B',7); pdf.set_text_color(*MID)
        for i,h in enumerate(['Date','SFLA','From','To','Notes']): pdf.cell(clw[i],5,h, align='C' if i<4 else 'L')
        pdf.ln(); pdf.set_draw_color(*LIGHT); pdf.set_line_width(0.2); pdf.line(10,pdf.get_y(),200,pdf.get_y())
        pdf.set_font('Helvetica','',7)
        for i,c in enumerate(cd):
            fill = i%2==0
            if fill: pdf.set_fill_color(*ROW_ALT)
            ts=c['timestamp'][:16].replace('T',' ') if c['timestamp'] else ''
            pdf.set_text_color(*DARK); pdf.cell(clw[0],5,ts,fill=fill); pdf.cell(clw[1],5,c['name'],fill=fill,align='C')
            for val,w in [(c['prev'],clw[2]),(c['new'],clw[3])]:
                r,g,b=STATUS_COLORS.get(val,MID); pdf.set_text_color(r,g,b); pdf.set_font('Helvetica','B',7); pdf.cell(w,5,val,fill=fill,align='C')
            pdf.set_font('Helvetica','',7); pdf.set_text_color(*DARK); pdf.cell(clw[4],5,(c.get('notes','') or '')[:65],fill=fill); pdf.ln()
    pdf.ln(4)

    if pdf.get_y()>210: pdf.add_page()
    pdf.section_title('Current SFLA Status')
    col_sets=3; gap=5; usable=190-(gap*(col_sets-1)); gw=usable/col_sets
    nw=gw*0.28; stw=gw*0.35; dw=gw*0.37
    def headers():
        pdf.set_font('Helvetica','B',6); pdf.set_text_color(*MID)
        for c in range(col_sets):
            pdf.cell(nw,4,'SFLA',align='C'); pdf.cell(stw,4,'Status',align='C'); pdf.cell(dw,4,'Last Check',align='C')
            if c<col_sets-1: pdf.cell(gap,4,'')
        pdf.ln(); pdf.set_draw_color(*LIGHT); pdf.set_line_width(0.2); pdf.line(10,pdf.get_y(),200,pdf.get_y()); pdf.ln(0.5)
    headers()
    rpc=-(-len(sd)//col_sets); pdf.set_font('Helvetica','',6)
    for row in range(rpc):
        if pdf.get_y()>275: pdf.add_page(); headers(); pdf.set_font('Helvetica','',6)
        fill = row%2==0
        if fill: pdf.set_fill_color(*ROW_ALT)
        for col in range(col_sets):
            idx=col*rpc+row
            if idx<len(sd):
                x=sd[idx]; pdf.set_text_color(*DARK); pdf.cell(nw,4,x['name'],fill=fill,align='C')
                r,g,b=STATUS_COLORS.get(x['status'],MID); pdf.set_text_color(r,g,b); pdf.set_font('Helvetica','B',6); pdf.cell(stw,4,x['status'],fill=fill,align='C')
                pdf.set_font('Helvetica','',6); pdf.set_text_color(*DARK); pdf.cell(dw,4,x['last_checked'],fill=fill,align='C')
            else: pdf.cell(nw+stw+dw,4,'',fill=False)
            if col<col_sets-1: pdf.cell(gap,4,'',fill=False)
        pdf.ln()

    os.makedirs(OUT_DIR, exist_ok=True)
    out=os.path.join(OUT_DIR, f'SFLA_{label.replace(" ","")}_{s.strftime("%Y-%m")}.pdf')
    pdf.output(out); print('Saved:', out); return out

if __name__=='__main__':
    args=sys.argv[1:]
    now=datetime.now()
    area=args[0] if args else 'all'
    if len(args)>=3: y,m=int(args[1]),int(args[2])
    else: y,m=now.year,now.month
    slugs=list(AREAS) if area=='all' else [area]
    for sl in slugs:
        if sl not in AREAS: raise SystemExit(f'Unknown area "{sl}". Use: {", ".join(AREAS)} or all')
        generate(sl,y,m)
