import base64
import os
import re
import smtplib
import sys
import time
import html
import xml.etree.ElementTree as ET
from email.mime.text import MIMEText
from email.utils import formataddr

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

requests.packages.urllib3.disable_warnings()


ok_code = [200, 201, 202, 203, 204, 205, 206]

# 邮箱域名过滤列表
blackhole_list = ["cnr.cn", "cyberpolice.cn", "gov.cn", "samr.gov.cn", "12321.cn"
                  "miit.gov.cn", "chinatcc.gov.cn"]


def write_log(content, level="INFO"):

    date_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()))
    update_log = f"[{date_str}] [{level}] {content}\n"
    print(update_log)
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


_NODE_SCHEME_RE = re.compile(r"(?:vmess|vless|trojan|ss|ssr|hysteria2?|tuic)://")


def _b64decode(text):
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return ""
    try:
        # binascii.Error 是 ValueError 的子类，统一捕获即可
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4))
    except ValueError:
        return ""
    return raw.decode("utf-8", "ignore")


def _detect_kind(text):
    """根据下载内容判断订阅类型：'clash' / 'v2ray'，无法识别返回 None。"""
    sample = text.strip()
    if not sample:
        return None
    # clash 配置为 YAML，包含 proxies/proxy-groups 字段
    if re.search(r"^(?:proxies|proxy-groups)\s*:", sample, re.MULTILINE):
        return "clash"
    # v2ray 订阅为节点 URI 列表，可能是明文或 base64 编码
    if _NODE_SCHEME_RE.search(sample):
        return "v2ray"
    decoded = _b64decode(sample)
    if decoded and _NODE_SCHEME_RE.search(decoded):
        return "v2ray"
    return None


def _download_with_retry(urls):
    if not urls:
        return None, None
    for url in urls:
        for _ in range(3):
            try:
                req = requests.request(
                    "GET",
                    url,
                    verify=False,
                    timeout=20,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
            except requests.RequestException as e:
                print(f"请求 {url} 失败: {e}")
                continue
            if req.status_code in ok_code:
                return req, url
    return None, urls[0]


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
            req = session.get(url, verify=False, timeout=20)
        except requests.RequestException as e:
            write_log(f"请求失败：{url} - {e}", "WARN")
            continue
        if req.status_code not in ok_code:
            write_log(f"请求失败：{url} - {req.status_code}", "WARN")
            continue
        kind = _detect_kind(req.text)
        if kind and kind not in found:
            found[kind] = (req, url)
            write_log(f"识别到 {kind} 订阅：{url}", "INFO")
    return found

def get_subscribe_url():
    dirs = './subscribe'
    if not os.path.exists(dirs):
        os.makedirs(dirs)
    log_dir = "./log"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    update_list = []
    session = _build_session()
    try:
        rss_req = session.get(
            'https://www.cfmem.com/feeds/posts/default?alt=rss',
            timeout=20,
        )
    except requests.RequestException as ex:
        write_log(f"更新失败！拉取 RSS 异常: {ex}", "ERROR")
        return

    if rss_req.status_code not in ok_code:
        write_log(f"更新失败！无法拉取原网站内容 - {rss_req.status_code}", "ERROR")
        return

    try:
        root = ET.fromstring(rss_req.text)
    except ET.ParseError as ex:
        write_log(f"更新失败！RSS 解析失败: {ex}", "ERROR")
        return

    item = root.find("./channel/item")
    if item is None:
        write_log("更新失败！RSS 中未找到可用条目", "ERROR")
        return

    summary = item.findtext("description")
    if not summary:
        write_log("暂时没有可用的订阅更新", "WARN")
        return

    urls, _ = _extract_urls(summary)

    # 链接已无固定后缀，需下载内容后再判断是 v2ray 还是 clash
    classified = _classify_subscriptions(session, urls)

    # 获取普通订阅链接
    v2ray_entry = classified.get("v2ray")
    if v2ray_entry:
        v2ray_req, _ = v2ray_entry
        update_list.append(f"v2ray: {v2ray_req.status_code}")
        with open(dirs + '/v2ray.txt', 'w', encoding="utf-8") as f:
            f.write(v2ray_req.text)
    else:
        cache_file = dirs + '/v2ray.txt'
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
            update_list.append("v2ray: cache")
            write_log("未获取到 v2ray 订阅，已保留本地缓存", "WARN")
        else:
            write_log("未获取到 v2ray 订阅", "WARN")

    # 获取clash订阅链接
    clash_entry = classified.get("clash")
    if clash_entry:
        clash_req, _ = clash_entry
        update_list.append(f"clash: {clash_req.status_code}")
        with open(dirs + '/clash.yml', 'w', encoding="utf-8") as f:
            f.write(clash_req.content.decode("utf-8"))
    else:
        cache_file = dirs + '/clash.yml'
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
            update_list.append("clash: cache")
            write_log("未获取到 clash 订阅，已保留本地缓存", "WARN")
        else:
            write_log("未获取到 clash 订阅", "WARN")
    if update_list:
        file_pat = re.compile(r"v2ray\.txt|clash\.yml")
        if file_pat.search(os.popen("git status").read()):
            write_log(f"更新成功：{update_list}", "INFO")
        else:
            write_log(f"订阅暂未更新", "WARN")
    else:
        write_log(f"未能获取新的更新内容", "WARN")


def main():
    get_subscribe_url()


# 主函数入口
if __name__ == '__main__':
    main()
