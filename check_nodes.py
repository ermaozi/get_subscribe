"""按 TCP 握手结果分类订阅；UDP/QUIC 和无法解析的格式不作失效判断。"""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import socket
import time
from urllib.parse import parse_qs, urlsplit

import yaml

TIMEOUT = 3
WORKERS = 24
STATUSES = ('reachable', 'unreachable', 'untested')
UDP_TYPES = {'hysteria', 'hysteria2', 'hy2', 'tuic', 'wireguard', 'mieru'}


def _decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)).decode('utf-8')


def endpoint(node):
    """返回 TCP 主机和端口；不支持或使用 UDP 的节点返回 None。"""
    try:
        if isinstance(node, dict):
            if node.get('type') in UDP_TYPES or node.get('network') in {'quic', 'kcp'}:
                return None
            host, port = node.get('server'), node.get('port')
        else:
            scheme, body = node.split('://', 1)
            if scheme in UDP_TYPES:
                return None
            if scheme == 'vmess':
                data = json.loads(_decode(body.split('#', 1)[0]))
                if not isinstance(data, dict) or data.get('net') in {'quic', 'kcp'}:
                    return None
                host, port = data.get('add'), data.get('port')
            elif scheme == 'ssr':
                host, port, *_ = _decode(body.split('#', 1)[0]).split('/?', 1)[0].rsplit(':', 5)
            else:
                if scheme == 'ss' and '@' not in body:
                    node = 'ss://' + _decode(body.split('#', 1)[0])
                parsed = urlsplit(node)
                if any(v in {'quic', 'kcp'} for v in parse_qs(parsed.query).get('type', [])):
                    return None
                host, port = parsed.hostname, parsed.port
        port = int(port)
        if not isinstance(host, str) or not host or not 1 <= port <= 65535:
            return None
        return host.strip('[]').lower().rstrip('.'), port
    except (ValueError, TypeError, KeyError, UnicodeError):
        return None


def tcp_status(address):
    # ponytail: 只测 TCP 握手；需要验证认证和代理出口时再引入代理内核。
    host, port = address
    try:
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return 'unreachable'
    public = []
    for family, socktype, proto, _, sockaddr in resolved:
        # 来源不可信，不探测宿主机内网、回环或云实例元数据地址。
        if ipaddress.ip_address(sockaddr[0]).is_global:
            public.append((family, socktype, proto, sockaddr))
    if not public:
        return 'untested'
    deadline = time.monotonic() + TIMEOUT
    for family, socktype, proto, sockaddr in public:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with socket.socket(family, socktype, proto) as connection:
                connection.settimeout(remaining)
                connection.connect(sockaddr)
            return 'reachable'
        except OSError:
            continue
    return 'unreachable'


def classify(directory='subscribe'):
    directory = Path(directory)
    config = yaml.safe_load((directory / 'clash.yml').read_text(encoding='utf-8'))
    uris = (directory / 'v2ray.txt').read_text(encoding='utf-8').splitlines()
    proxies = config['proxies']
    proxy_addresses = [endpoint(proxy) for proxy in proxies]
    uri_addresses = [endpoint(uri) for uri in uris]
    addresses = list(dict.fromkeys(a for a in proxy_addresses + uri_addresses if a is not None))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = dict(zip(addresses, pool.map(tcp_status, addresses)))
    summary = {'checked_at': datetime.now(timezone.utc).isoformat(), 'method': 'TCP connect',
               'timeout_seconds': TIMEOUT, 'unique_endpoints': len(addresses), 'counts': {}}
    all_names = {proxy['name'] for proxy in proxies}
    for status in STATUSES:
        selected = [p for p, address in zip(proxies, proxy_addresses) if results.get(address, 'untested') == status]
        selected_uris = [uri for uri, address in zip(uris, uri_addresses) if results.get(address, 'untested') == status]
        removed = all_names - {proxy['name'] for proxy in selected}
        filtered = {**config, 'proxies': selected}
        groups = []
        for group in config.get('proxy-groups', []):
            group = dict(group)
            if 'proxies' in group:
                group['proxies'] = [name for name in group['proxies'] if name not in removed] or ['REJECT']
            groups.append(group)
        if 'proxy-groups' in config:
            filtered['proxy-groups'] = groups
        target = directory / status
        target.mkdir(exist_ok=True)
        (target / 'clash.yml').write_text(yaml.safe_dump(filtered, allow_unicode=True, sort_keys=False), encoding='utf-8')
        (target / 'v2ray.txt').write_text('\n'.join(selected_uris) + ('\n' if selected_uris else ''), encoding='utf-8')
        summary['counts'][status] = {'clash': len(selected), 'v2ray': len(selected_uris)}
    (directory / 'health.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == '__main__':
    classify()
