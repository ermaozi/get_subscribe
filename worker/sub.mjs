const REPOSITORY = 'ermaozi/get_subscribe';
const PREFIX = 'files/subscribe/';
const CACHE_SECONDS = 300;
const MAX_FILE_BYTES = 4 * 1024 * 1024;
const FILES = ['clash.yml', 'v2ray.txt', ...['reachable', 'unreachable', 'untested']
  .flatMap(group => [`${group}/clash.yml`, `${group}/v2ray.txt`]), 'health.json'];

function json(body, status = 200) {
  return Response.json(body, { status, headers: { 'Cache-Control': 'no-store' } });
}

function fileForPath(path) {
  if (path === '/sub/health.json') return 'health.json';
  const match = path.match(/^\/sub\/(?:(reachable|unreachable|untested)\/)?(clash|v2ray)\/ermao\.net$/);
  return match ? `${match[1] ? match[1] + '/' : ''}${match[2] === 'clash' ? 'clash.yml' : 'v2ray.txt'}` : null;
}

function contentType(file) {
  if (file.endsWith('.json')) return 'application/json; charset=utf-8';
  return file.endsWith('.yml') ? 'text/yaml; charset=utf-8' : 'text/plain; charset=utf-8';
}

async function fetchText(url, limit = MAX_FILE_BYTES) {
  const response = await fetch(url, {
    headers: { 'User-Agent': 'ermao-subscription-sync' },
    redirect: 'manual', signal: AbortSignal.timeout(20000),
    cf: { cacheTtl: 0, cacheEverything: false },
  });
  if (response.status !== 200) {
    await response.body?.cancel();
    throw new Error(`Upstream HTTP ${response.status}`);
  }
  // 只读取有明确大小上限的文本，防止异常响应耗尽 Worker 内存。
  const reader = response.body?.getReader();
  if (!reader) return { text: '', bytes: 0 };
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let text = '', bytes = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      bytes += chunk.value.byteLength;
      if (bytes > limit) throw new Error('Upstream file exceeds size limit');
      text += decoder.decode(chunk.value, { stream: true });
    }
    text += decoder.decode();
    return { text, bytes };
  } finally {
    await reader.cancel();
  }
}

function validate(file, text) {
  if (file === 'health.json') {
    const report = JSON.parse(text);
    if (!Number.isFinite(Date.parse(report.checked_at)) || report.method !== 'TCP connect') {
      throw new Error('Invalid health report');
    }
    for (const group of ['reachable', 'unreachable', 'untested']) {
      for (const kind of ['clash', 'v2ray']) {
        const count = report.counts?.[group]?.[kind];
        if (!Number.isInteger(count) || count < 0) throw new Error('Invalid health counts');
      }
    }
    return;
  }
  // YAML 结构已由主仓库回归测试和采集程序检查；此处防止错误页覆盖订阅。
  if (file.endsWith('.yml')) {
    if (!/^proxies:\s/m.test(text)) throw new Error('Invalid Clash artifact');
  } else {
    const nodes = text.trim() ? text.trim().split(/\r?\n/) : [];
    if ((!file.includes('/') && !nodes.length) || nodes.some(node =>
      !/^(vmess|vless|trojan|ss|ssr|hysteria2?|hy2|tuic|anytls|mieru|https?|socks[45]?):\/\/\S+$/.test(node))) {
      throw new Error('Invalid V2Ray artifact');
    }
  }
}

