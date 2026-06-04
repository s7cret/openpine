import { createServer } from 'vite'

const server = await createServer({
  root: process.cwd(),
  server: { host: '0.0.0.0', port: 1888 }
})
await server.listen()
server.printUrls()
console.log('OPENPINE_UI_READY')
