#!/usr/bin/env python3
"""Read the legacy Mail reservation resource; never acquire or release a lease."""
from __future__ import annotations

from datetime import datetime, timezone
import fnmatch
import json
import os
import sys
import unicodedata
import urllib.parse
import urllib.request


def check() -> None:
    project = os.environ['QUERY_PROJECT_KEY']
    uri = 'resource://file_reservations/' + urllib.parse.quote(project, safe='') + '?active_only=true&format=json'
    payload = {'jsonrpc': '2.0', 'id': 'reservation-conflicts', 'method': 'resources/read',
               'params': {'uri': uri}}
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    token = os.environ.get('QUERY_TOKEN', '')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(os.environ['QUERY_URL'], data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=3) as response:
        document = json.load(response)
    if document.get('error') is not None:
        raise ValueError('reservation resource rejected the check')
    result = document['result']
    if not isinstance(result, dict) or result.get('isError', False) is not False:
        raise ValueError('reservation resource reports an error')
    contents = result['contents']
    if not isinstance(contents, list) or len(contents) != 1:
        raise ValueError('invalid reservation resource contents')
    rows = json.loads(contents[0]['text'])
    if not isinstance(rows, list):
        raise ValueError('invalid reservation resource rows')
    targets = [unicodedata.normalize('NFC', os.environ[key]) for key in ('QUERY_REL_PATH', 'QUERY_ABS_PATH')]
    now = datetime.now(timezone.utc)
    owners = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('agent'), str)
                or not row['agent'] or not isinstance(row.get('path_pattern'), str)
                or not row['path_pattern'] or not isinstance(row.get('exclusive'), bool)):
            raise ValueError('invalid reservation row')
        expires = datetime.fromisoformat(row['expires_ts'].replace('Z', '+00:00'))
        if expires.tzinfo is None:
            raise ValueError('reservation expiry has no timezone')
        released = row.get('released_ts')
        if released is not None:
            if not isinstance(released, str) or datetime.fromisoformat(released.replace('Z', '+00:00')).tzinfo is None:
                raise ValueError('invalid reservation release timestamp')
        if released is not None or expires <= now:
            continue
        if row['agent'] == os.environ['QUERY_AGENT'] or not row['exclusive']:
            continue
        pattern = unicodedata.normalize('NFC', row['path_pattern'])
        if any(fnmatch.fnmatchcase(target, pattern) for target in targets):
            owners.add(row['agent'])
    if owners:
        print('Other exclusive reservation holder(s): ' + ', '.join(sorted(owners)))
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        check()
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        print(f'Reservation conflict check rejected: {exc}')
        raise SystemExit(2)