export async function refreshAll(env) {
  if (!env.SUBSCRIBE_BUCKET) throw new Error('Missing subscription storage');
  const ref = await fetchText(`https://api.github.com/repos/${REPOSITORY}/git/ref/heads/main`, 16384);
  const commit = JSON.parse(ref.text).object?.sha;
  if (!/^[a-f0-9]{40}$/.test(commit)) throw new Error('Invalid upstream commit');
  const previous = await env.SUBSCRIBE_BUCKET.head(PREFIX + 'health.json');
  if (previous?.customMetadata?.commit === commit) {
    return { ok: true, commit, files: FILES.length, unchanged: true, updatedAt: previous.customMetadata.updatedAt };
  }
  const downloads = [];
  let totalBytes = 0;
  // 同一次同步固定到同一提交，所有文件验证完成后才写入 R2。
  for (const file of FILES) {
    const url = `https://raw.githubusercontent.com/${REPOSITORY}/${commit}/subscribe/${file}`;
    const body = await fetchText(url);
    totalBytes += body.bytes;
    if (totalBytes > 16 * 1024 * 1024) throw new Error('Subscription batch exceeds size limit');
    validate(file, body.text);
    downloads.push({ file, url, text: body.text });
  }
  const updatedAt = new Date().toISOString();
  for (const { file, url, text } of downloads) {
    await env.SUBSCRIBE_BUCKET.put(PREFIX + file, text, {
      httpMetadata: { contentType: contentType(file), cacheControl: `public, max-age=0, s-maxage=${CACHE_SECONDS}` },
      customMetadata: { commit, source: url, updatedAt },
    });
  }
  console.log(JSON.stringify({ event: 'subscription_sync', commit, files: downloads.length, bytes: totalBytes }));
  return { ok: true, commit, files: downloads.length, updatedAt };
}

async function serve(request, env, file) {
  let object = await env.SUBSCRIBE_BUCKET.get(PREFIX + file);
  if (!object) {
    await refreshAll(env);
    object = await env.SUBSCRIBE_BUCKET.get(PREFIX + file);
  }
  if (!object) throw new Error('Subscription not available');
  const headers = new Headers({
    'Content-Type': contentType(file), 'Cache-Control': `public, max-age=0, s-maxage=${CACHE_SECONDS}`,
    'X-Source': 'r2', 'ETag': object.httpEtag,
  });
  if (file !== 'health.json') {
    const group = file.includes('/') ? file.split('/')[0] : 'all';
    const label = { all: '免费订阅', reachable: 'TCP 可连接', unreachable: '连接失败', untested: '未检测' }[group];
    const title = `ermao.net · ${label}`;
    headers.set('Profile-Title', 'base64:' + btoa(String.fromCharCode(...new TextEncoder().encode(title))));
    headers.set('Profile-Web-Page-Url', 'https://www.ermao.net/');
    headers.set('Support-Url', 'https://www.ermao.net/');
    headers.set('Content-Disposition', `attachment; filename="ermao.net-${file.replaceAll('/', '-')}"`);
  }
  const metadata = object.customMetadata || {};
  if (/^[a-f0-9]{40}$/.test(metadata.commit)) headers.set('X-Git-Commit', metadata.commit);
  if (metadata.updatedAt) headers.set('X-Updated-At', metadata.updatedAt.replace(/[\r\n]/g, ''));
  if (request.headers.get('If-None-Match') === object.httpEtag) return new Response(null, { status: 304, headers });
  return new Response(request.method === 'HEAD' ? null : object.body, { headers });
}

export default {
  async fetch(request, env) {
    if (!['GET', 'HEAD'].includes(request.method)) return new Response('Method Not Allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
    const path = new URL(request.url).pathname.toLowerCase();
    if (['/', '/health', '/sub/health'].includes(path)) {
      return json({ ok: true, service: 'subscribe-worker', version: 'github-sync-v1', endpoints: [
        '/sub/clash/ermao.net', '/sub/v2ray/ermao.net', '/sub/reachable/clash/ermao.net', '/sub/health.json',
      ] });
    }
    const refresh = path.match(/^\/sub\/refresh\/(all|clash|v2ray)$/);
    const file = refresh && refresh[1] !== 'all' ? `${refresh[1] === 'clash' ? 'clash.yml' : 'v2ray.txt'}` : fileForPath(path);
    if (!refresh && !file) return new Response('Not Found', { status: 404 });
    try {
      if (refresh) {
        const result = await refreshAll(env);
        if (refresh[1] === 'all') return json(result);
      }
      return await serve(request, env, file);
    } catch (error) {
      console.error(JSON.stringify({ event: 'subscription_error', message: String(error.message || error) }));
      return json({ ok: false, error: 'Subscription refresh or storage unavailable' }, 502);
    }
  },
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(refreshAll(env));
  },
};
