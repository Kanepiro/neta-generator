#!/usr/bin/env python3
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

TARGET = 30000
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


def clean_term(value):
    """Keep one display term only. Reject spaces and phrase-like grammatical constructions."""
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
    return sorted(values, key=lambda x: hashlib.sha256((salt + "\0" + x).encode("utf-8")).digest())


def take(values, salt, target=TARGET):
    out = stable(values, salt)[:target]
    if len(out) < target:
        raise RuntimeError(f"{salt}: only {len(out):,} single-word candidates; need {target:,}")
    return out


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
    for lemma, pos in conn.execute("SELECT DISTINCT lemma, pos FROM word WHERE lang='jpn'"):
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


def geonames_terms(path):
    """Read Japanese place-name terms from a GeoNames JP dump. One place name = one candidate."""
    terms = []
    with Path(path).open('r', encoding='utf-8') as f:
        for line in f:
            cols = line.rstrip('\n').split('\t')
            if len(cols) < 8:
                continue
            feature_class = cols[6]
            # Geographic features only; all classes below describe actual places/features.
            if feature_class not in {'A', 'H', 'L', 'P', 'R', 'S', 'T', 'V'}:
                continue
            terms.append(cols[1])
            if len(cols) > 3 and cols[3]:
                terms.extend(cols[3].split(','))
    return uniq(terms)


def build(db_path, geonames_path):
    conn = sqlite3.connect(str(db_path))
    by_pos = all_words_by_pos(conn)
    nouns = by_pos.get('n', [])
    verbs = by_pos.get('v', [])
    adjectives = merge_unique(by_pos.get('a', []), by_pos.get('s', []))
    adverbs = by_pos.get('r', [])
    every_word = merge_unique(nouns, verbs, adjectives, adverbs)

    physical = descendants(conn, root_synsets(conn, ['physical_entity']))
    person = descendants(conn, root_synsets(conn, ['person', 'organism', 'animal']))
    artifact = descendants(conn, root_synsets(conn, ['artifact', 'instrumentality', 'device', 'container', 'vehicle']))
    natural = descendants(conn, root_synsets(conn, ['natural_object', 'plant', 'food', 'substance']))
    action = descendants(conn, root_synsets(conn, ['act', 'action', 'activity', 'process']))
    event = descendants(conn, root_synsets(conn, ['event', 'change', 'happening', 'process']))
    state = descendants(conn, root_synsets(conn, ['state', 'condition', 'attribute', 'feeling', 'emotion']))

    physical_words = words_for_synsets(conn, physical, {'n'})
    person_words = words_for_synsets(conn, person, {'n'})
    artifact_words = words_for_synsets(conn, artifact, {'n'})
    natural_words = words_for_synsets(conn, natural, {'n'})
    action_nouns = words_for_synsets(conn, action, {'n'})
    event_nouns = words_for_synsets(conn, event, {'n'})
    state_nouns = words_for_synsets(conn, state, {'n'})
    place_names = geonames_terms(geonames_path)

    # Every category is made only from existing dictionary/geographic terms.
    # No "Xの近く", "Xを動かす", "Xで終わる" or other generated phrases.
    actors = take(merge_unique(person_words, physical_words, nouns), 'actor')
    places = take(place_names, 'place')
    props = take(merge_unique(artifact_words, natural_words, physical_words, nouns), 'prop')
    actions = take(merge_unique(verbs, action_nouns, event_nouns, nouns), 'action')
    states = take(merge_unique(adjectives, adverbs, state_nouns, event_nouns, nouns), 'state')
    results = take(merge_unique(event_nouns, state_nouns, action_nouns, verbs, nouns), 'result')

    data = {
        '主役': actors,
        '場所': places,
        '小道具': props,
        '行動': actions,
        '状態': states,
        '結果': results,
    }

    for key, values in data.items():
        if len(values) != TARGET or len(set(values)) != TARGET:
            raise RuntimeError(f'{key}: invalid candidate count {len(values)} / unique {len(set(values))}')
        bad = [v for v in values if clean_term(v) != v]
        if bad:
            raise RuntimeError(f'{key}: non-single-term candidates found: {bad[:10]}')

    stats = {
        'target_per_category': TARGET,
        'single_term_only': True,
        'sources': ['Japanese WordNet v1.1', 'GeoNames Japan'],
        'method': '30,000 existing single terms per category; no generated phrases or modifier expansion',
        'counts': {k: len(v) for k, v in data.items()},
        'source_pools': {
            'japanese_words_total_after_single_term_filter': len(every_word),
            'nouns': len(nouns),
            'verbs': len(verbs),
            'adjectives': len(adjectives),
            'adverbs': len(adverbs),
            'physical_nouns': len(physical_words),
            'person_or_living_nouns': len(person_words),
            'action_nouns': len(action_nouns),
            'event_nouns': len(event_nouns),
            'state_nouns': len(state_nouns),
            'geonames_japanese_terms': len(place_names),
        },
    }
    conn.close()
    return data, stats


def main():
    if len(sys.argv) != 4:
        print('usage: build-single-word-candidates.py WNJPN_DB GEONAMES_JP_TSV OUTPUT_JS', file=sys.stderr)
        raise SystemExit(2)
    db_path = Path(sys.argv[1])
    geonames_path = Path(sys.argv[2])
    out_path = Path(sys.argv[3])
    data, stats = build(db_path, geonames_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        '/* Generated from Japanese WordNet v1.1 and GeoNames Japan. See license files in data/. */\n'
        + 'window.CANDIDATE_DATA='
        + json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        + ';\n',
        encoding='utf-8',
    )
    (out_path.parent / 'candidate-stats.json').write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
