#!/usr/bin/env python3
"""Extract user messages, long assistant text, edits, tail and summaries from a session jsonl."""
import json, sys, collections, re

path = sys.argv[1]
recs = []
for line in open(path):
    try:
        recs.append(json.loads(line))
    except Exception:
        pass

def blocks(d):
    c = d.get('message', {}).get('content')
    if isinstance(c, str):
        return [{'type': 'text', 'text': c}]
    return c or []

def clip(s, n):
    return s if len(s) <= n else s[:n] + f'...[truncated, {len(s)} chars total]'

def strip_reminders(t):
    return re.sub(r'<system-reminder>.*?</system-reminder>', '', t, flags=re.S).strip()

print('=== 1. USER MESSAGES ===')
nu = 0
for d in recs:
    if d.get('type') != 'user' or d.get('isCompactSummary'):
        continue
    texts = [b['text'] for b in blocks(d) if b.get('type') == 'text']
    t = strip_reminders('\n'.join(texts))
    if not t:
        continue
    if t.startswith('<task-notification') or t.startswith('<command-') or t.startswith('<local-command'):
        tag = '[AUTOMATED/notification] '
    elif t.lower().strip(' .!') in ('continue', 'go on', 'proceed'):
        tag = '[LOOP-CONTINUE] '
    else:
        tag = ''
    nu += 1
    print(f"--- {d.get('timestamp')} {tag}")
    print(clip(t, 1500))
print(f'(user message count: {nu})')

print('\n=== 2. ASSISTANT TEXT >200 chars ===')
na = 0
for d in recs:
    if d.get('type') != 'assistant':
        continue
    for b in blocks(d):
        if b.get('type') == 'text' and len(b['text']) > 200:
            na += 1
            print(f"--- {d.get('timestamp')}")
            print(clip(b['text'], 1200))
print(f'(count: {na})')

print('\n=== 3. FILES EDITED/WRITTEN ===')
cnt = collections.Counter()
for d in recs:
    if d.get('type') != 'assistant':
        continue
    for b in blocks(d):
        if b.get('type') == 'tool_use' and b.get('name') in ('Edit', 'Write', 'MultiEdit'):
            cnt[(b['name'], b['input'].get('file_path'))] += 1
for (n, f), k in sorted(cnt.items(), key=lambda x: x[0][1] or ''):
    print(f'{k:4d} {n:6s} {f}')

print('\n=== 4. LAST 15 TOOL_USE + LAST TEXT ===')
tus = []
lasttext = None
for d in recs:
    if d.get('type') != 'assistant':
        continue
    for b in blocks(d):
        if b.get('type') == 'tool_use':
            tus.append((d.get('timestamp'), b['name'], json.dumps(b['input'])))
        elif b.get('type') == 'text' and b['text'].strip():
            lasttext = (d.get('timestamp'), b['text'])
for ts, n, i in tus[-15:]:
    print(ts, n, clip(i, 300))
print('--- last assistant text (full)')
print(lasttext[0]); print(lasttext[1])

print('\n=== 5. COMPACT/SUMMARY RECORDS ===')
for d in recs:
    if d.get('isCompactSummary') or d.get('type') == 'summary':
        print('---', d.get('timestamp'))
        c = d.get('summary') or '\n'.join(b.get('text', '') for b in blocks(d))
        print(c)
