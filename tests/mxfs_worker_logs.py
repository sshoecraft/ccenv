import re, os, time, collections, glob
W = '/src/mxfs/.ccdirector/workers/'
for n in range(1, 5):
    p = W + 'worker-%d.log' % n
    L = open(p, errors='replace').read().split('\n')
    st = max(i for i, l in enumerate(L) if 'starting =====' in l)
    S = L[st:]
    c = collections.Counter()
    for l in S:
        if l.startswith('→ '): c['tool'] += 1
        for k in ('mcp__ccdirector__item_done', 'mcp__ccdirector__run', 'mcp__ccdirector__acquire', 'mcp__ccdirector__release', 'item park', 'mcp__ask_'):
            if l.startswith('→ ') and k in l: c[k] += 1
        if 'sent no response or progress' in l: c['MCP_TIMEOUT_ABORT'] += 1
    print('== worker-%d' % n, 'mtime', time.strftime('%F %T %z', time.localtime(os.path.getmtime(p))), 'start line', st + 1, 'lines', len(S), dict(c))
    ne = [l for l in S if l.strip()][-8:]
    for l in ne: print('   ', l[:200])
