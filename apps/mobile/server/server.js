const http = require('http');
const https = require('https');
const net = require('net');
const tls = require('tls');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const port = Number(process.env.PORT || 4173);
// Deployed backend by default; SECO_API_ORIGIN=http://localhost:8000 for a local API.
const apiOrigin = new URL(process.env.SECO_API_ORIGIN || 'https://seco-backend-api.onrender.com');
const apiSecure = apiOrigin.protocol === 'https:';
const apiPort = Number(apiOrigin.port || (apiSecure ? 443 : 80));
const types = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.json': 'application/json; charset=utf-8'
};

/** /api/v1/* -> backend, same origin for the browser (no CORS). */
function proxyApi(req, res) {
  const requestUrl = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  const headers = { ...req.headers, host: apiOrigin.host };
  delete headers.origin;
  delete headers.referer;
  const upstream = (apiSecure ? https : http).request({
    hostname: apiOrigin.hostname,
    port: apiPort,
    path: `${requestUrl.pathname}${requestUrl.search}`,
    method: req.method,
    headers
  }, (upstreamResponse) => {
    res.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
    upstreamResponse.pipe(res);
  });
  upstream.on('error', () => {
    if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ detail: 'Backend API proxy unavailable' }));
  });
  req.pipe(upstream);
}

const server = http.createServer((req, res) => {
  const requested = decodeURIComponent(req.url.split('?')[0]);
  if (requested === '/api/v1' || requested.startsWith('/api/v1/')) return proxyApi(req, res);

  const relative = requested === '/' ? 'public/index.html' : requested.replace(/^\/+/, '');
  const candidates = requested === '/'
    ? [path.join(root, 'public/index.html')]
    : [path.join(root, relative), path.join(root, 'public', relative)];
  const filePath = candidates.find((candidate) => {
    const resolved = path.resolve(candidate);
    return resolved.startsWith(root) && fs.existsSync(resolved) && !fs.statSync(resolved).isDirectory();
  });
  if (!filePath) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Not found');
    return;
  }
  res.writeHead(200, {
    'Content-Type': types[path.extname(filePath)] || 'application/octet-stream',
    'Cache-Control': 'no-cache'
  });
  fs.createReadStream(filePath).pipe(res);
});

/** /ws (realtime) -> backend WebSocket: forward the upgrade request, then pipe both ways. */
server.on('upgrade', (req, socket, head) => {
  if (!req.url.startsWith('/ws')) return socket.destroy();
  const upstream = apiSecure
    ? tls.connect({ host: apiOrigin.hostname, port: apiPort, servername: apiOrigin.hostname })
    : net.connect({ host: apiOrigin.hostname, port: apiPort });
  upstream.once(apiSecure ? 'secureConnect' : 'connect', () => {
    const headers = { ...req.headers, host: apiOrigin.host };
    delete headers.origin;
    const lines = Object.entries(headers).map(([key, value]) => `${key}: ${value}`);
    upstream.write(`GET ${req.url} HTTP/1.1\r\n${lines.join('\r\n')}\r\n\r\n`);
    if (head?.length) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  });
  const close = () => { upstream.destroy(); socket.destroy(); };
  upstream.on('error', close);
  socket.on('error', close);
});

server.listen(port, '0.0.0.0', () => {
  console.log(`SECO Mobile running at http://localhost:${port} (API ${apiOrigin.origin})`);
});
