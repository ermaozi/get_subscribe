import base64
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml
import check_nodes


class HealthTests(unittest.TestCase):
    def test_endpoints_and_udp_are_not_misclassified(self):
        vmess = base64.b64encode(json.dumps({'add': 'example.com', 'port': '443'}).encode()).decode()
        ss = base64.b64encode(b'aes-256-gcm:password@example.com:8443').decode()
        ssr = base64.urlsafe_b64encode(b'example.com:443:origin:aes-256-cfb:plain:cGFzcw/?x=y').decode()
        for node, expected in [({'type': 'ss', 'server': 'example.com', 'port': 443}, ('example.com',443)),
                ('vmess://' + vmess, ('example.com',443)), ('ss://' + ss, ('example.com',8443)),
                ('ssr://' + ssr, ('example.com',443)), ('vless://id@[2606:4700:4700::1111]:443', ('2606:4700:4700::1111',443)),
                ('hysteria2://password@example.com:443',None),
                ({'type':'tuic', 'server':'example.com', 'port':443},None),
                ('vless://id@example.com:443?type=quic',None), ('vmess://invalid',None)]:
            with self.subTest(node=node):
                self.assertEqual(check_nodes.endpoint(node), expected)

    def test_private_addresses_are_not_connected(self):
        with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]), patch.object(socket, 'socket') as connect:
            self.assertEqual(check_nodes.tcp_status(('local.example',443)), 'untested')
            connect.assert_not_called()
        with patch.object(socket, 'getaddrinfo', side_effect=socket.gaierror):
            self.assertEqual(check_nodes.tcp_status(('bad.example',443)), 'unreachable')

    def test_tcp_success_and_timeout(self):
        resolved = [(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))]
        with patch.object(socket, 'getaddrinfo', return_value=resolved), patch.object(socket, 'socket') as factory:
            self.assertEqual(check_nodes.tcp_status(('public.example',443)), 'reachable')
            factory.return_value.__enter__.return_value.connect.assert_called_once_with(('1.1.1.1',443))
            factory.return_value.__enter__.return_value.connect.side_effect = socket.timeout
            self.assertEqual(check_nodes.tcp_status(('public.example',443)), 'unreachable')

    def test_classification_is_complete_and_keeps_original(self):
        config = {'proxies': [
            {'name':'up','type':'ss','server':'up.example','port':443},
            {'name':'down','type':'ss','server':'down.example','port':443},
            {'name':'udp','type':'tuic','server':'udp.example','port':443}],
            'proxy-groups':[{'name':'select','type':'select','proxies':['up','down','udp']}]}
        uris = 'ss://YWVzOnBhc3M=@up.example:443\ntuic://id@udp.example:443\n'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);source=yaml.safe_dump(config)
            (path/'clash.yml').write_text(source);(path/'v2ray.txt').write_text(uris)
            with patch.object(check_nodes, 'tcp_status', side_effect=lambda a: 'reachable' if a[0]=='up.example' else 'unreachable') as probe:
                result=check_nodes.classify(path)
            self.assertEqual(probe.call_count,2)
            self.assertEqual(result['counts']['untested'],{'clash':1,'v2ray':1})
            self.assertEqual((path/'clash.yml').read_text(),source)
            self.assertEqual((path/'v2ray.txt').read_text(),uris)
            for status,name in [('reachable','up'),('unreachable','down'),('untested','udp')]:
                output=yaml.safe_load((path/status/'clash.yml').read_text())
                self.assertEqual(output['proxy-groups'][0]['proxies'],[name])
