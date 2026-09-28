import base64
import os
import re
import sys
import time
import html
import json
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry



ok_code = [200]
DIRECT_SOURCES = {
    "NoMoreWalls": [
        "https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.meta.yml",
        "https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.txt",
    ],
    "ProxyPool": [
        "https://raw.githubusercontent.com/snakem982/proxypool/main/source/clash-meta-2.yaml",
        "https://raw.githubusercontent.com/snakem982/proxypool/main/source/v2ray-2.txt",
    ],
}

def write_log(content, level="INFO"):

    date_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))
    update_log = f"[{date_str}] [{level}] {content}\n"
    print(update_log, end="")
    Path("log").mkdir(exist_ok=True)
    with open(f'./log/{time.strftime("%Y-%m", time.localtime(time.time()))}-update.log', 'a', encoding="utf-8") as f:
        f.write(update_log)


def _extract_urls(summary):
    decoded = html.unescape(summary)
    urls = []
    for raw_url in re.findall(r"https?://[^\s\"'<>]+", decoded):
        url = raw_url.strip().rstrip('.,;)')
        if url not in urls:
            urls.append(url)
    return urls, decoded


_NODE_SCHEME_RE = re.compile(r"(?:vmess|vless|trojan|ss|ssr|hysteria2?|hy2|tuic|anytls|mieru|https?|socks[45]?)://")


def _b64decode(text):
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return ""
    try:
        # binascii.Error 是 ValueError 的子类，统一捕获即可
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=True)
    except ValueError:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _detect_kind(text):
    """根据下载内容判断订阅类型：'clash' / 'v2ray'，无法识别返回 None。"""
    sample = text.strip()
    if not sample:
        return None
    if re.search(r"^(?:proxies|proxy-groups)\s*:", sample, re.MULTILINE):
        try:
            data = yaml.safe_load(sample)
        except yaml.YAMLError:
            return None
        proxies = data.get("proxies") if isinstance(data, dict) else None
        if isinstance(proxies, list) and proxies and all(
            isinstance(proxy, dict) and isinstance(proxy.get("name"), str) and proxy["name"]
            and proxy.get("type") and proxy.get("server") for proxy in proxies
        ):
            return "clash"
        return None
    decoded = sample if _NODE_SCHEME_RE.match(sample) else _b64decode(sample)
    nodes = [node.strip() for node in decoded.splitlines() if node.strip()]
    if nodes and all(_NODE_SCHEME_RE.match(node) and not re.search(r"\s", node) for node in nodes):
        try:
            for node in nodes:
                if node.startswith(("http://", "https://", "socks://", "socks4://", "socks5://")):
                    parsed = urlsplit(node)
                    if not parsed.hostname or not parsed.port:
                        return None
        except ValueError:
            return None
        return "v2ray"
    return None


def _build_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    proxy = os.environ.get("SUBSCRIBE_PROXY", "").strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})

    session.headers.update({"User-Agent": "Mozilla/5.0"})
    return session


def _classify_subscriptions(session, urls):
    """逐个下载候选链接并按内容判断类型，返回 {'v2ray': (req, url), 'clash': (req, url)}。"""
    found = {}
    for url in urls:
        if "v2ray" in found and "clash" in found:
            break
        try:
            req = session.get(url, timeout=20)
        except requests.RequestException as e:
            write_log(f"候选订阅请求失败：{type(e).__name__}", "WARN")
            continue
        if req.status_code not in ok_code:
            write_log(f"候选订阅 HTTP {req.status_code}", "WARN")
            continue
        kind = _detect_kind(req.text)
        if kind and kind not in found:
            found[kind] = (req, url)
            write_log(f"识别到有效的 {kind} 订阅", "INFO")
    return found

