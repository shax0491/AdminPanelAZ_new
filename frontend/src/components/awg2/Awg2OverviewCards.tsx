import { Activity, Network, Shield, Users } from 'lucide-react'
import { Link } from 'react-router-dom'
import MetricCard from '@/components/noc/MetricCard'
import { Skeleton } from '@/components/ui/skeleton'
import type { Awg2HealthResponse, Awg2MonitoringResponse, Awg3HealthResponse } from '@/types'
import { formatAwg2IfacePeers, formatAwg2OnlineCount } from './utils'
import { AWG_VARIANTS, type AwgVariant } from './variants'

interface Awg2OverviewCardsProps {
  health: Awg2HealthResponse | Awg3HealthResponse | null
  monitoring: Awg2MonitoringResponse | null
  loading?: boolean
  variant?: AwgVariant
}

export default function Awg2OverviewCards({
  health,
  monitoring,
  loading = false,
  variant = 'awg2',
}: Awg2OverviewCardsProps) {
  const config = AWG_VARIANTS[variant]
  const isAwg3 = variant === 'awg3'
  const awg2Health = isAwg3 ? null : (health as Awg2HealthResponse | null)
  const awg3Health = isAwg3 ? (health as Awg3HealthResponse | null) : null
  const installed = isAwg3
    ? Boolean(awg3Health?.tools_present && awg3Health?.userspace_present)
    : Boolean(awg2Health?.installed)
  const missingCount = awg2Health?.missing_components?.length ?? 0
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
        value={!health ? '—' : installed ? 'Готов' : 'Не готов'}
        sub={isAwg3 ? 'awg1 · amneziawg-go' : missingCount > 0 ? `${missingCount} компонентов` : 'нативный awg'}
        icon={Activity}
        accent={installed ? 'green' : health ? 'amber' : 'default'}
      />
      <Link
        to="/"
        className="block transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        title="Открыть Клиенты"
      >
        <MetricCard
          label="Онлайн / всего"
          value={`${formatAwg2OnlineCount(monitoring)} / ${total || '—'}`}
          sub={config.clientsHint}
          icon={Users}
          accent="cyan"
        />
      </Link>
      <MetricCard
        label="AntiZapret"
        value={formatAwg2IfacePeers(monitoring, config.antizapretIface)}
        sub={config.antizapretSub}
        icon={Shield}
        accent="amber"
      />
      <MetricCard
        label="VPN"
        value={formatAwg2IfacePeers(monitoring, config.vpnIface)}
        sub={config.vpnSub}
        icon={Network}
      />
    </div>
  )
}
