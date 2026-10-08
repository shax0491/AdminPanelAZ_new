import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Copy, Loader2, Pencil, Power, RefreshCw, RotateCcw, Send } from 'lucide-react'
import { getMtproxyStatus, runMtproxyAction, type MtproxyActionPayload } from '@/api/mtproxy'
import AutoRefreshControl from '@/components/noc/AutoRefreshControl'
import SettingsAlert from '@/components/settings/SettingsAlert'
import PageSectionHeader from '@/components/shared/PageSectionHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useNotifications } from '@/context/NotificationContext'
import { useIntervalWhenVisible } from '@/hooks/useIntervalWhenVisible'
import { formatDateTime } from '@/lib/datetime'
import { formatBytes } from '@/lib/trafficFormat'
import { cn } from '@/lib/utils'
import type { MtproxyAvailabilityCheck, MtproxyNodeStatus, MtproxyUser } from '@/types'

const REFRESH_SEC = 60
const GB = 1024 ** 3

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
    <div
      className="min-w-[90px] space-y-0.5"
      title={`${formatBytes(user.session_bytes)} из ${formatBytes(user.quota_bytes)} с последнего запуска прокси`}
    >
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

interface EditTarget {
  nodeId: number
  nodeName: string
  user: MtproxyUser
}

/** Лимиты пользователя: соединения, IP, квота (ГБ), срок. 0 - без ограничения. */
function LimitsDialog({
  target,
  onClose,
  onSave,
}: {
  target: EditTarget | null
  onClose: () => void
  onSave: (payload: MtproxyActionPayload) => Promise<void>
}) {
  const [conns, setConns] = useState('')
  const [ips, setIps] = useState('')
  const [quota, setQuota] = useState('')
  const [expires, setExpires] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!target) return
    const u = target.user
    setConns(String(u.max_conns))
    setIps(String(u.max_ips))
    setQuota(u.quota_bytes ? String(Math.round((u.quota_bytes / GB) * 100) / 100) : '0')
    setExpires(u.expires && u.expires !== '0' ? u.expires.slice(0, 10) : '')
  }, [target])

  const save = async () => {
    setSaving(true)
    try {
      await onSave({
        action: 'setlimits',
        label: target?.user.label,
        max_conns: Number(conns) || 0,
        max_ips: Number(ips) || 0,
        quota_gb: Number(quota.replace(',', '.')) || 0,
        expires: expires || '0',
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {target?.user.label} · {target?.nodeName}
          </DialogTitle>
          <DialogDescription>
            0 — без ограничения. Квоту прокси считает с последнего своего запуска.
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="mtp-conns">Соединений</Label>
            <Input id="mtp-conns" inputMode="numeric" value={conns} onChange={(e) => setConns(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="mtp-ips">IP (устройств)</Label>
            <Input id="mtp-ips" inputMode="numeric" value={ips} onChange={(e) => setIps(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="mtp-quota">Квота, ГБ</Label>
            <div className="flex gap-1">
              <Input id="mtp-quota" inputMode="decimal" value={quota} onChange={(e) => setQuota(e.target.value)} />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setQuota(String((Number(quota.replace(',', '.')) || 0) + 5))}
              >
                +5
              </Button>
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="mtp-exp">Действует до</Label>
            <Input id="mtp-exp" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Отмена
          </Button>
          <Button onClick={() => void save()} disabled={saving}>
            {saving && <Loader2 size={14} className="animate-spin" />}
            Сохранить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Пользователи (секреты) узла: кто подключён, трафик, лимиты, квота и действия. */
function UsersTable({
  node,
  onlineOnly,
  busy,
  onEdit,
  onAction,
}: {
  node: MtproxyNodeStatus
  onlineOnly: boolean
  busy: string | null
  onEdit: (user: MtproxyUser) => void
  onAction: (payload: MtproxyActionPayload) => void
}) {
  const sorted = [...(node.users ?? [])]
    .filter((u) => !onlineOnly || u.connections > 0)
    .sort((a, b) => b.connections - a.connections || b.total_bytes - a.total_bytes)
  if (sorted.length === 0) {
    return <p className="text-xs text-muted-foreground">Сейчас никто не подключён.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-muted-foreground">
          <tr className="border-b text-left">
            <th className="py-1 pr-2 font-medium">Пользователь</th>
            <th className="py-1 pr-2 font-medium" title="Соединений сейчас / лимит">
              Соед.
            </th>
            <th className="py-1 pr-2 font-medium" title="IP сейчас / лимит">
              IP
            </th>
            <th className="py-1 pr-2 font-medium">Трафик</th>
            <th className="py-1 pr-2 font-medium">Квота</th>
            <th className="py-1 font-medium" />
          </tr>
        </thead>
        <tbody>
          {sorted.map((user) => {
            const key = `${node.node_id}:${user.label}`
            return (
              <tr key={user.label} className={cn('border-b last:border-0', !user.enabled && 'opacity-60')}>
                <td className="py-1 pr-2">
                  <span
                    className={cn(
                      'mr-1 inline-block h-1.5 w-1.5 rounded-full',
                      user.connections ? 'bg-emerald-500' : 'bg-muted-foreground/40',
                    )}
                  />
                  {user.label}
                  {!user.enabled && <span className="text-muted-foreground"> (выкл.)</span>}
                  {user.expires && user.expires !== '0' && (
                    <span className="text-muted-foreground"> · до {user.expires.slice(0, 10)}</span>
                  )}
                </td>
                <td className="py-1 pr-2 tabular-nums">
                  {user.connections}/{user.max_conns || '∞'}
                </td>
                <td className="py-1 pr-2 tabular-nums">
                  {user.unique_ips}/{user.max_ips || '∞'}
                </td>
                <td className="py-1 pr-2 tabular-nums">{formatBytes(user.total_bytes)}</td>
                <td className="py-1 pr-2">
                  <QuotaCell user={user} />
                </td>
                <td className="whitespace-nowrap py-1 text-right">
                  {busy === key ? (
                    <Loader2 size={14} className="inline animate-spin" />
                  ) : (
                    <>
                      <Button variant="ghost" size="icon" className="h-7 w-7" title="Лимиты и квота" onClick={() => onEdit(user)}>
                        <Pencil size={13} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        title="Скопировать ссылку"
                        onClick={() => onAction({ action: 'link', label: user.label })}
                      >
                        <Copy size={13} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className={cn('h-7 w-7', user.enabled ? 'text-muted-foreground' : 'text-emerald-600')}
                        title={user.enabled ? 'Выключить' : 'Включить'}
                        onClick={() => onAction({ action: user.enabled ? 'disable' : 'enable', label: user.label })}
                      >
                        <Power size={13} />
                      </Button>
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** Вкладка MTProxy: MTProxyL на всех VPN-узлах, пользователи и управление лимитами. */
export default function MtproxyPage() {
  const { success, error: notifyError } = useNotifications()
  const [nodes, setNodes] = useState<MtproxyNodeStatus[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [countdown, setCountdown] = useState(REFRESH_SEC)
  const [busy, setBusy] = useState<string | null>(null)
  const [editing, setEditing] = useState<EditTarget | null>(null)
  const [onlineOnly, setOnlineOnly] = useState(true)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const toggleExpanded = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const load = useCallback(async (refresh = false) => {
    setLoading(true)
    setError(null)
    try {
      setNodes((await getMtproxyStatus(refresh)).nodes)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить состояние MTProxy')
    } finally {
      setLoading(false)
      setCountdown(REFRESH_SEC)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useIntervalWhenVisible(
    () => {
      setCountdown((c) => {
        if (c <= 1) {
          void load(true)
          return REFRESH_SEC
        }
        return c - 1
      })
    },
    1000,
    { enabled: autoRefresh },
  )

  const act = useCallback(
    async (node: MtproxyNodeStatus, payload: MtproxyActionPayload) => {
      setBusy(`${node.node_id}:${payload.label ?? payload.action}`)
      try {
        const result = await runMtproxyAction(node.node_id, payload)
        if (payload.action === 'link') {
          const link = result.links?.[0]
          if (!link) throw new Error('MTProxyL не вернул ссылку')
          await navigator.clipboard.writeText(link)
          success(`Ссылка ${payload.label} скопирована`)
          return
        }
        success(
          {
            setlimits: `Лимиты ${payload.label} сохранены`,
            enable: `${payload.label} включён`,
            disable: `${payload.label} выключен`,
            reset_traffic: `Трафик на ${node.node_name} обнулён`,
            restart: `MTProxy на ${node.node_name} перезапущен, квоты обнулены`,
          }[payload.action] ?? 'Готово',
        )
        await load(true)
      } catch (err) {
        notifyError(err instanceof Error ? err.message : 'Действие не выполнено')
      } finally {
        setBusy(null)
      }
    },
    [load, success, notifyError],
  )

  const installed = nodes?.filter((n) => n.installed) ?? []
  const running = installed.filter((n) => n.running).length
  const online = installed.reduce((sum, n) => sum + (n.users?.filter((u) => u.connections > 0).length ?? 0), 0)

  return (
    <div className="space-y-6">
      <PageSectionHeader
        icon={Send}
        title="MTProxy"
        description={
          <>
            MTProxyL на всех узлах: работает ли, домен маскировки, пользователи, лимиты и доступность из России.
            {nodes && ` Работает ${running} из ${installed.length}, пользователей онлайн: ${online}.`}
          </>
        }
        actions={
          <AutoRefreshControl
            enabled={autoRefresh}
            onToggle={() => setAutoRefresh((v) => !v)}
            countdown={countdown}
            intervalSec={REFRESH_SEC}
            refreshing={loading}
            onManualRefresh={() => void load(true)}
          />
        }
      />

      <div className="flex w-fit overflow-hidden rounded-md border text-xs" role="tablist" aria-label="Какие пользователи показывать">
        {([true, false] as const).map((value) => (
          <button
            key={String(value)}
            type="button"
            role="tab"
            aria-selected={onlineOnly === value}
            onClick={() => setOnlineOnly(value)}
            className={cn(
              'px-3 py-1.5 transition-colors',
              onlineOnly === value ? 'bg-primary text-primary-foreground' : 'bg-card hover:bg-muted/60',
            )}
          >
            {value ? 'Пользователи: онлайн' : 'Пользователи: все'}
          </button>
        ))}
      </div>

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

      <div className="grid gap-4 xl:grid-cols-2">
        {nodes?.map((node) => {
          const pct = node.availability?.percentage
          const nodeBusy = busy === `${node.node_id}:reset_traffic` || busy === `${node.node_id}:restart`
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
                        ? `${node.public_port} → слушает ${node.port}`
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
                {node.installed && node.users && node.users.length > 0 && (
                  <div className="space-y-2">
                    <button
                      type="button"
                      className="flex items-center gap-1 text-sm font-medium hover:text-primary"
                      onClick={() => toggleExpanded(node.node_id)}
                      aria-expanded={expanded.has(node.node_id)}
                    >
                      {expanded.has(node.node_id) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      Пользователи: {node.users.length}, онлайн {node.users.filter((u) => u.connections > 0).length}
                    </button>
                    {expanded.has(node.node_id) && (
                      <UsersTable
                        node={node}
                        onlineOnly={onlineOnly}
                        busy={busy}
                        onEdit={(user) => setEditing({ nodeId: node.node_id, nodeName: node.node_name, user })}
                        onAction={(payload) => void act(node, payload)}
                      />
                    )}
                  </div>
                )}
                {node.installed && node.users === undefined && (
                  <p className="text-xs text-muted-foreground">
                    Пользователи не видны: обновите агент узла до 1.22.0 или новее.
                  </p>
                )}
                {node.installed && node.running && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={nodeBusy}
                      onClick={() => {
                        if (window.confirm(`Обнулить накопленный трафик всех пользователей на ${node.node_name}?`)) {
                          void act(node, { action: 'reset_traffic' })
                        }
                      }}
                    >
                      <RotateCcw size={14} />
                      Обнулить трафик
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={nodeBusy}
                      onClick={() => {
                        if (
                          window.confirm(
                            `Перезапустить MTProxy на ${node.node_name}? Квоты обнулятся, клиенты Telegram переподключатся за пару секунд.`,
                          )
                        ) {
                          void act(node, { action: 'restart' })
                        }
                      }}
                    >
                      {nodeBusy ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                      Перезапустить (обнулить квоты)
                    </Button>
                  </div>
                )}
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

      <LimitsDialog
        target={editing}
        onClose={() => setEditing(null)}
        onSave={async (payload) => {
          const node = nodes?.find((n) => n.node_id === editing?.nodeId)
          if (node) await act(node, payload)
          setEditing(null)
        }}
      />
    </div>
  )
}
