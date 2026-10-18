"""Bounded GET-only Assistant acceptance through Core, never a provider probe.

The reviewed coordinator supplies the private authenticated adapter and has
already validated source, policy, installed bytes and the shared lease. This
module binds observed mode/storage/registry to that policy. It neither starts
execution nor infers real provider capability from configured model labels.
"""
from __future__ import annotations

import re

from core_acceptance import json_response, request
from core_release_guard import must

PATHS = ('/api/v1/assistant/status', '/api/v1/assistant/storage',
         '/api/v1/assistant/models', '/api/v1/assistant/sessions/page?limit=1')


def _seconds(value):
    # Whole-second positive java.time.Duration policy subset. No production
    # default, unit guessing, floating point truncation or arbitrary expression.
    match = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)(?:\.0+)?S)?)?',
                         value if type(value) is str else '')
    must(match is not None and any(x is not None for x in match.groups()),
         'assistant_http_draft_policy')
    seconds = sum(int(x or 0) * scale for x, scale in zip(match.groups(), (86400, 3600, 60, 1)))
    must(0 < seconds <= 9007199254740991, 'assistant_http_draft_policy')
    return seconds


def _unavailable(path, codes, get):
    value = json_response(path, 503, get=get)
    must(value.get('status') == 503 and value.get('code') in codes,
         'assistant_http_disabled_contract')


def _storage(value, configured, history_only):
    must(value.get('policyStatus') == 'VALID' and value.get('historyOnly') is history_only,
         'assistant_http_storage_policy')
    quota = configured.get('storage.quota-bytes')
    must(type(quota) is int and 0 < quota <= 9007199254740991 and
         type(value.get('quotaBytes')) is int and value['quotaBytes'] == quota,
         'assistant_http_storage_quota')
    must(type(value.get('draftLifetimeSeconds')) is int and
         value['draftLifetimeSeconds'] == _seconds(configured.get('storage.draft-lifetime')),
         'assistant_http_draft_binding')
    must(type(configured.get('storage.cleanup-enabled')) is bool and
         value.get('cleanupEnabled') is configured['storage.cleanup-enabled'],
         'assistant_http_cleanup_binding')
    for key in ('readyBytes', 'reservedBytes', 'logicalBytes', 'cleanupReservedBytes',
                'chargedBytes', 'retainedFileBytes', 'pendingCleanupBytes', 'availableBytes'):
        must(type(value.get(key)) is int and 0 <= value[key] <= 9007199254740991,
             'assistant_http_storage_accounting')
    must(value['logicalBytes'] == value['readyBytes'] + value['reservedBytes'] and
         value['chargedBytes'] == value['logicalBytes'] + value['cleanupReservedBytes'] and
         value['availableBytes'] == max(0, quota - value['chargedBytes']) and
         value['pendingCleanupBytes'] <= value['retainedFileBytes'],
         'assistant_http_storage_accounting')
    # A full account is valid. Reading history must not require uploading anew.
    must(value.get('uploadsAvailable') is (not history_only and value['availableBytes'] > 0),
         'assistant_http_upload_mode')


def _models(value, configured, retrieval_profile=None):
    digest = configured.get('execution.expected-config-sha256')
    must(type(digest) is str and re.fullmatch('[a-f0-9]{64}', digest) and
         type(value.get('contractVersion')) is int and value['contractVersion'] == 1 and
         value.get('configSha256') == digest, 'assistant_http_registry_binding')
    must(retrieval_profile in (None, 'artwork-only-v1'), 'assistant_http_retrieval_profile')
    if retrieval_profile == 'artwork-only-v1':
        # Current AI registry truthfully uses NOT_CONFIGURED when ragflow is
        # disabled. Per-run DISABLED is a different contract; neither a live
        # retrieval call nor a synthetic model answer belongs in this probe.
        must(value.get('retrieval') == {'state': 'NOT_CONFIGURED'},
             'assistant_http_ragflow_must_be_disabled')
    models = value.get('models')
    must(type(models) is list and 1 <= len(models) <= 20, 'assistant_http_registry_models')
    identities = set()
    usable = 0
    for model in models:
        must(type(model) is dict, 'assistant_http_registry_model')
        identity = model.get('id')
        must(type(identity) is str and re.fullmatch('[A-Za-z0-9._-]{1,80}', identity) and
             identity not in identities, 'assistant_http_registry_identity')
        identities.add(identity)
        must(type(model.get('enabled')) is bool and type(model.get('configured')) is bool and
             type(model.get('evidence')) is str, 'assistant_http_registry_configuration')
        capabilities = model.get('capabilities')
        must(type(capabilities) is dict and type(capabilities.get('images')) is bool and
             type(capabilities.get('streaming')) is bool and
             capabilities.get('imageMimeTypes') == (['image/jpeg', 'image/png'] if capabilities['images'] else []),
             'assistant_http_registry_capabilities')
        usable += int(model['configured'] and model['enabled'] and capabilities['streaming'])
    must(usable > 0, 'assistant_http_no_configured_streaming_model')
    return len(models), usable, digest


