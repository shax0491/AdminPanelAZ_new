import { useCallback, useEffect, useState } from 'react'
import { Loader2, RefreshCw, Send } from 'lucide-react'
import { getMtproxyStatus } from '@/api/mtproxy'
import SettingsAlert from '@/components/settings/SettingsAlert'
import PageSectionHeader from '@/components/shared/PageSectionHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { formatDateTime } from '@/lib/datetime'
import { formatBytes } from '@/lib/trafficFormat'
import { cn } from '@/lib/utils'
import type { MtproxyAvailabilityCheck, MtproxyNodeStatus, MtproxyUser } from '@/types'

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

function QuotaCell({ user }: { user: MtproxyUser }) {
  if (!user.quota_bytes) return <span className="text-muted-foreground">без квоты</span>
  const pct = user.quota_pct ?? 0
  return (
    <div className="min-w-[90px] space-y-0.5" title={`${formatBytes(user.session_bytes)} из ${formatBytes(user.quota_bytes)} с последнего запуска прокси`}>
      <div className="h-1.5 rounded bg-muted">
        <div
          className={cn('h-1.5 rounded', pct >= 100 ? 'bg-destructive' : pct >= 90 ? 'bg-amber-500' : 'bg-emerald-500')}
          style={{ width: `${Math.min(100, pct)}%` }}
        />
      </div>
      <div className="text-[11px] text-muted-foreground">
        {pct}% · {formatBytes(user.quota_bytes)}
      </div>
    </div>
  )
}

/** Пользователи (секреты) узла: кто сейчас подключён, трафик, лимиты и квота. */
function UsersTable({ users }: { users: MtproxyUser[] }) {
  const sorted = [...users].sort((a, b) => b.connections - a.connections || b.total_bytes - a.total_bytes)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-muted-foreground">
          <tr className="border-b text-left">
            <th className="py-1 pr-2 font-medium">Пользователь</th>
            <th className="py-1 pr-2 font-medium" title="Соединений сейчас / лимит">Соед.</th>
            <th className="py-1 pr-2 font-medium" title="IP сейчас / лимит">IP</th>
            <th className="py-1 pr-2 font-medium">Трафик</th>
            <th className="py-1 font-medium">Квота</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((user) => (
            <tr key={user.label} className={cn('border-b last:border-0', !user.enabled && 'opacity-50')}>
              <td className="py-1 pr-2">
                <span className={cn('mr-1 inline-block h-1.5 w-1.5 rounded-full', user.connections ? 'bg-emerald-500' : 'bg-muted-foreground/40')} />
                {user.label}
                {!user.enabled && <span className="text-muted-foreground"> (выкл.)</span>}
              </td>
              <td className="py-1 pr-2 tabular-nums">
                {user.connections}/{user.max_conns || '∞'}
              </td>
              <td className="py-1 pr-2 tabular-nums">
                {user.unique_ips}/{user.max_ips || '∞'}
              </td>
              <td className="py-1 pr-2 tabular-nums">{formatBytes(user.total_bytes)}</td>
              <td className="py-1">
                <QuotaCell user={user} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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

      <div className="grid gap-4 lg:grid-cols-2">
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
                    <dd title="Порт в ссылках tg://proxy; если он другой, на сервере стоит переадресация на порт прокси">
                      {node.public_port && node.public_port !== node.port
                        ? `${node.public_port} (слушает ${node.port})`
                        : (node.port ?? '—')}
                    </dd>
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
                {node.installed && (node.users?.length ?? 0) > 0 && <UsersTable users={node.users ?? []} />}
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
