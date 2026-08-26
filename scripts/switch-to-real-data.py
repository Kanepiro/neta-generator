#!/usr/bin/env python3
from pathlib import Path
import re

p = Path('index.html')
s = p.read_text(encoding='utf-8')

# Load the prebuilt 30k x 6 lexical dataset before the app logic.
external = '<script src="./data/candidates.js"></script>\n<script>'
if '<script src="./data/candidates.js"></script>' not in s:
    s = s.replace('<script>\nconst DATA = {', external + '\nconst DATA = {', 1)

# Remove the old cartesian modifier expansion completely.
start = s.find('const TARGET_PER_CATEGORY = 30000;')
end_marker = 'const KEYS = Object.keys(DATA);'
if start != -1:
    end = s.find(end_marker, start)
    if end == -1:
        raise RuntimeError('Could not find end of old expansion block')
    s = s[:start] + s[end:]

# Never silently fall back to the old 80-item pools. If the real dataset did not
# load correctly, stop with a visible reload message instead of pretending the
# generator still has 30,000 candidates.
old_load_block = '''if (window.CANDIDATE_DATA) {
  for (const key of Object.keys(DATA)) {
    const values = window.CANDIDATE_DATA[key];
    if (Array.isArray(values) && values.length === 30000) DATA[key] = values;
  }
}

'''
strict_load_block = '''const CANDIDATE_DATA_READY = !!window.CANDIDATE_DATA && Object.keys(DATA).every(key =>
  Array.isArray(window.CANDIDATE_DATA[key]) && window.CANDIDATE_DATA[key].length === 30000
);
if (!CANDIDATE_DATA_READY) {
  document.body.innerHTML = '<div style="padding:24px;font-family:sans-serif;color:white;background:#11140d;min-height:100vh">候補データの読み込みに失敗しました。ページを再読み込みしてください。</div>';
  throw new Error('30,000-candidate data failed to load');
}
for (const key of Object.keys(DATA)) DATA[key] = window.CANDIDATE_DATA[key];

'''
if old_load_block in s:
    s = s.replace(old_load_block, strict_load_block, 1)
elif strict_load_block.strip() not in s:
    s = s.replace(end_marker, strict_load_block + end_marker, 1)

# Candidate data changed substantially, so use fresh history namespaces.
# This prevents old test history from making a newly rebuilt pool look exhausted.
s = s.replace(
    "const STORAGE = 'neta-generator-v4-global-no-repeat';",
    "const STORAGE = 'neta-generator-v5-common-single-no-proper';"
)
s = s.replace(
    "const SENTENCE_STORAGE = 'sentence-generator-v3-no-repeat';",
    "const SENTENCE_STORAGE = 'sentence-generator-v4-full-pools';"
)

# The sentence generator used to keep a separate hard-coded list of only about
# 40 actions, which caused real exhaustion after a few dozen sentences. Use the
# same 30,000-item action pool as the main generator.
s = re.sub(
    r'const SENTENCE_ACTIONS = \[.*?\];\n',
    'const SENTENCE_ACTIONS = DATA["行動"];\n',
    s,
    count=1,
    flags=re.S,
)
s = s.replace('"行動":SENTENCE_ACTIONS', '"行動":DATA["行動"]')

s = s.replace(
    '各カテゴリ30,000候補です。一度出た言葉は、',
    '各カテゴリ30,000件の実候補です。一度出た言葉は、'
)

# Guard against reintroducing the rejected synthetic inflation code or the old
# tiny sentence action pool.
for forbidden in (
    'function expandCategory(', 'const ACTOR_A=', 'const PLACE_A=',
    'const PROP_A=', 'const ACTION_A=', 'const STATE_A=', 'const RESULT_A=',
    'const SENTENCE_ACTIONS = ['
):
    if forbidden in s:
        raise RuntimeError(f'Rejected old code remains: {forbidden}')

if strict_load_block.strip() not in s:
    raise RuntimeError('Strict 30,000-candidate load guard was not installed')
if '"行動":DATA["行動"]' not in s:
    raise RuntimeError('Sentence generator is not using the 30,000 action pool')

p.write_text(s, encoding='utf-8')
print('index.html switched to strict 30k data and full sentence pools')