def verify_assistant_http(policy, *, get_authenticated, get=request):
    """Return a sanitized receipt; do not retain session bodies or bearer data.

    Eight bounded reads, no retries, no uploads or execution submission. An
    injected GET adapter is for isolated integration tests only; production
    callers keep the existing fixed-loopback no-redirect HTTP adapter.
    """
    upgrade = policy.get('upgrade', {})
    must(upgrade.get('capability') == 'assistant-v1', 'assistant_http_candidate_required')
    contract = upgrade.get('assistant', {})
    configured = dict(upgrade.get('configuration', {}).get('assistant', {}))
    if 'successor' in upgrade:
        import core_release_guard as guard
        phase = guard.successor_configuration_phase(policy)
        must(phase != 'transition', 'successor_acceptance_configuration_incomplete')
        if phase == 'final':
            configured['execution.expected-config-sha256'] = upgrade['successor']['ai_configuration']['after_sha256']
    mode = contract.get('mode')
    retrieval_profile = contract.get('retrieval_profile')
    must(retrieval_profile in (None, 'artwork-only-v1'), 'assistant_http_retrieval_profile')
    must(mode in ('disabled', 'history-only', 'enabled') and
         configured.get('enabled') is (mode != 'disabled') and
         configured.get('history-only') is (mode == 'history-only') and
         configured.get('execution.enabled') is (mode == 'enabled'),
         'assistant_http_policy_mode')
    for path in PATHS:
        denied = json_response(path, 401, get=get)
        must(denied.get('status') == 401 and denied.get('code') == 'UNAUTHORIZED',
             'assistant_http_authentication')
    status = json_response(PATHS[0], get=get_authenticated)
    must(status.get('enabled') is (mode != 'disabled') and
         status.get('executionAvailable') is (mode == 'enabled') and
         status.get('stage') == ('execution' if mode == 'enabled' else 'foundation') and
         type(status.get('automaticSummaryAvailable')) is bool,
         'assistant_http_runtime_mode')
    if mode != 'enabled':
        must(status['automaticSummaryAvailable'] is False, 'assistant_http_disabled_summary')
    receipt = {'schema': 'R25_ASSISTANT_READONLY_HTTP_ACCEPTANCE_V1', 'mode': mode,
               'authentication_required': True, 'history_read_available': False,
               'storage_policy_valid': False, 'registry_model_count': 0,
               'configured_streaming_model_count': 0, 'registry_configuration_sha256': None,
               'provider_capabilities': 'NOT_TESTED_CONFIGURED_REGISTRY_ONLY',
               'retrieval_profile': retrieval_profile,
               'ragflow_registry_disabled': None,
               'read_requests': 8, 'write_requests': 0, 'provider_requests': 0}
    if mode == 'disabled':
        for path in (PATHS[1], PATHS[3]):
            _unavailable(path, {'ASSISTANT_DISABLED'}, get_authenticated)
        _unavailable(PATHS[2], {'ASSISTANT_EXECUTION_DISABLED'}, get_authenticated)
        return receipt
    usage = json_response(PATHS[1], get=get_authenticated)
    _storage(usage, configured, mode == 'history-only')
    page = json_response(PATHS[3], get=get_authenticated)
    must(type(page.get('items')) is list and len(page['items']) <= 1 and
         type(page.get('hasMore')) is bool and
         ((page['hasMore'] and type(page.get('nextCursor')) is str and bool(page['nextCursor'])) or
          (not page['hasMore'] and page.get('nextCursor') is None)), 'assistant_http_history_page')
    receipt.update(history_read_available=True, storage_policy_valid=True)
    if mode == 'history-only':
        # Execution is explicitly disabled as well as historyOnly, so the
        # existing configuration's first guard returns EXECUTION_DISABLED.
        _unavailable(PATHS[2], {'ASSISTANT_EXECUTION_DISABLED', 'ASSISTANT_HISTORY_ONLY'}, get_authenticated)
    else:
        count, usable, digest = _models(json_response(PATHS[2], get=get_authenticated), configured,
                                       retrieval_profile)
        receipt.update(registry_model_count=count, configured_streaming_model_count=usable,
                       registry_configuration_sha256=digest,
                       ragflow_registry_disabled=True if retrieval_profile == 'artwork-only-v1' else None)
    return receipt
