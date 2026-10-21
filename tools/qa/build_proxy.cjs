// Exercise the overridden build downloader with real loopback HTTP traffic.
// No external proxy, model credentials, or Electron caches are modified.
const assert = require('node:assert/strict');
const http = require('node:http');
const https = require('node:https');
const net = require('node:net');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { createRequire } = require('node:module');
const { spawn, execFileSync } = require('node:child_process');
const builderRequire = createRequire(path.resolve(__dirname, '../../frontend/node_modules/app-builder-lib/package.json'));
const body = Buffer.from('GenTactix build proxy compatibility fixture\n');
const digest = createHash('sha256').update(body).digest('hex');

async function child() {
  const { downloadArtifact } = builderRequire('@electron/get');
  assert(global.GLOBAL_AGENT, 'Proxy bootstrap must not silently fail');
  const options = {
    version: '9.9.9', artifactName: 'fixture.bin', isGeneric: true,
    cacheRoot: process.env.QA_CACHE, force: true,
    checksums: { 'fixture.bin': digest },
    mirrorOptions: { resolveAssetURL: async () => process.env.QA_URL },
    downloadOptions: { retry: { limit: 0 }, timeout: { request: 5000 } },
  };
  if (process.env.QA_REJECT_TLS === '1') {
    await assert.rejects(downloadArtifact(options), /certificate|self.signed/i);
    return;
  }
  const downloaded = await downloadArtifact(options);
  assert.deepEqual(await fs.readFile(downloaded), body);
  await assert.rejects(downloadArtifact({ ...options, checksums: { 'fixture.bin': '0'.repeat(64) } }),
    /checksum/i, 'A tampered download must still be rejected');
}

async function main() {
  let hits = 0;
  const origin = http.createServer((req, res) => res.end(body));
  const proxy = http.createServer((req, res) => {
    hits++;
    const target = new URL(req.url);
    assert.equal(target.hostname, '127.0.0.1');
    const forwarded = http.get(target, upstream => upstream.pipe(res));
    forwarded.on('error', error => res.destroy(error));
  });
  proxy.on('connect', (req, socket, head) => {
    hits++;
    const [host, port] = req.url.split(':');
    assert(['127.0.0.1', 'localhost'].includes(host));
    const upstream = net.connect(Number(port), '127.0.0.1', () => {
      socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) upstream.write(head);
      socket.pipe(upstream); upstream.pipe(socket);
    });
    socket.on('error', () => upstream.destroy());
    socket.on('close', () => upstream.destroy());
    upstream.on('error', () => socket.destroy());
  });
  const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const cache = await fs.mkdtemp(path.join(os.tmpdir(), 'gentactix-build-proxy-'));
  const cert = path.join(cache, 'cert.pem');
  const key = path.join(cache, 'key.pem');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
    '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1', '-keyout', key, '-out', cert],
    { stdio: 'ignore' });
  const secureOrigin = https.createServer({ cert: await fs.readFile(cert), key: await fs.readFile(key) },
    (req, res) => res.end(body));
  try {
    await listen(origin); await listen(proxy); await listen(secureOrigin);
    for (const [secure, bypass, rejectTLS] of [[false, false, false], [false, true, false],
      [true, false, false], [true, true, false], [true, false, true]]) {
      const before = hits;
      const env = { ...process.env };
      for (const key of Object.keys(env)) if (/proxy/i.test(key)) delete env[key];
      delete env.NODE_TLS_REJECT_UNAUTHORIZED;
      delete env.NODE_EXTRA_CA_CERTS;
      Object.assign(env, {
        ELECTRON_GET_USE_PROXY: '1', GLOBAL_AGENT_HTTP_PROXY: `http://127.0.0.1:${proxy.address().port}`,
        GLOBAL_AGENT_HTTPS_PROXY: `http://127.0.0.1:${proxy.address().port}`,
        GLOBAL_AGENT_NO_PROXY: bypass ? '127.0.0.1,localhost' : '',
        QA_URL: `${secure ? 'https://localhost' : 'http://127.0.0.1'}:${(secure ? secureOrigin : origin).address().port}/fixture.bin`,
        QA_CACHE: cache, QA_REJECT_TLS: rejectTLS ? '1' : '0',
      });
      if (secure && !rejectTLS) env.NODE_EXTRA_CA_CERTS = cert;
      await new Promise((resolve, reject) => {
        const worker = spawn(process.execPath, [__filename, '--child'], { env, stdio: 'inherit' });
        const timeout = setTimeout(() => worker.kill('SIGKILL'), 15000);
        worker.once('error', error => { clearTimeout(timeout); reject(error); });
        worker.once('exit', code => { clearTimeout(timeout); code === 0 ? resolve() : reject(new Error(`Downloader exited ${code}`)); });
      });
      assert.equal(hits - before, bypass ? 0 : rejectTLS ? 1 : 2, 'Proxy / NO_PROXY routing mismatch');
      console.log(`${secure ? 'HTTPS' : 'HTTP'} ${bypass ? 'NO_PROXY bypass' : 'proxy'}: ${rejectTLS ? 'untrusted TLS rejection' : 'download and checksum rejection'} passed`);
    }
  } finally {
    origin.close(); secureOrigin.close(); proxy.close();
    await fs.rm(cache, { recursive: true, force: true });
  }
}
// Proxy bootstrap replaces process-wide agents; end the disposable worker after
// assertions so retained transport sockets cannot affect the next test case.
if (process.argv.includes('--child')) {
  child().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
} else {
  main().catch(error => { console.error(error); process.exitCode = 1; });
}