def _merge_clash(sources):
    # 保留第一个可用来源的规则，将所有来源节点加入已有的节点选择组。
    config = yaml.safe_load(sources[0][1])
    proxies, identities, names, renamed = [], {}, set(), {}
    names.update(group["name"] for group in config.get("proxy-groups", []))
    names.update(["DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"])
    for index, (source, content) in enumerate(sources):
        for proxy in yaml.safe_load(content)["proxies"]:
            identity = json.dumps({k: v for k, v in proxy.items() if k != "name"}, sort_keys=True, default=str)
            if identity not in identities:
                name = proxy["name"]
                if name in names:
                    name = f"{source} | {name}"
                original = name
                suffix = 2
                while name in names:
                    name = f"{original} ({suffix})"
                    suffix += 1
                identities[identity] = name
                names.add(name)
                proxies.append({**proxy, "name": name})
            if index == 0:
                renamed[proxy["name"]] = identities[identity]
    for group in config.get("proxy-groups", []):
        members = group.get("proxies", [])
        if any(member in renamed for member in members):
            group["proxies"] = list(dict.fromkeys(
                [renamed.get(member, member) for member in members] + [p["name"] for p in proxies]
            ))
    config["proxies"] = proxies
    # 仅给前两个选择组加入站点标识，同时保持规则和组间引用一致。
    groups = config.get("proxy-groups", [])
    branded = {}
    used_names = {p["name"] for p in proxies} | {g["name"] for g in groups}
    for group in [g for g in groups if g.get("type") in {"select", "url-test"}][:2]:
        old = group["name"]
        if "ermao.net" in old:
            continue
        name = f"ermao.net | {old}"
        while name in used_names:
            name += " ·"
        branded[old] = name
        used_names.add(name)
        group["name"] = name
    for group in groups:
        if "proxies" in group:
            group["proxies"] = [branded.get(name, name) for name in group["proxies"]]
    if "rules" in config:
        config["rules"] = [",".join(branded.get(part, part) for part in rule.split(","))
                           for rule in config["rules"]]
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


def get_subscribe_url():
    collected = {"clash": [], "v2ray": []}
    with _build_session() as session:
        try:
            rss = session.get('https://www.cfmem.com/feeds/posts/default?alt=rss', timeout=20)
            rss.raise_for_status()
            root = ET.fromstring(rss.content)
            for item in root.findall("./channel/item"):
                urls, _ = _extract_urls(item.findtext("description") or "")
                found = _classify_subscriptions(session, urls)
                if found:
                    for kind, (response, _) in found.items():
                        collected[kind].append(("长风分享", response.text))
                    break
        except (requests.RequestException, ET.ParseError) as exc:
            write_log(f"长风分享采集失败：{type(exc).__name__}，继续其他来源", "WARN")
        for source, urls in DIRECT_SOURCES.items():
            found = _classify_subscriptions(session, urls)
            for kind, (response, _) in found.items():
                collected[kind].append((source, response.text))
            write_log(f"{source}：获取 {', '.join(found) or '无有效订阅'}")

    if not all(collected.values()):
        write_log("未获取到完整的两种订阅，保留旧文件", "ERROR")
        return False
    nodes = []
    for _, content in collected["v2ray"]:
        text = content.strip() if _NODE_SCHEME_RE.match(content.strip()) else _b64decode(content)
        nodes.extend(node.strip() for node in text.splitlines() if node.strip())
    # 保持主项目原有的明文 URI 输出，兼容现有订阅入口。
    outputs = {"clash.yml": _merge_clash(collected["clash"]),
               "v2ray.txt": "\n".join(dict.fromkeys(nodes)) + "\n"}
    for filename, content in outputs.items():
        expected = "clash" if filename.endswith(".yml") else "v2ray"
        if _detect_kind(content) != expected:
            raise ValueError(f"合并后的 {expected} 订阅格式无效")
    Path("subscribe").mkdir(exist_ok=True)
    changed = []
    for filename, content in outputs.items():
        target = Path("subscribe") / filename
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
            changed.append(filename)
    write_log("已更新：" + ", ".join(changed) if changed else "订阅内容未变化")
    return True


def main():
    return 0 if get_subscribe_url() else 1


# 主函数入口
if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        write_log(f"采集或写入失败：{type(exc).__name__}", "ERROR")
        sys.exit(1)
