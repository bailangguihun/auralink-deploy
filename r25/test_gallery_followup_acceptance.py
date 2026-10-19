"""Finite read-only Gallery capability gate; no server or provider is invoked."""
from datetime import date
import json
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import core_acceptance as acceptance
import core_release_guard as guard
import core_runtime


class GalleryFollowupAcceptance(unittest.TestCase):
    day = date(2026, 10, 19)

    def fixture(self, mutate=None, total=7):
        calls = []
        identities = [f'00000000-0000-4000-8000-{i:012d}' for i in range(1, total + 1)]

        def get(path, accept='application/json'):
            calls.append(path)
            self.assertEqual(urlsplit(path).path, '/api/v1/paintings')
            query = {key: value[0] for key, value in parse_qs(urlsplit(path).query).items()}
            self.assertEqual(set(query) - {'sort', 'direction', 'page', 'size', 'orderDate', 'keyword'}, set())
            order = query['sort']
            rows = identities if order == 'source' else identities[::-1]
            if 'keyword' in query:
                self.assertEqual(query['keyword'], '山水%_\\图')
                rows = rows[:min(3, len(rows))]
            page = int(query['page'])
            if order == 'daily' and query.get('orderDate') != self.day.isoformat(): page = 0
            count = len(rows)
            value = {'page': page, 'size': 2, 'totalElements': count, 'totalPages': (count + 1) // 2,
                     'first': page == 0, 'last': (page + 1) * 2 >= count, 'hasNext': (page + 1) * 2 < count,
                     'items': [{'paintingId': identity, 'title': '山水%_\\图'} for identity in rows[page * 2:page * 2 + 2]]}
            if order == 'daily': value['orderDate'] = self.day.isoformat()
            if mutate: mutate(value, query, len(calls))
            return 200, 'application/json', json.dumps(value).encode()
        return calls, get

    def test_eight_bounded_gets_accept_daily_anchor_keyword_counts_and_reset(self):
        calls, get = self.fixture()
        result = acceptance.verify_gallery_followup_http(get_authenticated=get, today=lambda: self.day)
        self.assertEqual(result['read_requests'], 8)
        self.assertEqual(result['gallery_total'], 7)
        self.assertEqual(result['keyword_total'], 3)
        self.assertTrue(result['daily_stable'] and result['pagination_disjoint'] and result['stale_date_resets_page'])
        self.assertEqual(result['write_requests'], 0)
        self.assertEqual(result['provider_requests'], 0)
        self.assertNotIn('山水', json.dumps(result, ensure_ascii=False))
        self.assertEqual(len(calls), 8)

    def test_small_catalog_second_page_is_legitimately_empty(self):
        _, get = self.fixture(total=1)
        result = acceptance.verify_gallery_followup_http(get_authenticated=get, today=lambda: self.day)
        self.assertEqual(result['gallery_total'], 1)

    def test_date_count_pagination_and_identity_drift_refused(self):
        def change_count(value, query, call):
            if call == 8:
                value['totalElements'] = 8; value['totalPages'] = 4
        cases = [
            ('missing_date', lambda value, query, call: value.pop('orderDate', None) if query['sort'] == 'daily' else None,
             'order_date'),
            ('wrong_page', lambda value, query, call: value.update(page=1) if call == 2 else None, 'pagination'),
            ('flags', lambda value, query, call: value.update(hasNext=False) if call == 2 else None, 'pagination_flags'),
            ('invalid_id', lambda value, query, call: value['items'][0].update(paintingId='../../secret') if call == 2 else None,
             'identity'),
            ('duplicate', lambda value, query, call: value['items'].__setitem__(1, value['items'][0]) if call == 2 else None,
             'duplicate_item'),
            ('overlap', lambda value, query, call: value['items'][0].update(paintingId='00000000-0000-4000-8000-000000000007') if call == 4 else None,
             'daily_order'),
            ('changed_total', change_count, 'catalog_changed'),
            ('inconsistent_keyword', lambda value, query, call: value.update(totalElements=4, totalPages=2) if call == 7 else None,
             'keyword_count')]
        for name, mutation, reason in cases:
            with self.subTest(name=name):
                _, get = self.fixture(mutation)
                with self.assertRaisesRegex(guard.Refused, reason):
                    acceptance.verify_gallery_followup_http(get_authenticated=get, today=lambda: self.day)

    def test_timeout_and_midnight_do_not_retry_or_extend_window(self):
        calls, get = self.fixture()
        ticks = iter((0, 0, 61))
        with self.assertRaisesRegex(guard.Refused, 'deadline'):
            acceptance.verify_gallery_followup_http(get_authenticated=get, today=lambda: self.day,
                                                   monotonic=lambda: next(ticks))
        self.assertEqual(len(calls), 1)
        calls, get = self.fixture()
        days = iter((self.day, self.day, date(2026, 10, 20)))
        with self.assertRaisesRegex(guard.Refused, 'date_changed'):
            acceptance.verify_gallery_followup_http(get_authenticated=get, today=lambda: next(days))
        self.assertEqual(len(calls), 1)

    def test_http_error_is_not_success_and_does_not_retry(self):
        calls = []
        def failed_get(path):
            calls.append(path)
            return 503, 'application/json', b'{"code":"CATALOG_READ_UNAVAILABLE"}'
        with self.assertRaisesRegex(guard.Refused, 'http_json_contract'):
            acceptance.verify_gallery_followup_http(get_authenticated=failed_get, today=lambda: self.day)
        self.assertEqual(len(calls), 1)

    def test_only_followup_candidate_not_baseline_or_old_successor_requires_daily(self):
        candidate_sha = 'c' * 64
        value = {'upgrade': {'capability': 'assistant-v1', 'candidate': {'artifact_sha256': candidate_sha},
                            'core_followup': {'acceptance': 'gallery-daily-v1'}},
                 'runtime_modules': {'assistant_acceptance.py': 'a' * 64}}
        rt = core_runtime.CoreRuntime.__new__(core_runtime.CoreRuntime)
        rt.query = lambda sql: ''
        rt.read = lambda path: {'bearer': 'a' * 32}
        module = SimpleNamespace(verify_assistant_http=lambda *args, **kwargs: {'assistant': 'fixture'})
        with patch.object(core_runtime, 'verify_http', side_effect=lambda *args, **kwargs: {'gallery_total': 7}), \
             patch.object(core_runtime, 'verify_knowledge_http', return_value={'knowledge': 'fixture'}), \
             patch.object(core_runtime, 'verify_gallery_followup_http', return_value={'daily': 'fixture'}) as daily, \
             patch.object(guard, 'release_record', return_value={'schema': guard.BUSINESS_RECORD_SCHEMA}), \
             patch.object(guard, 'policy', return_value=value), \
             patch.object(guard, 'read_file', return_value=b''), \
             patch.object(guard, 'source_module', return_value=module):
            before = SimpleNamespace(artifact_sha256='b' * 64, accepted_record_sha256='d' * 64, commit_sha='1' * 40)
            self.assertNotIn('gallery_daily', rt.acceptance(before))
            daily.assert_not_called()
            after = SimpleNamespace(artifact_sha256=candidate_sha, accepted_record_sha256='e' * 64, commit_sha='2' * 40)
            self.assertEqual(rt.acceptance(after)['gallery_daily'], {'daily': 'fixture'})
            daily.assert_called_once()
            del value['upgrade']['core_followup']
            self.assertNotIn('gallery_daily', rt.acceptance(after))
            daily.assert_called_once()


if __name__ == '__main__':
    unittest.main()
