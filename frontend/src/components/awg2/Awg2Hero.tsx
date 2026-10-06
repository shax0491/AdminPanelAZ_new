import { RefreshCw, Shield } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import PageSectionHeader from '@/components/shared/PageSectionHeader'
import NodeSelector, { NodeBadge } from '@/components/NodeSelector'
import { useAuth } from '@/context/AuthContext'
import { useNode } from '@/context/NodeContext'
import type { Awg2HealthResponse, Awg3HealthResponse } from '@/types'
import { cn } from '@/lib/utils'
import { awg2StatusMeta, awg3StatusMeta } from './utils'
import { AWG_VARIANTS, type AwgVariant } from './variants'

interface Awg2HeroProps {
  health: Awg2HealthResponse | Awg3HealthResponse | null
  loading: boolean
  nodeLabel: string
  onRefresh: () => void
  variant?: AwgVariant
}

export default function Awg2Hero({ health, loading, nodeLabel, onRefresh, variant = 'awg2' }: Awg2HeroProps) {
  const { activeNode } = useNode()
  const { user } = useAuth()
  const config = AWG_VARIANTS[variant]
  const status = variant === 'awg3' ? awg3StatusMeta(health as Awg3HealthResponse | null) : awg2StatusMeta(health as Awg2HealthResponse | null)

  return (
    <PageSectionHeader
      icon={Shield}
      title={config.title}
      docsHref={config.docsHref}
      titleAddon={
        <>
          <NodeBadge name={activeNode?.name} status={activeNode?.status} />
          {loading ? (
            <Skeleton className="h-5 w-24 rounded-full" />
          ) : (
            <Badge variant={status.variant} className="gap-1.5">
              <span className={cn('h-2 w-2 rounded-full', status.dot)} />
              {status.label}
            </Badge>
          )}
        </>
      }
      description={
        <>
          {config.nodeNote} на узле{' '}
          <strong className="font-medium text-foreground">{nodeLabel}</strong>
          {activeNode?.is_local ? ' (локальный controller)' : activeNode ? ' (удалённый node agent)' : ''}.
          Управление клиентами — в разделе Клиенты; здесь только статус и живой мониторинг пиров.
        </>
      }
      actions={
        <>
          {user?.role === 'admin' && <NodeSelector compact />}
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={loading}>
            <RefreshCw className={cn('mr-1.5 h-4 w-4', loading && 'animate-spin')} />
            Обновить
          </Button>
        </>
      }
    />
  )
}
