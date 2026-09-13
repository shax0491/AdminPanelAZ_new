import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import EmptyState from '@/components/ui/EmptyState'
import { Users } from 'lucide-react'
import { formatBytes } from '@/components/warper/utils'
import type { Awg2MonitoringResponse } from '@/types'

interface Awg2ClientsTableProps {
  monitoring: Awg2MonitoringResponse | null
}

function formatHandshakeAge(seconds?: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds} с назад`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин назад`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч назад`
  return `${Math.floor(seconds / 86400)} дн назад`
}

export default function Awg2ClientsTable({ monitoring }: Awg2ClientsTableProps) {
  const clients = monitoring?.clients ?? []

  if (clients.length === 0) {
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
    <div className="overflow-x-auto rounded-xl border bg-card/50">
      <table className="w-full text-sm">
        <thead className="border-b bg-muted/40 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2 text-left font-medium">Клиент</th>
            <th className="px-3 py-2 text-left font-medium">Интерфейс</th>
            <th className="px-3 py-2 text-left font-medium">Статус</th>
            <th className="px-3 py-2 text-left font-medium">Хендшейк</th>
            <th className="px-3 py-2 text-right font-medium">RX</th>
            <th className="px-3 py-2 text-right font-medium">TX</th>
          </tr>
        </thead>
        <tbody>
          {clients.map((client, index) => (
            <tr key={`${client.name}-${client.iface ?? index}`} className="border-b last:border-0">
              <td className="px-3 py-2 font-medium">{client.name}</td>
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
  )
}
