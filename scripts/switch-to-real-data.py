#!/usr/bin/env python3
from pathlib import Path
import re

p = Path('index.html')
s = p.read_text(encoding='utf-8')

external = '<script src="./data/candidates.js"></script>\n<script>'
if '<script src="./data/candidates.js"></script>' not in s:
    s = s.replace('<script>\nconst DATA = {', external + '\nconst DATA = {', 1)

# Remove the old synthetic 30k expansion completely if it is ever reintroduced.
start = s.find('const TARGET_PER_CATEGORY = 30000;')
end_marker = 'const KEYS = Object.keys(DATA);'
if start != -1:
    end = s.find(end_marker, start)
    if end == -1:
        raise RuntimeError('Could not find end of old expansion block')
    s = s[:start] + s[end:]

# Load the external data only when every category has a substantial strict pool.
new_load_block = """const MIN_CANDIDATES_PER_CATEGORY = 1000;
const CANDIDATE_DATA_READY = !!window.CANDIDATE_DATA && Object.keys(DATA).every(key =>
  Array.isArray(window.CANDIDATE_DATA[key]) && window.CANDIDATE_DATA[key].length >= MIN_CANDIDATES_PER_CATEGORY
);
if (!CANDIDATE_DATA_READY) {
  document.body.innerHTML = '<div style="padding:24px;font-family:sans-serif;color:white;background:#11140d;min-height:100vh">候補データの読み込みに失敗しました。ページを再読み込みしてください。</div>';
  throw new Error('strict-category candidate data failed to load');
}
for (const key of Object.keys(DATA)) DATA[key] = window.CANDIDATE_DATA[key];

"""

current_load_re = re.compile(
    r'const CANDIDATE_DATA_READY = .*?'
    r'for \(const key of Object\.keys\(DATA\)\) DATA\[key\] = window\.CANDIDATE_DATA\[key\];\n\n',
    re.S,
)
old_load_re = re.compile(
    r'if \(window\.CANDIDATE_DATA\) \{.*?\}\n\n',
    re.S,
)

if current_load_re.search(s):
    s = current_load_re.sub(new_load_block, s, count=1)
elif old_load_re.search(s):
    s = old_load_re.sub(new_load_block, s, count=1)
elif new_load_block.strip() not in s:
    s = s.replace(end_marker, new_load_block + end_marker, 1)

# Fresh history namespaces because the candidate pools changed meaning.
s = re.sub(
    r"const STORAGE = 'neta-generator-[^']+';",
    "const STORAGE = 'neta-generator-v6-strict-categories';",
    s,
    count=1,
)
s = re.sub(
    r"const SENTENCE_STORAGE = 'sentence-generator-[^']+';",
    "const SENTENCE_STORAGE = 'sentence-generator-v5-strict-categories';",
    s,
    count=1,
)

# Sentence generation must use the same category-correct action pool.
s = re.sub(
    r'const SENTENCE_ACTIONS = \[.*?\];\n',
    'const SENTENCE_ACTIONS = DATA["行動"];\n',
    s,
    count=1,
    flags=re.S,
)
s = s.replace('"行動":SENTENCE_ACTIONS', '"行動":DATA["行動"]')

# Update the explanatory note: semantic fit now matters more than a fixed count.
s = s.replace(
    '各カテゴリ30,000件の実候補です。一度出た言葉は、',
    '各カテゴリは項目に合う実候補だけを使用しています。一度出た言葉は、'
)
s = s.replace(
    '各カテゴリ30,000候補です。一度出た言葉は、',
    '各カテゴリは項目に合う実候補だけを使用しています。一度出た言葉は、'
)

for forbidden in (
    'function expandCategory(', 'const ACTOR_A=', 'const PLACE_A=',
    'const PROP_A=', 'const ACTION_A=', 'const STATE_A=', 'const RESULT_A=',
    'const SENTENCE_ACTIONS = ['
):
    if forbidden in s:
        raise RuntimeError(f'Rejected old code remains: {forbidden}')

if new_load_block.strip() not in s:
    raise RuntimeError('Strict category load guard was not installed')
if '"行動":DATA["行動"]' not in s:
    raise RuntimeError('Sentence generator is not using the category action pool')

p.write_text(s, encoding='utf-8')
print('index.html switched to strict category-matched candidate pools')
