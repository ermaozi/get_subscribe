import base64
from contextlib import nullcontext
import yaml
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import main

CLASH = 'proxies:\n  - {name: test, type: ss, server: example.com, port: 443}\n'
NODES = 'vmess://example\nvless://example'


class SubscriptionTests(unittest.TestCase):
    def test_real_formats_and_error_pages(self):
        for text, expected in [(CLASH, 'clash'), (NODES, 'v2ray'),
                (base64.b64encode(NODES.encode()).decode().rstrip('='), 'v2ray'),
                ('https://example.com:443#proxy', 'v2ray'), ('socks5://example.com:1080', 'v2ray'),
                ('https://example.com/article', None), ('https://example.com:bad', None), ('proxies: []', None), ('proxies: [', None),
                ('<html>error vmess://example</html>', None), ('vmess://example\n<html>error</html>', None),
                ('proxies:\n - broken', None), ('', None)]:
            with self.subTest(text=text):
                self.assertEqual(main._detect_kind(text), expected)

    def test_download_uses_tls_and_skips_invalid_content(self):
        session = Mock()
        session.get.side_effect = [Mock(status_code=200, text='<html>error</html>'),
                                  Mock(status_code=200, text=CLASH), Mock(status_code=200, text=NODES)]
        with patch.object(main, 'write_log'):
            found = main._classify_subscriptions(session, ['https://example.com/error', 'https://example.com/c', 'https://example.com/v'])
        self.assertEqual(set(found), {'clash', 'v2ray'})
        self.assertTrue(all(call.kwargs == {'timeout': 20} for call in session.get.call_args_list))

    def test_collection_and_failed_source_keeps_cache(self):
        feed = '<rss><channel><item><description>https://example.com/c https://example.com/v</description></item></channel></rss>'
        session = Mock()
        session.get.side_effect = [Mock(status_code=200, text=feed, content=feed.encode()),
            Mock(status_code=200, text=CLASH, content=CLASH.encode()),
            Mock(status_code=200, text=NODES)]
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd(); os.chdir(directory)
            try:
                with patch.object(main, '_build_session', return_value=nullcontext(session)), patch.object(main, 'DIRECT_SOURCES', {}):
                    self.assertEqual(main.main(), 0)
                self.assertEqual(yaml.safe_load(Path('subscribe/clash.yml').read_text()), yaml.safe_load(CLASH))
                self.assertEqual(Path('subscribe/v2ray.txt').read_text().strip(), NODES)
                session.get.side_effect = [Mock(raise_for_status=Mock(side_effect=main.requests.HTTPError))]
                with patch.object(main, '_build_session', return_value=nullcontext(session)), patch.object(main, 'DIRECT_SOURCES', {}):
                    self.assertEqual(main.main(), 1)
                self.assertEqual(yaml.safe_load(Path('subscribe/clash.yml').read_text()), yaml.safe_load(CLASH))
                self.assertEqual(Path('subscribe/v2ray.txt').read_text().strip(), NODES)
            finally:
                os.chdir(previous)

    def test_merge_deduplicates_and_keeps_groups_resolvable(self):
        first = CLASH + 'proxy-groups:\n - {name: select, type: select, proxies: [test, DIRECT, auto]}\n - {name: auto, type: url-test, proxies: [test]}\nrules: ["MATCH,select"]\n'
        duplicate = CLASH.replace('name: test', 'name: another')
        collision = CLASH.replace('example.com', 'other.example')
        data = yaml.safe_load(main._merge_clash([('original', first), ('NoMoreWalls', duplicate), ('ProxyPool', collision)]))
        names = [p['name'] for p in data['proxies']]
        self.assertEqual(names, ['test', 'ProxyPool | test'])
        self.assertEqual(data['proxy-groups'][0]['proxies'], ['test', 'DIRECT', 'ermao.net | auto', 'ProxyPool | test'])
        self.assertEqual(data['proxy-groups'][0]['name'], 'ermao.net | select')
        self.assertEqual(data['proxy-groups'][1]['name'], 'ermao.net | auto')
        self.assertEqual(data['rules'], ['MATCH,ermao.net | select'])

    def test_direct_sources_work_when_rss_fails(self):
        session = Mock()
        session.get.side_effect = [main.requests.ConnectionError,
            Mock(status_code=200, text=CLASH), Mock(status_code=200, text=NODES),
            Mock(status_code=200, text=CLASH), Mock(status_code=200, text=base64.b64encode(NODES.encode()).decode())]
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd(); os.chdir(directory)
            try:
                with patch.object(main, '_build_session', return_value=nullcontext(session)):
                    self.assertEqual(main.main(), 0)
                self.assertEqual(len(yaml.safe_load(Path('subscribe/clash.yml').read_text())['proxies']), 1)
                self.assertEqual(Path('subscribe/v2ray.txt').read_text().strip(), NODES)
                self.assertEqual(session.get.call_count, 5)
            finally:
                os.chdir(previous)

    def test_github_empty_history_and_http_errors(self):
        from get_projaec_info import get_project_info
        with patch('get_projaec_info.requests.get', return_value=Mock(json=Mock(return_value=[]))):
            self.assertEqual(get_project_info('u','p','star','stargazers','starred_at')['num_list'], [0])
        with patch('get_projaec_info.requests.get', return_value=Mock(raise_for_status=Mock(side_effect=main.requests.HTTPError))):
            with self.assertRaises(main.requests.HTTPError):
                get_project_info('u','p','star','stargazers','starred_at')
