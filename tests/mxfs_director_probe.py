import json,collections,sys
D='/src/mxfs/.ccdirector/'
ev=[]
for l in open(D+'events.jsonl'):
    try: ev.append(json.loads(l))
    except Exception: pass
print('events',len(ev),'first',ev[0]['time'],'last',ev[-1]['time'])
# gaps
prev=None
for i,e in enumerate(ev):
    t=e['time']
    if prev and t[:13]!=prev[:13] and t[:10]>=prev[:10]:
        pass
    prev=t
ws=[(i,e['time']) for i,e in enumerate(ev) if e['kind']=='items_returned']
print('items_returned',ws)
pa=[(i,e['time']) for i,e in enumerate(ev) if e['kind']=='plan_accepted']
print('plan_accepted last 8',pa[-8:])
print('plan_accepted on 2026-11-17',[t for i,t in pa if t>='2026-11-17'])
# start: first worker_started after last big gap
big=[]
for a,b in zip(range(len(ev)-1),range(1,len(ev))):
    from datetime import datetime as dt
    d=(dt.fromisoformat(ev[b]['time'])-dt.fromisoformat(ev[a]['time'])).total_seconds()
    if d>1800: big.append((b,ev[a]['time'],ev[b]['time'],ev[b]['kind']))
print('gaps>30min',big[-6:])
start=big[-1][0] if big else 0
sub=ev[start:]
print('SINCE idx',start,sub[0]['time'])
print(collections.Counter(e['kind'] for e in sub))
for e in sub:
    if e['kind']=='plan_accepted' and e['time']>='2026-11-17T20:00':
        ch=e.get('changes',[]);print('PLAN',e['time'],len(ch),[str(c)[:100] for c in ch[:15]])
cl=collections.defaultdict(list);fin={};
for e in sub:
    k=e['kind']
    if k=='item_claimed': cl[e.get('item')].append(e.get('worker') or e.get('by'))
    if k=='item_done': fin[e.get('item')]=e.get('worker') or e.get('by')
print('claimed items',len(cl),'total claims',sum(len(v) for v in cl.values()))
print('most claimed',sorted(((len(v),k,collections.Counter(v)) for k,v in cl.items()),key=lambda x:-x[0])[:12])
print('done',fin)
print('check_failed',[e for e in sub if e['kind']=='item_check_failed'])
pk=[e for e in sub if e['kind']=='item_parked']
print('parked',len(pk),collections.Counter(e.get('item') for e in pk).most_common(10))
seen=collections.Counter()
for e in pk: seen[str(e.get('reason') or e.get('note'))[:200]]+=1
print('park reasons',seen.most_common(10)); print('sample park keys',pk[:1])
for k in ('item_waiting','item_resumed','supervisor_error','gate_refused','goal_done','goal_stuck','workers_failing','item_released'):
    x=[e for e in sub if e['kind']==k]; print(k,len(x),[str(e)[:250] for e in x[:3]])
wc=collections.defaultdict(collections.Counter)
for e in sub:
    if e['kind'].startswith('worker_'): wc[e.get('worker') or e.get('id')][e['kind']]+=1
print(dict(wc))
print('sample worker_started',[e for e in sub if e['kind']=='worker_started'][:1],[e for e in sub if e['kind']=='worker_exited'][:1])
print('last 5',[str(e)[:200] for e in sub[-5:]])
# plan
p=json.load(open(D+'plan.json'))
items=p['items'] if isinstance(p,dict) and 'items' in p else p
if isinstance(items,dict): items=list(items.values())
print('PLAN keys',list(p.keys()) if isinstance(p,dict) else None,'items',len(items))
print('item keys',collections.Counter(k for it in items for k in it))
print('resources/interface',sum(1 for it in items if 'resources' in it or 'interface' in it))
ct=[it['check_timeout'] for it in items if 'check_timeout' in it]; print('check_timeout',len(ct),max(ct) if ct else None)
print('size',collections.Counter(str(it.get('size')) for it in items))
print('empty deps',sum(1 for it in items if not it.get('depends_on')))
for it in items[:5]: print(it.get('id'),'|',str(it.get('what'))[:150],'|',str(it.get('check'))[:150])
s=json.load(open(D+'state.json'))
print('STATE keys',list(s.keys()))
si=s.get('items',s)
print(collections.Counter(v.get('status') for v in si.values()))
print('sample',json.dumps(next(iter(si.values())))[:300])
done={k for k,v in si.items() if v.get('status')=='done'}
byid={it['id']:it for it in items}
ready=[k for k,v in si.items() if v.get('status')=='pending' and all(d in done for d in byid.get(k,{}).get('depends_on',[]))]
print('ready',len(ready),ready[:10])
for k,v in si.items():
    if v.get('status') in('claimed',): print('CLAIMED',k,v.get('worker') or v.get('claimed_by'))
    if v.get('status') in('parked','waiting'): print(v['status'].upper(),k,str(v.get('note'))[:200])
print('consult_required',[k for k,v in si.items() if v.get('consult_required')])
