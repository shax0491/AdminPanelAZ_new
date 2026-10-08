import { useCallback, useEffect, useState } from 'react'
import { Loader2, RefreshCw, Send } from 'lucide-react'
import { getMtproxyStatus } from '@/api/mtproxy'
import SettingsAlert from '@/components/settings/SettingsAlert'
import PageSectionHeader from '@/components/shared/PageSectionHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { formatDateTime } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import type { MtproxyAvailabilityCheck, MtproxyNodeStatus } from '@/types'

function pctVariant(pct: number | null | undefined) {
  if (pct == null) return 'secondary' as const
  return pct >= 80 ? ('success' as const) : pct >= 50 ? ('warning' as const) : ('destructive' as const)
}

function StateBadge({ node }: { node: MtproxyNodeStatus }) {
  if (!node.node_online) return <Badge variant="secondary">узел офлайн</Badge>
  if (node.installed === null) return <Badge variant="secondary">нет данных от агента</Badge>
  if (!node.installed) return <Badge variant="outline">не установлен</Badge>
  if (node.running) return <Badge variant="success">работает</Badge>
  return <Badge variant="destructive">{node.status || 'остановлен'}</Badge>
}

/** Последние проверки доступности из России: столбики по процентам, новые справа. */
function AvailabilityBars({ checks }: { checks: MtproxyAvailabilityCheck[] }) {
  if (checks.length === 0) return null
  return (
    <div className="flex h-6 items-end gap-0.5" aria-label="История проверок доступности">
      {checks.map((check, i) => {
        const pct = check.percentage ?? 0
        const probes = check.total ? ` (${check.success}/${check.total})` : ''
        return (
          <div
            key={`${check.checked_at ?? ''}-${i}`}
            title={`${formatDateTime(check.checked_at)}: ${check.percentage ?? '—'}%${probes}`}
            className={cn(
              'w-2 rounded-sm',
              pct >= 80 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-destructive',
            )}
            style={{ height: `${Math.max(15, pct)}%` }}
          />
        )
      })}
    </div>
  )
}

/** Вкладка MTProxy: MTProxyL на всех VPN-узлах в одном месте. */
export default function MtproxyPage() {
  const [nodes, setNodes] = useState<MtproxyNodeStatus[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (refresh = false) => {
    setLoading(true)
    setError(null)
    try {
      setNodes((await getMtproxyStatus(refresh)).nodes)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить состояние MTProxy')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const installed = nodes?.filter((n) => n.installed) ?? []
  const running = installed.filter((n) => n.running).length

  return (
    <div className="space-y-6">
      <PageSectionHeader
        icon={Send}
        title="MTProxy"
        description={
          <>
            MTProxyL на всех узлах: работает ли, домен маскировки, подключения и доступность из России.
            {nodes && ` Работает ${running} из ${installed.length}.`}
          </>
        }
        actions={
          <Button variant="outline" size="sm" onClick={() => void load(true)} disabled={loading}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Опросить узлы
          </Button>
        }
      />

      {error && (
        <SettingsAlert variant="danger" title="Ошибка загрузки">
          {error}
        </SettingsAlert>
      )}

      {nodes === null && !error && (
        <div className="flex justify-center py-10">
          <Loader2 size={20} className="animate-spin text-muted-foreground" />
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {nodes?.map((node) => {
          const pct = node.availability?.percentage
          return (
            <Card key={node.node_id}>
              <CardContent className="space-y-3 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{node.node_name}</span>
                  <StateBadge node={node} />
                  {node.installed && (
                    <Badge variant={pctVariant(pct)}>
                      {pct == null ? 'доступность: нет данных' : `${pct}% из России`}
                    </Badge>
                  )}
                </div>
                {node.installed && (
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
                    <dt className="text-muted-foreground">Домен</dt>
                    <dd className="truncate font-mono text-xs" title={node.domain ?? ''}>
                      {node.domain || '—'}
                    </dd>
                    <dt className="text-muted-foreground">Порт</dt>
                    <dd>{node.port ?? '—'}</dd>
                    <dt className="text-muted-foreground">Подключений</dt>
                    <dd>
                      {node.connections ?? '—'}
                      {node.unique_ips != null && (
                        <span className="text-muted-foreground"> · IP: {node.unique_ips}</span>
                      )}
                    </dd>
                    <dt className="text-muted-foreground">Версия</dt>
                    <dd>{node.version || '—'}</dd>
                    {node.availability?.checked_at && (
                      <>
                        <dt className="text-muted-foreground">Проверка</dt>
                        <dd>{formatDateTime(node.availability.checked_at)}</dd>
                      </>
                    )}
                  </dl>
                )}
                {node.installed && <AvailabilityBars checks={node.availability_recent ?? []} />}
                {node.installed === false && (
                  <p className="text-xs text-muted-foreground">
                    MTProxyL на узле нет. Команда установки — в «Настройки → Модули → MTProxy».
                  </p>
                )}
                {node.installed === null && node.node_online && (
                  <p className="text-xs text-muted-foreground">
                    Агент узла не отдаёт состояние MTProxy — обновите агент.
                  </p>
                )}
                {node.error && <p className="text-xs text-destructive">{node.error}</p>}
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
