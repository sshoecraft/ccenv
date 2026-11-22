import json, re, sys
p = '/home/steve/.claude/projects/-src-mxfs/886734ed-7d0a-4e08-8242-7a03dedc09d0.jsonl'
uses = {}; order = []
nwait = nplan = 0; res = []; prog = []; par = []; idle = []; n = 0; bad = 0
for l in open(p):
    try: o = json.loads(l)
    except Exception: bad += 1; continue
    n += 1
    m = o.get('message') or {}
    c = m.get('content')
    if not isinstance(c, list): continue
    for b in c:
        if b.get('type') == 'tool_use':
            cmd = (b.get('input') or {}).get('command', '')
            uses[b['id']] = (o.get('timestamp'), cmd, b.get('name'))
        if b.get('type') == 'tool_result':
            u = uses.get(b.get('tool_use_id'))
            if not u: continue
            t = b.get('content')
            if isinstance(t, list): t = '\n'.join(x.get('text', '') for x in t if isinstance(x, dict))
            t = t or ''
            if re.search(r'ccdirector\s+wait', u[1]): 
                nwait += 1
                if 'PROGRESS' in t: prog.append((o.get('timestamp'), [x for x in t.split('\n') if 'PROGRESS' in x][-1][:300]))
                for x in t.split('\n'):
                    if 'WORKERS IDLE' in x: idle.append((o.get('timestamp'), x[:300]))
            if re.search(r'plan\s+submit', u[1]):
                nplan += 1
                res.append((u[0], t.split('\n')[0][:200]))
                for x in t.split('\n'):
                    if x.startswith('PARALLEL:'): par.append((u[0], x[:300]))
print('records', n, 'malformed', bad, 'tool_use', len(uses))
print('wait calls (with result)', nwait, 'plan submit calls (with result)', nplan)
print('plan submit results', res[-5:])
print('PARALLEL last3', par[-3:])
print('WORKERS IDLE in wait results', len(idle), idle[-3:])
print('PROGRESS lines in wait results', len(prog)); print('last', prog[-1:])
print('wait calls issued', sum(1 for u in uses.values() if re.search(r'ccdirector\s+wait', u[1])), 'plan submit issued', sum(1 for u in uses.values() if re.search(r'plan\s+submit', u[1])))
print('first/last use ts', min(u[0] for u in uses.values() if u[0]), max(u[0] for u in uses.values() if u[0]))
