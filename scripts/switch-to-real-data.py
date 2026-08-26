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

# Replace the small built-in fallback pools with the generated real data when loaded.
load_block = '''if (window.CANDIDATE_DATA) {
  for (const key of Object.keys(DATA)) {
    const values = window.CANDIDATE_DATA[key];
    if (Array.isArray(values) && values.length === 30000) DATA[key] = values;
  }
}

'''
if load_block.strip() not in s:
    s = s.replace(end_marker, load_block + end_marker, 1)

s = s.replace(
    '各カテゴリ30,000候補です。一度出た言葉は、',
    '各カテゴリ30,000件の実候補です。一度出た言葉は、'
)

# Guard against reintroducing the rejected synthetic inflation code.
for forbidden in (
    'function expandCategory(', 'const ACTOR_A=', 'const PLACE_A=',
    'const PROP_A=', 'const ACTION_A=', 'const STATE_A=', 'const RESULT_A='
):
    if forbidden in s:
        raise RuntimeError(f'Old synthetic expansion remains: {forbidden}')

p.write_text(s, encoding='utf-8')
print('index.html switched to data/candidates.js')
