import { Activity, Network, Shield, Users } from 'lucide-react'
import { Link } from 'react-router-dom'
import MetricCard from '@/components/noc/MetricCard'
import { Skeleton } from '@/components/ui/skeleton'
import type { Awg2HealthResponse, Awg2MonitoringResponse } from '@/types'
import { formatAwg2IfacePeers, formatAwg2OnlineCount } from './utils'

interface Awg2OverviewCardsProps {
  health: Awg2HealthResponse | null
  monitoring: Awg2MonitoringResponse | null
  loading?: boolean
}

export default function Awg2OverviewCards({
  health,
  monitoring,
  loading = false,
}: Awg2OverviewCardsProps) {
  const missingCount = health?.missing_components?.length ?? 0
  const total = monitoring?.clients.length ?? 0

  if (loading && !health) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3 rounded-xl border bg-card/80 p-3 shadow-sm">
            <Skeleton className="h-10 w-10 rounded-xl" />
            <div className="min-w-0 flex-1 space-y-2">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-5 w-24" />
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        label="Состояние"
        value={!health ? '—' : health.installed ? 'Готов' : 'Не готов'}
        sub={missingCount > 0 ? `${missingCount} компонентов` : 'нативный awg'}
        icon={Activity}
        accent={health?.installed ? 'green' : health ? 'amber' : 'default'}
      />
      <Link
        to="/"
        className="block transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        title="Открыть Клиенты"
      >
        <MetricCard
          label="Онлайн / всего"
          value={`${formatAwg2OnlineCount(monitoring)} / ${total || '—'}`}
          sub="Клиенты → AmneziaWG 2.0"
          icon={Users}
          accent="cyan"
        />
      </Link>
      <MetricCard
        label="AntiZapret"
        value={formatAwg2IfacePeers(monitoring, 'antizapret')}
        sub="antizapret2 (10.29.9.0/24)"
        icon={Shield}
        accent="amber"
      />
      <MetricCard
        label="VPN"
        value={formatAwg2IfacePeers(monitoring, 'vpn')}
        sub="vpn2"
        icon={Network}
      />
    </div>
  )
}
