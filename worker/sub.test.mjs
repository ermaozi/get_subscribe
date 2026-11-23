import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { refreshAll } from './sub.mjs';

const commit = 'a'.repeat(40);
const clash = 'proxies:\n  - {name: test, type: ss, server: example.com, port: 443}\n';
const report = JSON.stringify({ checked_at: '2026-09-08T00:00:00Z', method: 'TCP connect', counts: {
  reachable: { clash: 1, v2ray: 1 }, unreachable: { clash: 0, v2ray: 0 }, untested: { clash: 0, v2ray: 0 },
} });

function storage() {
  const objects = new Map();
  return { objects, SUBSCRIBE_BUCKET: {
    async head(key) { return objects.get(key) || null; },
    async get(key) {
      const item = objects.get(key);
      return item ? { ...item, body: new Response(item.text).body, httpEtag: '"etag"' } : null;
    },
    async put(key, text, options) { objects.set(key, { text, ...options }); },
  } };
}

function upstream(url) {
  if (url.startsWith('https://api.github.com/')) return Response.json({ object: { sha: commit } });
  assert.ok(url.startsWith(`https://raw.githubusercontent.com/ermaozi/get_subscribe/${commit}/subscribe/`));
  if (url.endsWith('health.json')) return new Response(report);
  const empty = /\/(unreachable|untested)\//.test(url);
  return new Response(url.endsWith('.yml') ? (empty ? 'proxies: []\n' : clash) : (empty ? '' : 'vmess://test\n'));
}

test('syncs one commit, all routes serve R2, unchanged revision does not download again', async t => {
  const env = storage();
  const fetch = t.mock.method(globalThis, 'fetch', async url => upstream(url));
  const result = await refreshAll(env);
  assert.equal(result.files, 9);
  assert.equal(env.objects.size, 9);
  assert.equal(fetch.mock.callCount(), 10);
  for (const group of ['', 'reachable/', 'unreachable/', 'untested/']) {
    for (const kind of ['clash', 'v2ray']) {
      const response = await worker.fetch(new Request(`https://www.ermao.net/sub/${group}${kind}/ermao.net`), env);
      assert.equal(response.status, 200);
      assert.equal(response.headers.get('X-Git-Commit'), commit);
      const title = Buffer.from(response.headers.get('Profile-Title').slice(7), 'base64').toString('utf-8');
      assert.match(title, /^ermao.net · /);
      assert.equal(response.headers.get('Profile-Web-Page-Url'), 'https://www.ermao.net/');
      assert.match(response.headers.get('Content-Disposition'), /ermao\.net-/);
      await response.text();
    }
  }
  const health = await worker.fetch(new Request('https://www.ermao.net/sub/health.json'), env);
  assert.deepEqual(await health.json(), JSON.parse(report));
  assert.equal((await refreshAll(env)).unchanged, true);
  assert.equal(fetch.mock.callCount(), 11);
});

test('invalid upstream file leaves existing objects unchanged; reads work during outage', async t => {
  const env = storage();
  env.objects.set('files/subscribe/clash.yml', { text: clash, customMetadata: {} });
  t.mock.method(globalThis, 'fetch', async url => url.endsWith('health.json') ? new Response('<html>error</html>') : upstream(url));
  await assert.rejects(refreshAll(env));
  assert.equal(env.objects.size, 1);
  assert.equal(env.objects.get('files/subscribe/clash.yml').text, clash);
  const response = await worker.fetch(new Request('https://www.ermao.net/sub/clash/ermao.net'), env);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), clash);
});

test('rejects invalid commits, excessive responses, unknown paths and unsupported methods', async t => {
  const env = storage();
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ object: { sha: '../other' } }));
  await assert.rejects(refreshAll(env), /Invalid upstream commit/);
  fetch.mock.mockImplementation(async url => url.startsWith('https://api.github.com/')
    ? upstream(url) : new Response('x'.repeat(4 * 1024 * 1024 + 1)));
  await assert.rejects(refreshAll(env), /size limit/);
  assert.equal(env.objects.size, 0);
  assert.equal((await worker.fetch(new Request('https://example.com/sub/nope'), env)).status, 404);
  assert.equal((await worker.fetch(new Request('https://example.com/sub/refresh/all', { method: 'POST' }), env)).status, 405);
});

test('preserves refresh endpoints, HEAD, ETag and scheduled handler', async t => {
  const env = storage();
  t.mock.method(globalThis, 'fetch', async url => upstream(url));
  const refresh = await worker.fetch(new Request('https://example.com/sub/refresh/all'), env);
  assert.equal(refresh.status, 200);
  assert.equal((await refresh.json()).commit, commit);
  const head = await worker.fetch(new Request('https://example.com/sub/clash/ermao.net', { method: 'HEAD' }), env);
  assert.equal(await head.text(), '');
  const cached = await worker.fetch(new Request('https://example.com/sub/clash/ermao.net', { headers: { 'If-None-Match': '"etag"' } }), env);
  assert.equal(cached.status, 304);
  const pending = [];
  await worker.scheduled({}, env, { waitUntil(promise) { pending.push(promise); } });
  await Promise.all(pending);
  const subscription = await worker.fetch(new Request('https://example.com/sub/refresh/v2ray'), env);
  assert.equal(await subscription.text(), 'vmess://test\n');
});
