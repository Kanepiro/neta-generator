#!/usr/bin/env python3
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path

from fugashi import Tagger
from unidic_lite import DICDIR

MIN_PER_CATEGORY = 1000
JP_RE = re.compile(r"[ぁ-んァ-ヶ一-龯々〆ヵヶ]")
SPACE_RE = re.compile(r"[\s_]")
BAD_RE = re.compile(r"[<>\\/{}\[\]|]")
KANJI = r"[一-龯々〆ヵヶ]"
PHRASE_PARTICLE_RE = re.compile(
    KANJI + r".*(?:の|を|へ|に|で|と|が|から|まで|より).*" + KANJI
)
GENERATED_PHRASE_RE = re.compile(
    r"(?:の近く|の前|の裏|のそば|の中|の入口|の向こう側|を行う|を動かす|の状態|のまま|の最中|が起きる|で終わる|が残る|ことになる|ところで終わる|になる|だけが残る)$"
)

TAGGER = Tagger(f"-d {DICDIR}")


@lru_cache(maxsize=None)
def contains_proper_noun(value):
    try:
        tokens = list(TAGGER(value))
    except Exception:
        return True
    if not tokens:
        return True
    for token in tokens:
        if getattr(token, "is_unk", False):
            return True
        if "固有名詞" in str(token.feature):
            return True
    return False


