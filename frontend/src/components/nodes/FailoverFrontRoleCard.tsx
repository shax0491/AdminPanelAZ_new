import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { listFailoverPools } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import type { FailoverPool, Node } from '@/types'

/**
 * Separate from ProxyNodePanel on purpose — that panel is entirely about the
 * old proxy.sh / RU-proxy DESTINATION mechanism, and shows scary "proxy.sh не
 * установлен" messaging for a proxy node that was never meant to run proxy.sh
 * at all (a dnat_front-only front doesn't need it). This card shows the
 * *other* thing a Proxy node can be used for: the front of an Автопереключение
 * pool. Renders nothing if this node isn't a front for any pool.
 */
export default function FailoverFrontRoleCard({ node }: { node: Node }) {
  const [pools, setPools] = useState<FailoverPool[] | null>(null)

  useEffect(() => {
    let cancelled = false
    listFailoverPools()
      .then((data) => {
        if (!cancelled) setPools(data)
      })
      .catch(() => {
        if (!cancelled) setPools([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  const asFront = (pools ?? []).filter((p) => p.front_node_id === node.id)
  if (asFront.length === 0) return null

  return (
    <div className="space-y-2 rounded-lg border border-sky-500/25 bg-sky-500/5 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <RefreshCw size={14} className="text-sky-600 dark:text-sky-400" />
        <p className="text-sm font-medium">Фронт автопереключения</p>
        <Badge variant="outline" className="text-[10px]">
          {asFront.length === 1 ? '1 пул' : `${asFront.length} пула(ов)`}
        </Badge>
      </div>
      <div className="space-y-1.5">
        {asFront.map((pool) => {
          const activeMember = pool.members.find((m) => m.id === pool.active_member_id)
          return (
            <div
              key={pool.id}
              className="flex flex-wrap items-center gap-2 rounded-md border bg-card/40 px-2.5 py-1.5 text-xs"
            >
              <span className="font-medium">{pool.name}</span>
              <span className="text-muted-foreground">
                активен: {activeMember ? activeMember.label || activeMember.node_name : '—'}
              </span>
              {pool.last_switch_error && (
                <span className="text-destructive">· {pool.last_switch_error}</span>
              )}
            </div>
          )
        })}
      </div>
      <Link
        to="/failover"
        className="inline-flex items-center gap-1 text-xs font-medium underline underline-offset-2"
      >
        Открыть «Автопереключение»
        <ExternalLink size={12} />
      </Link>
    </div>
  )
}
