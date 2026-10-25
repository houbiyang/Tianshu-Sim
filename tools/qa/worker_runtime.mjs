// Local Worker smoke after the scoped HTTP dependency update. No deployment.
import assert from 'node:assert/strict';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

const runtime = new Miniflare(convertV4MiniflareOptions({
  modules: true,
  compatibilityDate: '2026-10-21',
  script: `export default { async fetch(request) {
    return new Response(JSON.stringify({ method: request.method, path: new URL(request.url).pathname,
      body: await request.text() }), { headers: { 'Content-Type': 'application/json' } });
  } };`,
}));
try {
  const response = await runtime.dispatchFetch('http://qa.local/health', {
    method: 'POST', body: 'isolated-worker-http-probe',
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('Content-Type'), /application\/json/);
  assert.deepEqual(await response.json(), {
    method: 'POST', path: '/health', body: 'isolated-worker-http-probe',
  });
  console.log('Miniflare Worker HTTP request/response passed');
} finally {
  await runtime.dispose();
}
