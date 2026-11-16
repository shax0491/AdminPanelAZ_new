import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, RotateCw } from 'lucide-react'
import {
  auditNodeDualStack,
  getNodeIpv4Egress,
  rotateNodeIpv6,
  setNodeDualStackEnabled,
  setNodeEgressMode,
  setNodeIpv4Egress,
  setNodeTransportMode,
} from '@/api/client'
import { getNodesIpv6Summary, type NodeIpv6Summary } from '@/api/ipv6'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { NodeIpv4Egress } from '@/api/nodes'
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
  // Фактическое состояние узла (ENABLE_IPV6, выход IPv6), а не значение из БД: null = ещё не получено / узел не ответил
  const [live, setLive] = useState<NodeIpv6Summary | null>(null)
  const reload = useCallback(async () => {
    try {
      const all = await getNodesIpv6Summary()
      const item = all[String(node.id)]
      setLive(item && !item.error ? item : null)
    } catch {
      setLive(null)
    }
  }, [node.id])
  useEffect(() => {
    if (isVpn) void reload()
  }, [isVpn, reload])
  // IPv4-выход = существующий механизм WARP (провайдер + режимы): direct/proton выбираются, прочее только показывается
  const [ipv4Egress, setIpv4Egress] = useState<NodeIpv4Egress>('unknown')
  const reloadIpv4 = useCallback(async () => {
    try {
      setIpv4Egress((await getNodeIpv4Egress(node.id)).ipv4_egress)
    } catch {
      setIpv4Egress('unknown')
    }
  }, [node.id])
  useEffect(() => {
    if (isVpn) void reloadIpv4()
  }, [isVpn, reloadIpv4])
  const dualStack: 'on' | 'off' | 'unknown' = live == null || live.enabled == null ? 'unknown' : live.enabled ? 'on' : 'off'
  const liveEgress = live?.active_egress || live?.egress || node.egress_mode
  const ipv6Only = node.ipv4_probe_status === 'offline' && node.ipv6_probe_status === 'online'

  const run = async (action: string, operation: () => Promise<unknown>) => {
    setBusy(action)
    setError(null)
    try {
      await operation()
      await onUpdated()
      await reload()
      await reloadIpv4()
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
        {node.dual_stack_telemetry === false && <Badge variant="outline">Нет данных (аудит не выполнялся)</Badge>}
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
      <div className={`grid gap-3 ${isVpn ? 'sm:grid-cols-2 lg:grid-cols-4' : ''}`}>
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
            <label htmlFor={`ipv4egress-${node.id}`} className="text-xs font-medium">IPv4: выход</label>
            <select
              id={`ipv4egress-${node.id}`}
              name="ipv4_egress"
              className="min-h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={ipv4Egress}
              disabled={busy !== null || ipv4Egress === 'unknown' || ipv4Egress === 'pending'}
              onChange={(event) => {
                if (!window.confirm('Применение перезапустит туннели узла на несколько секунд. Продолжить?')) return
                void run('ipv4egress', () => setNodeIpv4Egress(node.id, event.target.value as 'direct' | 'proton'))
              }}
            >
              {ipv4Egress === 'unknown' && <option value="unknown">Неизвестно (узел не ответил)</option>}
              {ipv4Egress === 'pending' && <option value="pending">Записано, не применено (вкладка WARP)</option>}
              {ipv4Egress === 'custom' && <option value="custom">Особый (настроен на вкладке WARP)</option>}
              <option value="direct">Прямой</option>
              <option value="proton">Proton</option>
            </select>
            <p className="text-xs text-muted-foreground">Меняет только выход сервера для IPv4, клиентские конфиги не меняются.</p>
          </div>
        )}
        {isVpn && (
          <div className="space-y-1">
            <label htmlFor={`dualstack-${node.id}`} className="text-xs font-medium">Dual-Stack сервера</label>
            <select
              id={`dualstack-${node.id}`}
              name="dual_stack_enabled"
              className="min-h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={dualStack}
              disabled={busy !== null || dualStack === 'unknown'}
              onChange={(event) => void run('dualstack', () => setNodeDualStackEnabled(node.id, event.target.value === 'on'))}
            >
              {dualStack === 'unknown' && <option value="unknown">Неизвестно (узел не ответил)</option>}
              <option value="on">Вкл</option>
              <option value="off">Выкл</option>
            </select>
            <p className="text-xs text-muted-foreground">
              Клиентский стек (IPv4 или Dual-Stack) выбирается при создании конфига. Выключение не ломает существующие конфиги: Dual-Stack-клиенты работают по IPv4.
            </p>
          </div>
        )}
        {isVpn && (
          <div className="space-y-1">
            <label htmlFor={`egress-${node.id}`} className="text-xs font-medium">Выход IPv6</label>
            <select
              id={`egress-${node.id}`}
              name="egress_mode"
              className="min-h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={liveEgress === 'proton' || liveEgress === 'warp' ? liveEgress : 'native'}
              disabled={busy !== null || dualStack !== 'on'}
              onChange={(event) => void run('egress', () => setNodeEgressMode(node.id, event.target.value))}
            >
              <option value="native">Native</option>
              <option value="proton">Proton</option>
              {liveEgress === 'warp' && <option value="warp">Cloudflare WARP</option>}
            </select>
            <p className="text-xs text-muted-foreground">
              {dualStack === 'on'
                ? 'Меняет только выход сервера, клиентские конфиги не меняются. IPv6 через Proton требует IPv6 на интерфейсе туннеля.'
                : 'Недоступно: включите Dual-Stack сервера.'}
            </p>
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
