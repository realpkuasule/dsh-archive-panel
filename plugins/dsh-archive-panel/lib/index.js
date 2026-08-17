// dsh-archive-panel — host half (profile bundle plugin).
//
// 1) unarchiveSession instance patch on the workspace registry:
//    mirrors the shipped archiveSession write path exactly
//    (enqueueOperation -> requireState -> setState), so persistence via the
//    workspace domain global, the in-memory state swap, and the apiProxy
//    poller that emits host/archived-sessions-changed all stay consistent —
//    the official session list re-shows a session right after unarchive.
// 2) HTTP endpoints for the browser half:
//      POST /archived/unarchive  { ids: string[] }
//      POST /archived/preview    { ids: string[] }
//    Guarded by a per-process random token injected into the index page
//    (window.__DSH_ARCHIVED_TOKEN__), so only pages served by this process
//    can call; external LAN clients cannot guess the token.
import { randomBytes } from 'node:crypto'

const TOKEN = randomBytes(16).toString('hex')

function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = ''
    req.on('data', (chunk) => {
      body += chunk
      if (body.length > 1e6) {
        req.destroy()
        reject(new Error('payload too large'))
      }
    })
    req.on('end', () => {
      if (body.length === 0) return resolve({})
      try {
        resolve(JSON.parse(body))
      } catch (error) {
        reject(error)
      }
    })
    req.on('error', reject)
  })
}

function send(res, status, payload) {
  const text = JSON.stringify(payload)
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
  res.end(text)
}

function idsOf(value) {
  return Array.isArray(value) ? value.filter((x) => typeof x === 'string') : []
}

export default {
  name: "dsh-archive-panel",
  inject: ['workspaceRegistry', 'webServer'],
  apply(ctx) {
    const registry = ctx.workspaceRegistry

    // ---- instance patch: unarchiveSession ----
    const own = Object.prototype.hasOwnProperty.call(registry, 'unarchiveSession')
    const inherited = typeof registry.unarchiveSession === 'function'
    if (!own && !inherited) {
      Object.defineProperty(registry, 'unarchiveSession', {
        configurable: true,
        writable: true,
        enumerable: false,
        value: async function unarchiveSession(sessionId) {
          return this.enqueueOperation(async () => {
            const state = this.requireState()
            if (!state.archivedSessionIds.includes(sessionId)) return
            await this.setState({
              ...state,
              archivedSessionIds: state.archivedSessionIds.filter((id) => id !== sessionId),
            })
          })
        },
      })
    }
    ctx.effect(() => () => {
      if (!own && !inherited && typeof registry.unarchiveSession === 'function') {
        delete registry.unarchiveSession
      }
    })

    // ---- inject the per-process token into every index page ----
    const tapIndex = ctx.webServer.tapIndex((html) =>
      html.replace('</head>', `<script>window.__DSH_ARCHIVED_TOKEN__=${JSON.stringify(TOKEN)}</script></head>`),
    )
    ctx.effect(tapIndex)

    const guard = (req, res, next) => {
      if (req.headers['x-archived-token'] !== TOKEN) {
        send(res, 403, { ok: false, error: 'forbidden' })
        return
      }
      next()
    }

    // ---- POST /archived/unarchive ----
    ctx.webServer.register({
      kind: 'exact',
      path: '/archived/unarchive',
      handler: async (req, res) => {
        if (req.method !== 'POST') {
          send(res, 405, { ok: false, error: 'method not allowed' })
          return
        }
        guard(req, res, async () => {
          try {
            const body = await readJson(req)
            const ids = idsOf(body && body.ids)
            for (const id of ids) {
              try {
                await registry.unarchiveSession(id)
              } catch (error) {
                console.error('[dsh-archive-panel] unarchive failed for', id, error)
              }
            }
            send(res, 200, { ok: true, archivedSessionIds: [...registry.archivedSessionIds] })
          } catch (error) {
            send(res, 400, { ok: false, error: String((error && error.message) || error) })
          }
        })
      },
    })

    // ---- POST /archived/preview ----
    ctx.webServer.register({
      kind: 'exact',
      path: '/archived/preview',
      handler: async (req, res) => {
        if (req.method !== 'POST') {
          send(res, 405, { ok: false, error: 'method not allowed' })
          return
        }
        guard(req, res, async () => {
          try {
            const body = await readJson(req)
            const ids = idsOf(body && body.ids)
            const query = ctx.get('sessionQuery')
            const items = []
            for (const id of ids) {
              let preview = ''
              let updatedAt = 0
              if (query !== undefined) {
                try {
                  const docs = await query.filterEvents(id, [])
                  for (let i = docs.length - 1; i >= 0; i--) {
                    const doc = docs[i]
                    if (typeof doc.time === 'number' && doc.time > updatedAt) updatedAt = doc.time
                    if (preview === '' && doc.text && (doc.type === 'user/message' || doc.type === 'assistant/message')) {
                      preview = String(doc.text).replace(/\s+/g, ' ').trim().slice(0, 120)
                    }
                  }
                } catch (error) {
                  console.error('[dsh-archive-panel] preview failed for', id, error)
                }
              }
              items.push({ id, preview, updatedAt })
            }
            send(res, 200, { ok: true, items })
          } catch (error) {
            send(res, 400, { ok: false, error: String((error && error.message) || error) })
          }
        })
      },
    })
  },
}
