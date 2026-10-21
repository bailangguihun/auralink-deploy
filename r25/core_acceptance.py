"""Read-only PostgreSQL/HTTP checks for the fixed local R25 Core endpoint.

No login, task creation, provider invocation or database mutation occurs. The
caller supplies its hash-bound readonly psql adapter and holds the shared lease.
Live checks complement, and do not replace, the executable equivalence gate.
"""
from __future__ import annotations

import json
import re
import hashlib
import time
from datetime import datetime, timedelta
import urllib.error
import urllib.request
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from core_release_guard import Refused, must

IDLE_SQL = """BEGIN READ ONLY; SET LOCAL statement_timeout='8000ms';
SELECT (SELECT count(*) FROM creations WHERE status IN ('QUEUED','RUNNING'))
+(SELECT count(*) FROM catalog_painting_critic_tasks WHERE status IN ('QUEUED','RUNNING'))
+(SELECT count(*) FROM catalog_painting_guide_generation_attempts
 WHERE status IN ('DISPATCHING','POST_RESPONSE_UNCERTAIN')); ROLLBACK;"""

MANAGED_VMM_INFLIGHT_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout='8000ms';
WITH music_creations AS (
  SELECT c.id, c.status, c.error_code
  FROM creations c
  WHERE EXISTS (
    SELECT 1 FROM creation_steps s
    WHERE s.creation_id=c.id AND s.operation_code='PAINTING_TO_MUSIC'
  )
), music_steps AS (
  SELECT s.id, s.creation_id, s.status, s.provider_dispatch_state, s.error_code
  FROM creation_steps s JOIN music_creations c ON c.id=s.creation_id
  WHERE s.operation_code='PAINTING_TO_MUSIC'
), music_dispatches AS (
  SELECT d.dispatch_state, d.finished_at, d.resolution_code
  FROM creation_step_dispatch_attempts d JOIN music_steps s ON s.id=d.creation_step_id
)
SELECT jsonb_build_object(
  'music_creations_by_status', COALESCE((SELECT jsonb_object_agg(status,n) FROM
    (SELECT status,count(*) AS n FROM music_creations GROUP BY status) q),'{}'::jsonb),
  'music_steps_by_status', COALESCE((SELECT jsonb_object_agg(status,n) FROM
    (SELECT status,count(*) AS n FROM music_steps GROUP BY status) q),'{}'::jsonb),
  'music_steps_by_dispatch_state', COALESCE((SELECT jsonb_object_agg(provider_dispatch_state,n) FROM
    (SELECT provider_dispatch_state,count(*) AS n FROM music_steps GROUP BY provider_dispatch_state) q),'{}'::jsonb),
  'music_queued', (SELECT count(*) FROM music_creations WHERE status='QUEUED'),
  'music_running', (SELECT count(*) FROM music_creations WHERE status='RUNNING'),
  'music_current_send_started', (SELECT count(*) FROM music_steps WHERE provider_dispatch_state='SEND_STARTED'),
  'music_dispatch_history_send_started', (SELECT count(*) FROM music_dispatches WHERE dispatch_state='SEND_STARTED'),
  'music_dispatch_unfinished', (SELECT count(*) FROM music_dispatches WHERE finished_at IS NULL),
  'music_execution_attempts_unfinished', (SELECT count(*) FROM creation_execution_attempts e
    JOIN music_creations c ON c.id=e.creation_id WHERE e.finished_at IS NULL),
  'music_creation_explicit_uncertain', (SELECT count(*) FROM music_creations
    WHERE error_code IN ('PROVIDER_OUTCOME_UNCERTAIN','PROVIDER_DISPATCH_AMBIGUOUS')),
  'music_step_explicit_uncertain', (SELECT count(*) FROM music_steps
    WHERE error_code IN ('PROVIDER_OUTCOME_UNCERTAIN','PROVIDER_DISPATCH_AMBIGUOUS')),
  'music_dispatch_resolution_uncertain', (SELECT count(*) FROM music_dispatches
    WHERE resolution_code IN ('PROVIDER_OUTCOME_UNCERTAIN','PROVIDER_DISPATCH_AMBIGUOUS')),
  'music_execution_resolution_uncertain', (SELECT count(*) FROM creation_execution_attempts e
    JOIN music_creations c ON c.id=e.creation_id
    WHERE e.resolution_code IN ('PROVIDER_OUTCOME_UNCERTAIN','PROVIDER_DISPATCH_AMBIGUOUS'))
)::text;
ROLLBACK;
"""

PRIVATE_SAMPLE_SQL = """BEGIN READ ONLY; SET LOCAL statement_timeout='8000ms';
SELECT coalesce(jsonb_agg(item),'[]'::jsonb)::text FROM (
 SELECT sr.painting_id::text AS painting_id,
 bool_or(fd.field_key='source.original_sequence' AND fd.visibility<>'public') AS hidden_sequence,
 bool_or(fd.field_key IN ('source.relative_image','source.original_image_reference') AND fd.visibility<>'public') AS hidden_image
 FROM catalog_source_records sr
 JOIN catalog_batch_current_revisions cr ON cr.batch_id=sr.batch_id AND cr.revision=sr.revision
 JOIN catalog_paintings p ON p.public_id=sr.painting_id
  AND p.current_batch_id=sr.batch_id AND p.current_revision=sr.revision
 JOIN catalog_annotation_field_definitions fd ON fd.batch_id=sr.batch_id
  AND fd.revision=sr.revision AND fd.annotation_file_id=sr.annotation_file_id
 WHERE fd.field_key IN ('source.original_sequence','source.relative_image','source.original_image_reference')
 GROUP BY sr.painting_id
 HAVING bool_or(fd.visibility<>'public')
 ORDER BY sr.painting_id LIMIT 3
) item; ROLLBACK;"""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Refused('http_redirect_refused')


def request(path, accept='application/json', *, bearer=None, port=19080):
    must(type(path) is str and path.startswith('/api/v1/') and
         not any(x in path for x in ('://', '\\', '\r', '\n', '#')), 'fixed_http_path')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    must(type(port) is int and 1024 <= port <= 65535, 'loopback_http_port')
    headers = {'Accept': accept}
    if bearer is not None:
        must(type(bearer) is str and re.fullmatch(r'[A-Za-z0-9._~-]{16,8192}', bearer), 'acceptance_session_format')
        headers['Authorization'] = 'Bearer ' + bearer
    req = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
                                 headers=headers, method='GET')
    try:
        response = opener.open(req, timeout=8)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, urllib.error.URLError) as error:
        raise Refused('local_http_unavailable') from error
    with response:
        body = response.read(2 * 1024 * 1024 + 1)
        must(len(body) <= 2 * 1024 * 1024, 'http_response_limit')
        return response.status, response.headers.get_content_type(), body


def json_response(path, expected_status=200, get=request):
    status, content_type, raw = get(path)
    must(status == expected_status and content_type == 'application/json', 'http_json_contract')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise Refused('http_json_parse') from error
    must(type(value) is dict, 'http_json_object')
    return value


def verify_http(query, *, minimum_total=1, get=request):
    gallery = json_response('/api/v1/paintings?page=0&size=1', get=get)
    items = gallery.get('items')
    total = gallery.get('totalElements')
    must(type(total) is int and total >= minimum_total and isinstance(items, list) and
         len(items) == 1 and isinstance(items[0], dict), 'gallery_pagination_contract')
    painting = items[0].get('paintingId')
    must(type(painting) is str and re.fullmatch('[0-9a-fA-F-]{36}', painting), 'gallery_painting_identity')
    # SecurityConfig explicitly protects detail/guide/critic. A successful
    # anonymous detail response is a regression, not a readiness success.
    detail = json_response('/api/v1/paintings/' + painting, 401, get=get)
    must(detail.get('code') == 'UNAUTHORIZED' and detail.get('status') == 401,
         'detail_authentication_contract')
    unknown = '00000000-0000-4000-8000-000000000000'
    missing_detail = json_response('/api/v1/paintings/' + unknown, 401, get=get)
    must(missing_detail.get('code') == 'UNAUTHORIZED' and missing_detail.get('status') == 401,
         'unknown_detail_authentication_contract')
    # The Accept image path must still return JSON for business errors.
    def image_get(path):
        return get(path, 'image/*,application/octet-stream')
    json_response('/api/v1/assets/' + unknown + '/content', 404, get=image_get)
    try:
        samples = json.loads(query(PRIVATE_SAMPLE_SQL))
    except (ValueError, UnicodeError) as error:
        raise Refused('private_projection_sample_unknown') from error
    must(type(samples) is list and len(samples) <= 3, 'private_projection_sample_shape')
    for item in samples:
        must(type(item) is dict and set(item) == {'painting_id', 'hidden_sequence', 'hidden_image'},
             'private_projection_sample_shape')
        identity = item['painting_id']
        must(type(identity) is str and re.fullmatch('[0-9a-f-]{36}', identity), 'private_projection_identity')
        must(type(item['hidden_sequence']) is bool and type(item['hidden_image']) is bool,
             'private_projection_flags')
        sample = json_response('/api/v1/paintings/' + identity, 401, get=get)
        must(sample.get('code') == 'UNAUTHORIZED' and sample.get('status') == 401,
             'private_detail_authentication_contract')
    return {'schema': 'R25_CORE_READONLY_HTTP_ACCEPTANCE_V1', 'gallery_total': total,
            'anonymous_gallery': True, 'detail_requires_authentication': True,
            'public_asset_json_error': True,
            'private_projection_pg_sample_count': len(samples),
            'authenticated_detail_projection': 'NOT_EXERCISED_NO_TEST_CREDENTIAL',
            'private_projection_live_samples': 0,
            'write_requests': 0, 'provider_requests': 0}


KNOWLEDGE_PATHS = ('/api/v1/knowledge/painting-statistics',
                   '/api/v1/knowledge/poetry-graph', '/api/v1/knowledge/china-map')


def authenticated_request(token):
    must(type(token) is str and re.fullmatch(r'[A-Za-z0-9._~-]{16,8192}', token), 'acceptance_session_format')
    def get(path, accept='application/json'):
        return request(path, accept, bearer=token)
    return get


def verify_gallery_followup_http(*, get_authenticated, monotonic=time.monotonic,
                                today=lambda: datetime.now(ZoneInfo('Asia/Shanghai')).date()):
    """Finite Gallery-daily capability acceptance for the new candidate only.

    Eight GETs using the existing fixed-origin, no-redirect, 8-second HTTP
    adapter. Each response is bounded there; this sequence also refuses after
    60 seconds and never retries. A catalog/date change is an inconclusive
    acceptance, not permission to mutate records or silently pick another day.
    Returned evidence contains only counts, date and a keyword digest.
    """
    started = monotonic(); day = today(); calls = 0

    def fetch(sort, page=0, *, order_date=None, keyword=None):
        nonlocal calls
        must(monotonic() - started < 60, 'gallery_followup_deadline')
        must(today() == day, 'gallery_followup_date_changed')
        parameters = {'sort': sort, 'direction': 'asc', 'page': page, 'size': 2}
        if order_date is not None: parameters['orderDate'] = order_date
        if keyword is not None: parameters['keyword'] = keyword
        calls += 1
        must(calls <= 8, 'gallery_followup_request_limit')
        value = json_response('/api/v1/paintings?' + urlencode(parameters), get=get_authenticated)
        must(monotonic() - started < 60, 'gallery_followup_deadline')
        must(today() == day, 'gallery_followup_date_changed')
        actual_page = 0 if sort == 'daily' and order_date != day.isoformat() else page
        total, items = value.get('totalElements'), value.get('items')
        must(type(total) is int and total >= 0 and type(items) is list and
             type(value.get('page')) is int and value['page'] == actual_page and
             type(value.get('size')) is int and value['size'] == 2 and
             type(value.get('totalPages')) is int and value['totalPages'] == (total + 1) // 2 and
             len(items) == min(2, max(0, total - actual_page * 2)), 'gallery_followup_pagination')
        for field, expected in (('first', actual_page == 0), ('last', (actual_page + 1) * 2 >= total),
                                ('hasNext', (actual_page + 1) * 2 < total)):
            must(type(value.get(field)) is bool and value[field] is expected, 'gallery_followup_pagination_flags')
        must(value.get('orderDate') == (day.isoformat() if sort == 'daily' else None),
             'gallery_followup_order_date')
        ids = []
        for item in items:
            must(type(item) is dict and type(item.get('paintingId')) is str, 'gallery_followup_identity')
            identity = item['paintingId']
            try:
                canonical = str(UUID(identity))
            except (ValueError, AttributeError):
                raise Refused('gallery_followup_identity') from None
            must(identity == canonical, 'gallery_followup_identity')
            ids.append(identity)
        must(len(ids) == len(set(ids)), 'gallery_followup_duplicate_item')
        return value, ids

    source, _ = fetch('source')
    must(source['totalElements'] > 0, 'gallery_followup_empty_catalog')
    daily, ids = fetch('daily')
    repeat, repeated_ids = fetch('daily', order_date=day.isoformat())
    second, next_ids = fetch('daily', page=1, order_date=day.isoformat())
    reset, reset_ids = fetch('daily', page=1, order_date=(day - timedelta(days=1)).isoformat())
    must(ids == repeated_ids == reset_ids and not set(ids).intersection(next_ids),
         'gallery_followup_daily_order')
    # Use only an already public value, bounded well below Core's 200-char
    # keyword limit. URL encoding is transport quoting, not wildcard escaping;
    # literal %, _, Chinese and backslash correctness belongs to real PG tests.
    keyword = next((item[field].strip()[:24] for item in source['items']
                    for field in ('title', 'authorName', 'subject')
                    if type(item.get(field)) is str and item[field].strip()), None)
    must(keyword is not None, 'gallery_followup_public_keyword_unavailable')
    filtered_source, _ = fetch('source', keyword=keyword)
    filtered_daily, _ = fetch('daily', keyword=keyword, order_date=day.isoformat())
    final, _ = fetch('source')
    must(all(value['totalElements'] == source['totalElements'] for value in
             (daily, repeat, second, reset, final)), 'gallery_followup_catalog_changed')
    must(0 < filtered_source['totalElements'] == filtered_daily['totalElements'] <= source['totalElements'],
         'gallery_followup_keyword_count')
    return {'schema': 'R25_GALLERY_DAILY_READONLY_HTTP_ACCEPTANCE_V1', 'order_date': day.isoformat(),
            'gallery_total': source['totalElements'], 'keyword_total': filtered_source['totalElements'],
            'keyword_sha256': hashlib.sha256(keyword.encode('utf-8')).hexdigest(),
            'daily_stable': True, 'pagination_disjoint': True, 'stale_date_resets_page': True,
            'read_requests': calls, 'write_requests': 0, 'provider_requests': 0,
            'elapsed_ms': round((monotonic() - started) * 1000)}


def verify_knowledge_http(query, policy, *, get=request, get_authenticated):
    """Candidate-specific GET-only acceptance. Return counts/hashes, never data/session.

    Stable bracketing Gallery reads detect a changing denominator; prior isolated
    public-field mutation tests remain required reviewed evidence, not a claim
    that production annotations were mutated by this read-only check.
    """
    for path in KNOWLEDGE_PATHS:
        denied = json_response(path, 401, get=get)
        must(denied.get('code') == 'UNAUTHORIZED' and denied.get('status') == 401,
             'knowledge_anonymous_contract')
    profile = policy['upgrade'].get('release_profile')
    knowledge = policy['upgrade']['configuration']['knowledge']
    must(profile in ('poetry-only', 'map-enabled') and
         knowledge.get('map-enabled') is (profile == 'map-enabled'), 'knowledge_release_profile')
    before = json_response('/api/v1/paintings?page=0&size=1', get=get)
    stats, graph = [json_response(path, get=get_authenticated) for path in KNOWLEDGE_PATHS[:2]]
    china = json_response(KNOWLEDGE_PATHS[2], 200 if profile == 'map-enabled' else 503, get=get_authenticated)
    after = json_response('/api/v1/paintings?page=0&size=1', get=get)
    count = stats.get('population', {}).get('count')
    must(stats.get('schemaVersion') == 1 and stats.get('population', {}).get('key') == 'gallery-displayable'
         and type(count) is int and count >= 0 and
         count == before.get('totalElements') == after.get('totalElements'), 'knowledge_gallery_denominator')
    names = stats.get('authorNames', {})
    must(type(names.get('count')) is int and type(names.get('eligibleRecords')) is int and
         0 <= names['count'] <= names['eligibleRecords'] <= count, 'knowledge_author_name_coverage')
    metrics = stats.get('metrics')
    must(type(metrics) is dict and set(metrics) == {'dynasty', 'category', 'geography', 'style',
         'imagery', 'sentiment', 'sentimentByDynasty'}, 'knowledge_metric_contract')
    for name, metric in metrics.items():
        must(type(metric) is dict and metric.get('status') in {'available', 'unavailable'}, 'knowledge_metric_status')
        eligible, missing, unmatched = (metric.get(k) for k in
            ('eligibleRecords', 'missingOrRestrictedRecords', 'unmatchedRecords'))
        must(all(type(x) is int for x in (eligible, missing, unmatched)) and
             0 <= unmatched <= eligible <= count and missing == count - eligible, 'knowledge_metric_coverage')
        limit = 12 if name == 'style' else 40 if name == 'imagery' else 50
        must(type(metric.get('items')) is list and len(metric['items']) <= limit, 'knowledge_metric_bound')
    must(graph.get('schemaVersion') == 1 and graph.get('source') == 'legacy-poetry-snapshot' and
         graph.get('graphSha256') == knowledge['graph-sha256'] and
         graph.get('statsSha256') == knowledge['stats-sha256'], 'knowledge_graph_identity')
    nodes, links = graph.get('nodes'), graph.get('links')
    must(type(nodes) is list and type(links) is list and
         graph.get('nodeCount') == len(nodes) <= 2000 and graph.get('edgeCount') == len(links) <= 10000,
         'knowledge_graph_counts')
    ids = [node.get('id') for node in nodes if type(node) is dict]
    must(len(ids) == len(nodes) and all(type(x) is str for x in ids) and len(set(ids)) == len(ids),
         'knowledge_graph_ids')
    must(all(type(link) is dict and link.get('source') in ids and link.get('target') in ids for link in links),
         'knowledge_graph_endpoints')
    if profile == 'map-enabled':
        must(china.get('type') == 'FeatureCollection' and china.get('sha256') == knowledge['china-sha256'] and
             type(china.get('features')) is list and 0 < len(china['features']) <= 100, 'knowledge_map_identity')
    else:
        # A generic failed map load is not evidence of the approved disabled
        # capability. Require its exact safe error envelope and no data fields.
        must(set(china) <= {'timestamp', 'status', 'code', 'message', 'path', 'correlationId', 'validationErrors', 'violations'} and
             china.get('status') == 503 and china.get('code') == 'KNOWLEDGE_SNAPSHOT_UNAVAILABLE' and
             china.get('message') == '此版本未启用地理底图；地域统计仍可读取。' and
             china.get('path') == KNOWLEDGE_PATHS[2] and
             china.get('validationErrors') in (None, {}) and china.get('violations') in (None, []),
             'knowledge_map_disabled_contract')
    samples = json.loads(query(PRIVATE_SAMPLE_SQL))
    must(type(samples) is list and len(samples) <= 3, 'private_projection_sample_shape')
    for sample in samples:
        identity = sample.get('painting_id', '')
        must(type(identity) is str and re.fullmatch('[0-9a-f-]{36}', identity), 'private_projection_identity')
        detail = json_response('/api/v1/paintings/' + identity, get=get_authenticated)
        if sample.get('hidden_sequence'):
            must(detail.get('sourceSequence') in (None, ''), 'authenticated_private_sequence')
        if sample.get('hidden_image'):
            must(detail.get('imageStorageName') in (None, ''), 'authenticated_private_image')
        annotations = detail.get('annotations')
        must(type(annotations) is list and all(type(a) is dict and a.get('visibility') == 'public'
             for a in annotations), 'authenticated_annotation_projection')
    receipt = {'schema': 'R25_KNOWLEDGE_HTTP_ACCEPTANCE_V2', 'release_profile': profile, 'gallery_total': count,
            'author_name_count': names['count'], 'node_count': len(nodes), 'edge_count': len(links),
            'map_enabled': profile == 'map-enabled', 'graph_sha256': graph['graphSha256'],
            'stats_sha256': graph['statsSha256'],
            'authenticated_projection_samples': len(samples),
            'projection_mutation_test': 'REUSED_REVIEWED_ISOLATED_EVIDENCE_NOT_RUN_ON_PRODUCTION',
            'write_requests': 0, 'provider_requests': 0}
    if profile == 'map-enabled':
        receipt.update(map_features=len(china['features']), china_sha256=china['sha256'])
    else:
        receipt['map_authenticated_status'] = 503
    return receipt


def verify_managed_vmm_closed_http(*,get_authenticated):
    """One authenticated non-generating capability GET; no VMM dispatch probe."""
    value=json_response('/api/v1/workflow/node-types',get=get_authenticated)
    must(value.get('featureEnabled') is True and type(value.get('operations'))is list,'managed_vmm_capability_contract')
    music=[x for x in value['operations'] if type(x)is dict and x.get('code')=='PAINTING_TO_MUSIC']
    must(len(music)==1 and music[0].get('inputModality')=='PAINTING' and music[0].get('outputModality')=='AUDIO' and
         music[0].get('executionAvailable')is False and music[0].get('availabilityReason') in
         {'CREATION_PROVIDER_NOT_READY','CREATION_PROVIDER_CONFIGURATION_MISSING','CREATION_PROVIDER_CONFIGURATION_INVALID'},'managed_vmm_dispatch_not_closed')
    providers=music[0].get('providers')
    must(type(providers)is list and len(providers)>0 and all(type(x)is dict and x.get('executionAvailable')is False for x in providers),'managed_vmm_provider_not_closed')
    return {'contract':'managed-vmm-closed-v1','requests':1,'generation_requests':0,'dispatch_permitted':False}


def verify_managed_vmm_inventory(value,*,require_clear=True):
    groups={'music_creations_by_status','music_steps_by_status','music_steps_by_dispatch_state'}
    counters={'music_queued','music_running','music_current_send_started','music_dispatch_history_send_started',
        'music_dispatch_unfinished','music_execution_attempts_unfinished','music_creation_explicit_uncertain',
        'music_step_explicit_uncertain','music_dispatch_resolution_uncertain','music_execution_resolution_uncertain'}
    must(type(value)is dict and set(value)==groups|counters,'managed_vmm_inventory_contract')
    for key in groups:
        must(type(value[key])is dict and all(type(k)is str and re.fullmatch('[A-Z_]{1,40}',k) and type(n)is int and n>=0 for k,n in value[key].items()),'managed_vmm_inventory_counts')
    must(all(type(value[k])is int and value[k]>=0 for k in counters),'managed_vmm_inventory_counts')
    if require_clear:must(all(value[k]==0 for k in counters),'managed_vmm_work_requires_readonly_reconciliation')
    return value
