import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import EmptyState from '@/components/ui/EmptyState'
import { Users } from 'lucide-react'
import { formatBytes } from '@/components/warper/utils'
import type { Awg2MonitoringResponse } from '@/types'

type Awg2Client = Awg2MonitoringResponse['clients'][number]
type RowClient = Awg2Client & { nodeName?: string }

interface Awg2ClientsTableProps {
  monitoring: Awg2MonitoringResponse | null
  /** When set, shows a "Узел" column and rows come pre-tagged with nodeName (combined/all-nodes view). */
  rows?: RowClient[] | null
}

type IfaceFilter = 'all' | 'antizapret' | 'vpn'
type StatusFilter = 'all' | 'online' | 'offline'

function formatHandshakeAge(seconds?: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds} с назад`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч назад`
  return `${Math.floor(seconds / 86400)} дн назад`
}

function ifaceOf(client: RowClient): IfaceFilter {
  const iface = (client.iface ?? '').toLowerCase()
  if (iface.includes('vpn')) return 'vpn'
  if (iface.includes('antizapret')) return 'antizapret'
  return 'all'
}

export default function Awg2ClientsTable({ monitoring, rows }: Awg2ClientsTableProps) {
  const [ifaceFilter, setIfaceFilter] = useState<IfaceFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  const allClients: RowClient[] = useMemo(() => rows ?? monitoring?.clients ?? [], [rows, monitoring])
  const showNodeColumn = Boolean(rows)

  const filtered = useMemo(
    () =>
      allClients.filter((c) => {
        if (ifaceFilter !== 'all' && ifaceOf(c) !== ifaceFilter) return false
        if (statusFilter === 'online' && !c.online) return false
        if (statusFilter === 'offline' && c.online) return false
        return true
      }),
    [allClients, ifaceFilter, statusFilter],
  )

  if (allClients.length === 0) {
    return (
      <div className="rounded-xl border bg-card/50 p-6">
        <EmptyState
          icon={Users}
          title="Нет клиентов AmneziaWG 2.0"
          description="Создайте клиента на странице Клиенты (галочка «AmneziaWG 2.0»)."
        />
        <div className="mt-3 flex justify-center">
          <Link to="/" className="text-sm font-medium text-foreground underline-offset-2 hover:underline">
            Перейти к Клиентам
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex overflow-hidden rounded-md border text-xs">
          {(
            [
              ['all', 'Все интерфейсы'],
              ['antizapret', 'AntiZapret'],
              ['vpn', 'VPN'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setIfaceFilter(value)}
              className={`px-2.5 py-1.5 transition-colors ${
                ifaceFilter === value ? 'bg-primary text-primary-foreground' : 'bg-card hover:bg-muted/60'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex overflow-hidden rounded-md border text-xs">
          {(
            [
              ['all', 'Все'],
              ['online', 'Онлайн'],
              ['offline', 'Офлайн'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setStatusFilter(value)}
              className={`px-2.5 py-1.5 transition-colors ${
                statusFilter === value ? 'bg-primary text-primary-foreground' : 'bg-card hover:bg-muted/60'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="ml-auto text-xs text-muted-foreground">
          {filtered.length} из {allClients.length}
        </span>
      </div>

      {filtered.length === 0 ? (
        <p className="rounded-xl border bg-card/50 p-6 text-center text-sm text-muted-foreground">
          Ничего не найдено под текущий фильтр.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border bg-card/50">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/40 text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Клиент</th>
                {showNodeColumn && <th className="px-3 py-2 text-left font-medium">Узел</th>}
                <th className="px-3 py-2 text-left font-medium">Интерфейс</th>
                <th className="px-3 py-2 text-left font-medium">Статус</th>
                <th className="px-3 py-2 text-left font-medium">Хендшейк</th>
                <th className="px-3 py-2 text-right font-medium">RX</th>
                <th className="px-3 py-2 text-right font-medium">TX</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((client, index) => (
                <tr key={`${client.nodeName ?? ''}-${client.name}-${client.iface ?? index}`} className="border-b last:border-0">
                  <td className="px-3 py-2 font-medium">{client.name}</td>
                  {showNodeColumn && (
                    <td className="px-3 py-2 text-xs text-muted-foreground">{client.nodeName ?? '—'}</td>
                  )}
                  <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{client.iface ?? '—'}</td>
                  <td className="px-3 py-2">
                    <Badge variant={client.online ? 'default' : 'secondary'} className="text-[10px]">
                      {client.online ? 'онлайн' : 'офлайн'}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{formatHandshakeAge(client.handshake_age_s)}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs">{formatBytes(client.rx ?? 0)}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs">{formatBytes(client.tx ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
