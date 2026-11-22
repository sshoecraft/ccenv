import json, collections, glob, os, re
D = '/src/mxfs/.ccdirector/'
START = '2026-11-18T01:56:40'
ev = []
bad = 0
for l in open(D + 'events.jsonl'):
    try: ev.append(json.loads(l))
    except Exception: bad += 1
sub = [e for e in ev if e['time'] >= START]
print('events total', len(ev), 'malformed', bad, 'since', START, len(sub), 'first', sub[0]['time'], 'last', sub[-1]['time'])
print('prior event time', [e['time'] for e in ev if e['time'] < START][-1])
print(collections.Counter(e['kind'] for e in sub))
for e in sub:
    if e['kind'] == 'plan_accepted':
        ch = e.get('changes', [])
        print('PLAN', e['time'], len(ch), [str(c)[:120] for c in ch[:10]])
for e in sub: print('EV', json.dumps(e)[:300])
s = json.load(open(D + 'state.json'))
si = s['items']
print('STATUS', collections.Counter(v.get('status') for v in si.values()), 'of', len(si))
for k, v in si.items():
    if v.get('status') not in ('pending', 'done'): print('HELD', k, v.get('status'), v.get('worker'), v.get('claimed'))
    if v.get('consult_required'): print('CONSULT_REQ', k, v.get('fails_in_a_row'), v.get('parks'))
p = json.load(open(D + 'plan.json'))
items = p['items'] if isinstance(p, dict) else p
byid = {it['id']: it for it in items}
done = {k for k, v in si.items() if v.get('status') == 'done'}
pend = [k for k, v in si.items() if v.get('status') == 'pending']
ready = [k for k in pend if all(d in done for d in byid.get(k, {}).get('depends_on', []))]
print('pending', len(pend), 'pending with all deps done', len(ready), 'plan items', len(items), 'state items not in plan', [k for k in si if k not in byid][:5])
# done items with finished/claimed time since start
print('items with claimed>=start', [(k, v['status'], v.get('worker'), v.get('claimed'), v.get('finished')) for k, v in si.items() if (v.get('claimed') or '') >= START])
# resources
rl = []
for l in open(D + 'resources.log'):
    try: rl.append(json.loads(l))
    except Exception: pass
rs = [r for r in rl if r['time'] >= START]
print('resources.log lines', len(rl), 'since start', len(rs))
print(collections.Counter((r['resource'], r['what']) for r in rs))
for r in rs:
    if r['resource'] == 'vm-pool' and r['what'] == 'acquired': print('VMACQ', r['time'], r['worker'], r['units'], r['why'][:80])
print('POOL', json.dumps({k: v for k, v in s['pool'].items()}))
for l in s['leases']: print('LEASE', l['resource'], l['units'], l['worker'], l['since'], l['why'][:80])
for q in s['queue']: print('QUEUE', q['resource'], q['units'], q['worker'], q['since'], q['why'][:80])
