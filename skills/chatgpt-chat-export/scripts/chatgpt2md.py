#!/usr/bin/env python3
"""
Convert a chatgpt.com backend-api conversation JSON into a readable Markdown
transcript (+ FILES.md attachment index).

ChatGPT's /backend-api/conversation/{id} payload is a *mapping tree*, not a
flat message list. This walks the canonical branch only (current_node ->
parents -> root), keeps just the visible turns, strips ChatGPT's private-use-
area citation runes, and links attachments to local files.

Usage:
    python3 chatgpt2md.py <conversation.json> [--files-dir DIR] [--out PATH]
                          [--files-md PATH | --no-files-md] [--user-name NAME]

Stdlib only (Python 3.9+).
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

#  ...  spans wrap citation/navlist markers; stray PUA chars remain
# around product carousels. Strip spans first, then any leftover PUA char.
PUA_SPAN = re.compile('[^]*')
PUA_ANY = re.compile('[-]')
# Leftover ChatGPT UI blobs that survive PUA stripping.
UI_BLOB = re.compile(r'^(?:products|navlist|videos?)\{.*\}\s*$', re.MULTILINE)
MEMCITE = re.compile(r'\s*\bmemcite\b')


def clean(text):
    text = PUA_SPAN.sub('', text)
    text = PUA_ANY.sub('', text)
    text = UI_BLOB.sub('', text)
    text = MEMCITE.sub('', text)
    return text.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('json_path', type=Path)
    ap.add_argument('--files-dir', type=Path, default=None,
                    help='local attachments dir (default: <json-dir>/files)')
    ap.add_argument('--out', type=Path, default=None,
                    help='output .md (default: <json-dir>/conversation.md)')
    ap.add_argument('--files-md', type=Path, default=None,
                    help='attachment index (default: <json-dir>/FILES.md)')
    ap.add_argument('--no-files-md', action='store_true')
    ap.add_argument('--user-name', default='User', help='label for user turns')
    args = ap.parse_args()

    base = args.json_path.resolve().parent
    files_dir = args.files_dir or base / 'files'
    out_path = args.out or base / 'conversation.md'

    conv = json.loads(args.json_path.read_text())
    mapping = conv['mapping']
    conv_id = conv.get('conversation_id') or conv.get('id') or ''

    # file id -> original filename, and per-message attachment metadata
    fnames, fmeta = {}, {}
    for node in mapping.values():
        m = node.get('message')
        if not m:
            continue
        for a in ((m.get('metadata') or {}).get('attachments') or []):
            fnames[a['id']] = a.get('name')
            fmeta[a['id']] = a

    rel_files = files_dir.name  # link prefix relative to the .md

    def render_part(p):
        if isinstance(p, str):
            return clean(p)
        if isinstance(p, dict):
            ct = p.get('content_type')
            if ct == 'image_asset_pointer':
                ap_ = p.get('asset_pointer', '')
                fid = ap_.split('://', 1)[-1]
                name = fnames.get(fid)
                if name and (files_dir / name).exists():
                    return f'![{name}]({rel_files}/{name})'
                return f'*[image: {name or fid}]*'
            if ct == 'audio_transcription':
                return clean(p.get('text', ''))
        return ''

    # canonical branch
    chain, cur = [], conv.get('current_node')
    while cur:
        chain.append(cur)
        cur = mapping.get(cur, {}).get('parent')
    chain.reverse()

    msgs = []
    for nid in chain:
        m = mapping[nid].get('message')
        if not m:
            continue
        role = (m.get('author') or {}).get('role')
        md = m.get('metadata') or {}
        if md.get('is_visually_hidden_from_conversation'):
            continue
        c = m.get('content') or {}
        ct = c.get('content_type')
        if role == 'user' and ct in ('text', 'multimodal_text'):
            txt = '\n'.join(filter(None, (render_part(p) for p in c.get('parts') or [])))
            # non-image attachments (PDFs etc.) referenced on this message
            for a in (md.get('attachments') or []):
                name = a.get('name', '')
                if name and f'({rel_files}/{name})' not in txt \
                        and not (a.get('mime_type', '') or '').startswith('image/'):
                    mark = f'📎 [{name}]({rel_files}/{name})' if (files_dir / name).exists() \
                        else f'📎 *{name} (not downloaded)*'
                    txt += '\n' + mark
            if txt.strip():
                msgs.append(('user', txt.strip()))
        elif role == 'assistant' and ct == 'text' and m.get('recipient', 'all') == 'all':
            txt = '\n'.join(filter(None, (render_part(p) for p in c.get('parts') or [])))
            if txt.strip():
                msgs.append(('assistant', txt.strip()))

    merged = []
    for role, txt in msgs:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + '\n\n' + txt)
        else:
            merged.append((role, txt))

    def day(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d') if ts else '?'

    out = [f"# {conv.get('title', 'ChatGPT conversation')}", '']
    if conv_id:
        out.append(f'> ChatGPT conversation — https://chatgpt.com/c/{conv_id}')
    out += [
        f"> Created: {day(conv.get('create_time'))} · Last message: {day(conv.get('update_time'))}"
        f" · Exported: {datetime.now().strftime('%Y-%m-%d')}",
        '', '---', '',
    ]
    for role, txt in merged:
        out += [f'## 👤 {args.user_name}' if role == 'user' else '## 🤖 ChatGPT',
                '', txt, '', '---', '']
    out_path.write_text('\n'.join(out))
    print(f'{len(merged)} messages -> {out_path}')

    if not args.no_files_md and fmeta:
        fm = args.files_md or base / 'FILES.md'
        lines = [f"# Files — {conv.get('title', conv_id)}", '',
                 '| file id | original name | type | size | local |',
                 '|---|---|---|---:|---|']
        for fid, a in fmeta.items():
            name = a.get('name', '')
            local = f'[{rel_files}/{name}]({rel_files}/{name})' if (files_dir / name).exists() \
                else 'missing'
            lines.append(f"| {fid} | {name} | {a.get('mime_type', '')} | {a.get('size', '')} | {local} |")
        fm.write_text('\n'.join(lines) + '\n')
        print(f'{len(fmeta)} attachments -> {fm}')


if __name__ == '__main__':
    sys.exit(main())
