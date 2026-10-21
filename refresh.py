#!/usr/bin/env python3
"""Re-pull 2026 Arizona Fall League stats from MLB Stats API and regenerate data.json."""
import json, csv, re, unicodedata, datetime, collections, urllib.request, concurrent.futures as cf, os
API="https://statsapi.mlb.com/api/v1"; SEASON=2026
HERE=os.path.dirname(os.path.abspath(__file__))
RANK_FILES={"pitcher":os.path.join(HERE,"data","pitchers.csv"),"hitter":os.path.join(HERE,"data","hitters.csv")}
OUT=os.path.join(os.path.dirname(os.path.abspath(__file__)),"data.json")
def get(u):
    with urllib.request.urlopen(u,timeout=30) as r: return json.load(r)
def norm(s):
    s=unicodedata.normalize("NFKD",s).encode("ascii","ignore").decode().lower()
    s=re.sub(r"\b(jr|sr|ii|iii|iv)\b","",s); return re.sub(r"[^a-z]","",s)

teams={t["id"]:t for t in get(f"{API}/teams?sportId=1&season={SEASON}")["teams"]}
sched=get(f"{API}/schedule?sportId=17&leagueId=119&season={SEASON}")
games=[g for d in sched["dates"] for g in d["games"]
       if g["status"]["abstractGameState"]=="Final" and g["status"]["detailedState"]!="Postponed"]
games={g["gamePk"]:g for g in games}.values()
H=collections.defaultdict(lambda: collections.Counter()); P=collections.defaultdict(lambda: collections.Counter())
names={}; issues=[]
def box(pk): return pk,get(f"{API}/game/{pk}/boxscore")
with cf.ThreadPoolExecutor(8) as ex: boxes=list(ex.map(box,[g["gamePk"] for g in games]))
for pk,b in boxes:
    for side in ("home","away"):
        for p in b["teams"][side]["players"].values():
            pid=p["person"]["id"]; names[pid]=p["person"]["fullName"]; st=p.get("stats",{})
            h=st.get("batting",{}); pi=st.get("pitching",{})
            if h and (h.get("plateAppearances",0) or h.get("atBats",0) or h.get("gamesPlayed")):
                c=H[pid]; c["G"]+=1
                for k,a in [("PA","plateAppearances"),("AB","atBats"),("H","hits"),("2B","doubles"),("3B","triples"),("HR","homeRuns"),("R","runs"),("RBI","rbi"),("BB","baseOnBalls"),("SO","strikeOuts"),("SB","stolenBases"),("HBP","hitByPitch"),("SF","sacFlies")]: c[k]+=h.get(a,0)
            if pi and (pi.get("outs",0) or pi.get("battersFaced",0)):
                c=P[pid]; c["G"]+=1; c["GS"]+=pi.get("gamesStarted",0)
                for k,a in [("OUTS","outs"),("H","hits"),("R","runs"),("ER","earnedRuns"),("BB","baseOnBalls"),("SO","strikeOuts"),("HR","homeRuns")]: c[k]+=pi.get(a,0)
ids=list(set(H)|set(P)); org={}
for i in range(0,len(ids),100):
    for p in get(f"{API}/people?personIds={','.join(map(str,ids[i:i+100]))}&hydrate=currentTeam")["people"]:
        org[p["id"]]=(p.get("currentTeam") or {}).get("parentOrgId")
for pid in ids:
    if org.get(pid) not in teams:
        try:
            re_=get(f"{API}/people/{pid}?hydrate=rosterEntries")["people"][0].get("rosterEntries",[])
            po=[r["team"].get("parentOrgId") or r["team"]["id"] for r in re_ if (r["team"].get("parentOrgId") or r["team"]["id"]) in teams]
            org[pid]=po[0] if po else None
        except Exception: org[pid]=None
        if org[pid] is None: issues.append(f"No MLB org found for {names[pid]} ({pid})")
# rankings: both files share one overall rank scale; matched by normalized name + org nickname
ranks={}; rank_rows={}
for kind,f in RANK_FILES.items():
    for r in csv.DictReader(open(f,encoding="utf-8",errors="replace")):
        key=(norm(r["player_name"]),r["organization"].strip().lower())
        rank_rows.setdefault(norm(r["player_name"]),[]).append((r,kind))
        ranks[key]={"rank":int(r["rank"]),"list":kind,"pos":r["positions"],"eta":r["eta"]}
matched={}; ambiguous=[]; namedOnly=[]
for pid in ids:
    t=teams.get(org.get(pid)); nick=(t["teamName"].lower() if t else "")
    n=norm(names[pid]); k=(n,nick)
    full=norm(t["name"]) if t else ""
    hits=[c for c in rank_rows.get(n,[]) if full and norm(c[0]["organization"]) in full]
    if len(hits)==1: matched[pid]=ranks[(n,hits[0][0]["organization"].strip().lower())]
    elif len(hits)>1: ambiguous.append(f"{names[pid]}: multiple rank rows {[h[0]['rank'] for h in hits]}")
    elif n in rank_rows:
        cands=rank_rows[n]
        namedOnly.append(f"{names[pid]} ({t['abbreviation'] if t else '?'}) name matches {[c[0]['player_name']+'/'+c[0]['organization'] for c in cands]} but org differs - NOT matched")
def ip(o): return f"{o//3}.{o%3}"
hit=[];pit=[]
for pid,c in H.items():
    t=teams.get(org.get(pid)); ab=c["AB"]
    obp_d=ab+c["BB"]+c["HBP"]+c["SF"]; tb=c["H"]+c["2B"]+2*c["3B"]+3*c["HR"]
    avg=c["H"]/ab if ab else 0; obp=(c["H"]+c["BB"]+c["HBP"])/obp_d if obp_d else 0; slg=tb/ab if ab else 0
    hit.append(dict(id=pid,name=names[pid],org=t["abbreviation"] if t else "—",rank=matched.get(pid),**{k:c[k] for k in "G PA AB H 2B 3B HR R RBI BB SO SB".split()},AVG=round(avg,3),OBP=round(obp,3),SLG=round(slg,3),OPS=round(obp+slg,3)))
for pid,c in P.items():
    t=teams.get(org.get(pid)); inn=c["OUTS"]/3
    pit.append(dict(id=pid,name=names[pid],org=t["abbreviation"] if t else "—",rank=matched.get(pid),G=c["G"],GS=c["GS"],IP=ip(c["OUTS"]),IPd=round(inn,3),**{k:c[k] for k in "H R ER BB SO HR".split()},
        ERA=round(9*c["ER"]/inn,2) if inn else None,WHIP=round((c["H"]+c["BB"])/inn,2) if inn else None,K9=round(9*c["SO"]/inn,2) if inn else None,BB9=round(9*c["BB"]/inn,2) if inn else None))
# hitter-ranked players appearing only as pitchers etc are fine; count ranked rows present in AFL
total_rank_rows=sum(len(v) for v in rank_rows.values())
data=dict(updated=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),games=len(boxes),hitters=hit,pitchers=pit,
    meta=dict(players=len(ids),matched=len(matched),matched_hitter_list=sum(1 for m in matched.values() if m["list"]=="hitter"),
    matched_pitcher_list=sum(1 for m in matched.values() if m["list"]=="pitcher"),rank_rows=total_rank_rows,name_only=namedOnly,ambiguous=ambiguous,issues=issues))
json.dump(data,open(OUT,"w"))
print(json.dumps({k:v for k,v in data.items() if k not in("hitters","pitchers")},indent=1))