@lru_cache(maxsize=None)
def clean_term(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s or len(s) > 24:
        return None
    if SPACE_RE.search(s) or BAD_RE.search(s) or not JP_RE.search(s):
        return None
    if s.isdigit():
        return None
    if GENERATED_PHRASE_RE.search(s):
        return None
    if PHRASE_PARTICLE_RE.search(s):
        return None
    if contains_proper_noun(s):
        return None
    return s


def uniq(values):
    out = []
    seen = set()
    for value in values:
        value = clean_term(value)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def stable(values, salt):
    values = uniq(values)
    return sorted(
        values,
        key=lambda x: hashlib.sha256((salt + "\0" + x).encode("utf-8")).digest()
    )


def without(values, *excluded_pools):
    excluded = set()
    for pool in excluded_pools:
        excluded.update(pool)
    return [value for value in uniq(values) if value not in excluded]


def root_synsets(conn, lemmas):
    roots = set()
    for lemma in lemmas:
        rows = conn.execute(
            """
            SELECT DISTINCT s.synset
            FROM word w
            JOIN sense s ON s.wordid = w.wordid
            WHERE w.lang='eng' AND w.lemma=?
            """,
            (lemma,),
        )
        roots.update(row[0] for row in rows)
    return roots


def descendants(conn, roots):
    children = defaultdict(list)
    for a, b, link in conn.execute(
        "SELECT synset1, synset2, link FROM synlink WHERE link IN ('hypo','hasi')"
    ):
        children[a].append(b)
    seen = set(roots)
    q = deque(roots)
    while q:
        cur = q.popleft()
        for nxt in children.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return seen


def words_for_synsets(conn, synsets, pos=None):
    if not synsets:
        return []
    result = []
    batch = 700
    synsets = list(synsets)
    for i in range(0, len(synsets), batch):
        chunk = synsets[i:i + batch]
        qs = ",".join("?" for _ in chunk)
        sql = f"""
            SELECT DISTINCT w.lemma, w.pos
            FROM sense s
            JOIN word w ON w.wordid=s.wordid
            WHERE w.lang='jpn' AND s.synset IN ({qs})
        """
        for lemma, word_pos in conn.execute(sql, chunk):
            if pos is None or word_pos in pos:
                result.append(lemma)
    return uniq(result)


def all_words_by_pos(conn):
    by_pos = defaultdict(list)
    for lemma, pos in conn.execute(
        "SELECT DISTINCT lemma, pos FROM word WHERE lang='jpn'"
    ):
        lemma = clean_term(lemma)
        if lemma:
            by_pos[pos].append(lemma)
    return {k: uniq(v) for k, v in by_pos.items()}


def merge_unique(*pools):
    out, seen = [], set()
    for pool in pools:
        for value in pool:
            value = clean_term(value)
            if value and value not in seen:
                seen.add(value)
                out.append(value)
    return out


def build(db_path):
    conn = sqlite3.connect(str(db_path))
    by_pos = all_words_by_pos(conn)
    nouns = by_pos.get('n', [])
    verbs = by_pos.get('v', [])
    adjectives = merge_unique(by_pos.get('a', []), by_pos.get('s', []))
    adverbs = by_pos.get('r', [])
    every_word = merge_unique(nouns, verbs, adjectives, adverbs)

    person = descendants(
        conn,
        root_synsets(conn, ['person', 'organism', 'animal'])
    )
    location = descendants(
        conn,
        root_synsets(conn, [
            'location', 'region', 'geographical_area', 'structure', 'facility',
            'building', 'room', 'area', 'site', 'body_of_water', 'land',
            'road', 'route', 'path'
        ])
    )
    artifact = descendants(
        conn,
        root_synsets(conn, [
            'artifact', 'instrumentality', 'device', 'container', 'vehicle'
        ])
    )
    natural = descendants(
        conn,
        root_synsets(conn, [
            'natural_object', 'plant', 'food', 'substance'
        ])
    )
    event = descendants(
        conn,
        root_synsets(conn, [
            'event', 'change', 'happening', 'process'
        ])
    )

    person_words = words_for_synsets(conn, person, {'n'})
    location_words = words_for_synsets(conn, location, {'n'})
    artifact_words = words_for_synsets(conn, artifact, {'n'})
    natural_words = words_for_synsets(conn, natural, {'n'})
    event_nouns = words_for_synsets(conn, event, {'n'})

    actors = stable(person_words, 'actor-strict')
    places = stable(location_words, 'place-strict')
    props = stable(
        without(
            merge_unique(artifact_words, natural_words),
            person_words,
            location_words
        ),
        'prop-strict'
    )
    actions = stable(verbs, 'action-strict')
    states = stable(
        merge_unique(adjectives, adverbs),
        'state-strict'
    )
    results = stable(event_nouns, 'result-strict')

    data = {
        '主役': actors,
        '場所': places,
        '小道具': props,
        '行動': actions,
        '状態': states,
        '結果': results,
    }

    for key, values in data.items():
        if len(values) < MIN_PER_CATEGORY or len(values) != len(set(values)):
            raise RuntimeError(
                f'{key}: invalid candidate pool {len(values)} / '
                f'unique {len(set(values))}'
            )
        bad = [v for v in values if clean_term(v) != v]
        if bad:
            raise RuntimeError(
                f'{key}: invalid lexical candidates found: {bad[:10]}'
            )
        proper = [v for v in values if contains_proper_noun(v)]
        if proper:
            raise RuntimeError(f'{key}: proper nouns found: {proper[:10]}')

    stats = {
        'minimum_per_category': MIN_PER_CATEGORY,
        'single_term_only': True,
        'proper_nouns_allowed': False,
        'proper_noun_filter': 'UniDic via fugashi; unknown tokens rejected',
        'strict_category_pools': True,
        'cross_category_fallback': False,
        'sources': ['Japanese WordNet v1.1'],
        'method': (
            'Only category-compatible WordNet/POS pools; '
            'no generic fallback to reach a fixed count'
        ),
        'category_definitions': {
            '主役': 'person / organism / animal nouns',
            '場所': (
                'location / structure / facility / room / area / '
                'road / route / path nouns'
            ),
            '小道具': (
                'artifact / instrument / device / container / vehicle / '
                'natural object / plant / food / substance nouns, '
                'excluding actors and places'
            ),
            '行動': 'verbs',
            '状態': 'adjectives / adverbs',
            '結果': 'event / change / happening / process nouns',
        },
        'counts': {k: len(v) for k, v in data.items()},
        'source_pools': {
            'japanese_words_total_after_filters': len(every_word),
            'nouns': len(nouns),
            'verbs': len(verbs),
            'adjectives': len(adjectives),
            'adverbs': len(adverbs),
            'person_or_living_nouns': len(person_words),
            'location_like_nouns': len(location_words),
            'artifact_nouns': len(artifact_words),
            'natural_item_nouns': len(natural_words),
            'event_nouns': len(event_nouns),
        },
    }
    conn.close()
    return data, stats


def main():
    if len(sys.argv) == 4:
        db_path = Path(sys.argv[1])
        out_path = Path(sys.argv[3])
    elif len(sys.argv) == 3:
        db_path = Path(sys.argv[1])
        out_path = Path(sys.argv[2])
    else:
        print(
            'usage: build-single-word-candidates.py '
            'WNJPN_DB [OLD_GEONAMES_IGNORED] OUTPUT_JS',
            file=sys.stderr
        )
        raise SystemExit(2)

    data, stats = build(db_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    runtime_fix = r"""
;try{
  const version='strict-category-v1';
  const versionKey='neta-generator-data-version';
  if(localStorage.getItem(versionKey)!==version){
    localStorage.removeItem('neta-generator-v5-common-single-no-proper');
    localStorage.removeItem('sentence-generator-v4-full-pools');
    localStorage.setItem(versionKey,version);
  }
}catch(e){}
"""

    out_path.write_text(
        '/* Generated from Japanese WordNet v1.1. '
        'Strict category pools; no cross-category fallback. */\n'
        + 'window.CANDIDATE_DATA='
        + json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        + ';\n'
        + runtime_fix.lstrip(),
        encoding='utf-8',
    )
    (out_path.parent / 'candidate-stats.json').write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8'
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
