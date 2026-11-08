import { useState } from 'react'
import { RefreshCw, RotateCw } from 'lucide-react'
import {
  auditNodeDualStack,
  rotateNodeIpv6,
  setNodeEgressMode,
  setNodeTransportMode,
} from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { Node } from '@/types'

function familyLabel(status?: string, latency?: number | null) {
  const text = status === 'online' ? 'online' : status === 'offline' ? 'offline' : 'unknown'
  return `${text}${latency != null ? ` · ${latency} ms` : ''}`
}

export default function NodeDualStackControls({
  node,
  onUpdated,
}: {
  node: Node
  onUpdated: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isVpn = (node.node_kind ?? 'vpn') === 'vpn'
  const isLinkedRelay = node.node_kind === 'proxy' && node.linked_vpn_node_id != null
  const ipv6Only = node.ipv4_probe_status === 'offline' && node.ipv6_probe_status === 'online'

  const run = async (action: string, operation: () => Promise<unknown>) => {
    setBusy(action)
    setError(null)
    try {
      await operation()
      await onUpdated()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Операция не выполнена')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-border/60 bg-muted/10 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium">Dual-Stack</span>
        <Badge variant={node.ipv4_probe_status === 'online' ? 'default' : 'outline'}>
          IPv4: {familyLabel(node.ipv4_probe_status, node.ipv4_latency_ms)}
        </Badge>
        <Badge variant={node.ipv6_probe_status === 'online' ? 'default' : 'outline'}>
          IPv6: {familyLabel(node.ipv6_probe_status, node.ipv6_latency_ms)}
        </Badge>
        {ipv6Only && <Badge variant="secondary">IPv4 Blocked / Active via IPv6</Badge>}
        {node.reverse_state && node.reverse_state !== 'disabled' && (
          <Badge variant="secondary">Reverse: {node.reverse_state}</Badge>
        )}
      </div>
      <div className="grid gap-1 text-xs sm:grid-cols-2">
        <span className="break-all font-mono">IPv4: {node.ipv4_address ?? '—'}</span>
        <span className="break-all font-mono">
          IPv6: {node.ipv6_address ?? '—'}
          {node.ipv6_subnet_prefix ? ` /${node.ipv6_subnet_prefix}` : ''}
        </span>
      </div>
      <div className={`grid gap-3 ${isVpn ? 'sm:grid-cols-2' : ''}`}>
        <div className="space-y-1">
          <label htmlFor={`transport-${node.id}`} className="text-xs font-medium">Транспорт</label>
          <select
            id={`transport-${node.id}`}
            name="transport_mode"
            className="min-h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            value={node.transport_mode ?? 'auto'}
            disabled={busy !== null}
            onChange={(event) => void run('transport', () => setNodeTransportMode(node.id, event.target.value))}
          >
            <option value="auto">Auto (Dual-Stack)</option>
            <option value="force_ipv6" disabled={!node.has_ipv6}>Force IPv6</option>
            <option value="force_ipv4" disabled={!node.has_ipv4}>Force IPv4</option>
            {isLinkedRelay && <option value="reverse">Reverse Tunnel (Зарубеж → РФ)</option>}
          </select>
        </div>
        {isVpn && (
          <div className="space-y-1">
            <label htmlFor={`egress-${node.id}`} className="text-xs font-medium">Выходной шлюз</label>
            <select
              id={`egress-${node.id}`}
              name="egress_mode"
              className="min-h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={node.egress_mode ?? 'native'}
              disabled={busy !== null}
              onChange={(event) => void run('egress', () => setNodeEgressMode(node.id, event.target.value))}
            >
              <option value="native">Native Dual-Stack</option>
              <option value="proton">Proton VPN (v4+v6)</option>
              <option value="warp">Cloudflare WARP (v4+v6)</option>
              <option value="ipv4_only">IPv4 Only</option>
              <option value="ipv6_prefer">IPv6 Prefer</option>
            </select>
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy !== null}
          onClick={() => void run('audit', () => auditNodeDualStack(node.id))}
        >
          <RefreshCw className={busy === 'audit' ? 'animate-spin' : ''} />
          Проверить IPv4/IPv6
        </Button>
        {isVpn && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy !== null || !node.ipv6_rotation_supported}
            onClick={() => void run('rotate', () => rotateNodeIpv6(node.id))}
          >
            <RotateCw className={busy === 'rotate' ? 'animate-spin' : ''} />
            Ротировать IPv6 адрес (из /64)
          </Button>
        )}
      </div>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
