#!/bin/bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
exec node -e "
const { createServer } = require('vite');
(async () => {
  const server = await createServer({ root: process.cwd(), server: { host: '0.0.0.0', port: 1888 } });
  await server.listen();
  server.printUrls();
  console.log('OPENPINE_UI_READY');
})().catch(e => { console.error(e); process.exit(1); });
" 2>&1
