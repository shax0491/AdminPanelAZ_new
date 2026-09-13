import type { Awg2HealthResponse, Awg2MonitoringResponse, Node } from '@/types'

export function awg2StatusMeta(health: Awg2HealthResponse | null) {
  if (!health) {
    return { label: 'Нет данных', variant: 'secondary' as const, dot: 'bg-muted-foreground' }
  }
  if (!health.installed) {
    return { label: 'Не установлен', variant: 'warning' as const, dot: 'bg-amber-500' }
  }
  return { label: 'Установлен', variant: 'success' as const, dot: 'bg-emerald-500' }
}

export function formatAwg2ClientCount(monitoring: Awg2MonitoringResponse | null): string {
  if (!monitoring) return '—'
  return String(monitoring.clients.length)
}

export function formatAwg2OnlineCount(monitoring: Awg2MonitoringResponse | null): string {
  if (!monitoring) return '—'
  return String(monitoring.clients.filter((c) => c.online).length)
}

export function formatAwg2IfacePeers(
  monitoring: Awg2MonitoringResponse | null,
  ifaceName: string,
): string {
  const iface = monitoring?.ifaces.find((i) => i.name === ifaceName)
  if (!iface) return '—'
  const count = iface.peer_count ?? 0
  return `${count} ${count === 1 ? 'клиент' : 'клиентов'}`
}

export function formatAwg2NodeLabel(health: Awg2HealthResponse | null, activeNode: Node | null): string {
  const name = health?.node_name ?? activeNode?.name
  const host = health?.node_host ?? activeNode?.host
  if (name && host) return `${name} (${host})`
  if (name) return name
  if (host) return host
  return 'активном узле панели'
}
