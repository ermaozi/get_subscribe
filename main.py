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


def _pick_url(urls, mode):
    if mode == "v2ray":
        for suffix in (".txt", ".json"):
            for url in urls:
                if url.lower().endswith(suffix):
                    return url
    if mode == "clash":
        for suffix in (".yaml", ".yml"):
            for url in urls:
                if url.lower().endswith(suffix):
                    return url
    return ""


def _pick_urls(urls, mode):
    matched = []
    suffixes = (".txt", ".json") if mode == "v2ray" else (".yaml", ".yml")
    for suffix in suffixes:
        for url in urls:
            if url.lower().endswith(suffix) and url not in matched:
                matched.append(url)
    return matched


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


def _download_candidates(session, urls):
    if not urls:
        return None, None
    for url in urls:
        try:
            req = session.get(url, verify=False, timeout=20)
        except requests.RequestException as e:
            write_log(f"请求失败：{url} - {e}", "WARN")
            continue
        if req.status_code in ok_code:
            return req, url
        write_log(f"请求失败：{url} - {req.status_code}", "WARN")
    return None, urls[0]

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

    urls, decoded_summary = _extract_urls(summary)

    v2ray_url = _pick_url(urls, "v2ray")
    clash_url = _pick_url(urls, "clash")
    v2ray_candidates = _pick_urls(urls, "v2ray")
    clash_candidates = _pick_urls(urls, "clash")

    # 兼容旧页面结构，通用提取失败时再尝试历史规则
    if not v2ray_url:
        v2ray_list = re.findall(r">V2Ray/XRay -&gt; (.*?)</span>", summary)
        if not v2ray_list:
            v2ray_list = re.findall(r">V2Ray/XRay -> (.*?)</span>", decoded_summary)
        if any(v2ray_list):
            v2ray_url = v2ray_list[-1].replace('amp;', '')
            if v2ray_url not in v2ray_candidates:
                v2ray_candidates.append(v2ray_url)

    if not clash_url:
        clash_list = re.findall(r">clash -&gt; (.*?)</span>", summary)
        if not clash_list:
            clash_list = re.findall(r">clash -> (.*?)</span>", decoded_summary)
        if any(clash_list) and not clash_list[-1].startswith("订阅地址生成失败"):
            clash_url = clash_list[-1].replace('amp;', '')
            if clash_url not in clash_candidates:
                clash_candidates.append(clash_url)

    # 获取普通订阅链接
    if v2ray_url:
        v2ray_req, used_v2ray_url = _download_candidates(session, v2ray_candidates)
        if not v2ray_req:
            cache_file = dirs + '/v2ray.txt'
            if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
                update_list.append("v2ray: cache")
                write_log(f"获取 v2ray 订阅失败，已保留本地缓存：{used_v2ray_url}", "WARN")
            else:
                write_log(f"获取 v2ray 订阅失败：{used_v2ray_url}", "WARN")
        else:
            update_list.append(f"v2ray: {v2ray_req.status_code}")
            with open(dirs + '/v2ray.txt', 'w', encoding="utf-8") as f:
                f.write(v2ray_req.text)

    # 获取clash订阅链接
    if clash_url and not clash_url.startswith("订阅地址生成失败"):
        clash_req, used_clash_url = _download_candidates(session, clash_candidates)
        if not clash_req:
            cache_file = dirs + '/clash.yml'
            if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
                update_list.append("clash: cache")
                write_log(f"获取 clash 订阅失败，已保留本地缓存：{used_clash_url}", "WARN")
            else:
                write_log(f"获取 clash 订阅失败：{used_clash_url}", "WARN")
        else:
            update_list.append(f"clash: {clash_req.status_code}")
            with open(dirs + '/clash.yml', 'w', encoding="utf-8") as f:
                clash_content = clash_req.content.decode("utf-8")
                f.write(clash_content)
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
