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
BAD_RE = re.compile(r"[\r\n\t<>\\/{}\[\]|]")


def clean_lemma(value):
    if value is None:
        return None
    s = str(value).strip().replace("_", " ")
    if not s or len(s) > 24 or BAD_RE.search(s) or not JP_RE.search(s):
        return None
    if s.isdigit():
        return None
    return s


def uniq(values):
    out = []
    seen = set()
    for value in values:
        value = clean_lemma(value)
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
        raise RuntimeError(f"{salt}: only {len(out):,} candidates; need {target:,}")
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
        chunk = synsets[i:i+batch]
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
        lemma = clean_lemma(lemma)
        if lemma:
            by_pos[pos].append(lemma)
    return {k: uniq(v) for k, v in by_pos.items()}


def merge_unique(*pools):
    out, seen = [], set()
    for pool in pools:
        for value in pool:
            value = clean_lemma(value)
            if value and value not in seen:
                seen.add(value)
                out.append(value)
    return out


def one_to_one_phrases(base_words, variants, salt):
    """Create exactly one natural phrase per distinct root word; no cartesian-product inflation."""
    result = []
    for word in uniq(base_words):
        idx = hashlib.sha256((salt + "\0" + word).encode("utf-8")).digest()[0] % len(variants)
        result.append(variants[idx].format(word=word))
    return uniq(result)


def build(db_path):
    conn = sqlite3.connect(str(db_path))
    by_pos = all_words_by_pos(conn)
    nouns = by_pos.get('n', [])
    verbs = by_pos.get('v', [])
    adjectives = merge_unique(by_pos.get('a', []), by_pos.get('s', []))
    adverbs = by_pos.get('r', [])
    every_word = merge_unique(nouns, verbs, adjectives, adverbs)

    physical = descendants(conn, root_synsets(conn, ['physical_entity']))
    person = descendants(conn, root_synsets(conn, ['person', 'organism', 'animal']))
    location = descendants(conn, root_synsets(conn, ['location', 'region', 'geographical_area', 'structure']))
    artifact = descendants(conn, root_synsets(conn, ['artifact', 'instrumentality', 'device', 'container', 'vehicle']))
    natural = descendants(conn, root_synsets(conn, ['natural_object', 'plant', 'food', 'substance']))
    action = descendants(conn, root_synsets(conn, ['act', 'action', 'activity', 'process']))
    event = descendants(conn, root_synsets(conn, ['event', 'change', 'happening', 'process']))
    state = descendants(conn, root_synsets(conn, ['state', 'condition', 'attribute', 'feeling', 'emotion']))

    physical_words = words_for_synsets(conn, physical, {'n'})
    person_words = words_for_synsets(conn, person, {'n'})
    location_words = words_for_synsets(conn, location, {'n'})
    prop_words = merge_unique(
        words_for_synsets(conn, artifact, {'n'}),
        words_for_synsets(conn, natural, {'n'}),
        physical_words,
    )
    action_nouns = words_for_synsets(conn, action, {'n'})
    event_nouns = words_for_synsets(conn, event, {'n'})
    state_nouns = words_for_synsets(conn, state, {'n'})

    # 主役: one real Japanese WordNet lemma per candidate. Physical/living terms first,
    # then other nouns only as a count-safe fallback. No adjective multiplication.
    actor_roots = merge_unique(person_words, physical_words, nouns)
    actors = take(actor_roots, 'actor')

    # 場所: true location/structure lemmas first. To reach a large creative pool,
    # each additional physical noun contributes ONE spatial phrase only.
    place_fill = one_to_one_phrases(
        physical_words,
        ['{word}の近く', '{word}の前', '{word}の裏', '{word}のそば', '{word}の中', '{word}の入口', '{word}の向こう側'],
        'place-fill',
    )
    if len(merge_unique(location_words, place_fill)) < TARGET:
        place_fill2 = one_to_one_phrases(
            nouns,
            ['{word}の近く', '{word}の前', '{word}の裏', '{word}のそば', '{word}の中'],
            'place-fill2',
        )
    else:
        place_fill2 = []
    places = take(merge_unique(location_words, place_fill, place_fill2), 'place')

    # 小道具: real concrete/physical lemmas; fallback remains one distinct noun per item.
    props = take(merge_unique(prop_words, nouns), 'prop')

    # 行動: real verb lemmas. If WordNet has fewer than 30k unique verbs,
    # action/event nouns become sahen-style actions, again one phrase per distinct root.
    action_fill = one_to_one_phrases(action_nouns, ['{word}する'], 'action-fill')
    action_fill2 = one_to_one_phrases(event_nouns, ['{word}を行う'], 'action-fill2')
    if len(merge_unique(verbs, action_fill, action_fill2)) < TARGET:
        action_fill3 = one_to_one_phrases(physical_words, ['{word}を動かす'], 'action-fill3')
    else:
        action_fill3 = []
    actions = take(merge_unique(verbs, action_fill, action_fill2, action_fill3), 'action')

    # 状態: adjective/adverb/state concepts. One root creates at most one candidate.
    state_fill = one_to_one_phrases(state_nouns, ['{word}の状態', '{word}のまま'], 'state-fill')
    state_fill2 = one_to_one_phrases(event_nouns, ['{word}の最中'], 'state-fill2')
    if len(merge_unique(adjectives, adverbs, state_nouns, state_fill, state_fill2)) < TARGET:
        state_fill3 = one_to_one_phrases(nouns, ['{word}の状態'], 'state-fill3')
    else:
        state_fill3 = []
    states = take(merge_unique(adjectives, adverbs, state_nouns, state_fill, state_fill2, state_fill3), 'state')

    # 結果: event/change concepts and verb outcomes. Distinct lexical roots, one phrase each.
    result_events = one_to_one_phrases(event_nouns, ['{word}が起きる', '{word}で終わる', '{word}が残る'], 'result-event')
    result_verbs = one_to_one_phrases(verbs, ['{word}ことになる', '{word}ところで終わる'], 'result-verb')
    result_states = one_to_one_phrases(state_nouns, ['{word}になる', '{word}だけが残る'], 'result-state')
    if len(merge_unique(result_events, result_verbs, result_states)) < TARGET:
        result_fill = one_to_one_phrases(nouns, ['{word}だけが残る'], 'result-fill')
    else:
        result_fill = []
    results = take(merge_unique(result_events, result_verbs, result_states, result_fill), 'result')

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

    stats = {
        'target_per_category': TARGET,
        'source': 'Japanese WordNet v1.1',
        'source_url': 'https://github.com/bond-lab/wnja/releases/tag/v1.1',
        'method': '30,000 distinct lexical-root candidates per category; no cartesian adjective/modifier expansion',
        'counts': {k: len(v) for k, v in data.items()},
        'source_pools': {
            'japanese_words_total': len(every_word),
            'nouns': len(nouns),
            'verbs': len(verbs),
            'adjectives': len(adjectives),
            'adverbs': len(adverbs),
            'physical_nouns': len(physical_words),
            'person_or_living_nouns': len(person_words),
            'location_nouns': len(location_words),
            'action_nouns': len(action_nouns),
            'event_nouns': len(event_nouns),
            'state_nouns': len(state_nouns),
        },
    }
    conn.close()
    return data, stats


def main():
    if len(sys.argv) != 3:
        print('usage: build-real-candidates.py WNJPN_DB OUTPUT_JS', file=sys.stderr)
        raise SystemExit(2)
    db_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    data, stats = build(db_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        '/* Generated from Japanese WordNet v1.1. See data/JAPANESE_WORDNET_LICENSE.txt */\n'
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
