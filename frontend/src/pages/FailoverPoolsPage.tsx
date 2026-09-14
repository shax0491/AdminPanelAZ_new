import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Plus, RefreshCw, Trash2, XCircle } from 'lucide-react'
import {
  addFailoverPoolMember,
  createFailoverPool,
  deleteFailoverPool,
  getFailoverClientStatus,
  getNodes,
  linkFailoverClient,
  listFailoverPools,
  mirrorFailoverMemberIdentity,
  removeFailoverPoolMember,
  resyncFailoverClient,
  setFailoverFront,
  switchCheckFailoverPool,
  unlinkFailoverClient,
  updateFailoverPool,
} from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import EmptyState from '@/components/ui/EmptyState'
import SettingsAlert from '@/components/settings/SettingsAlert'
import { useNotifications } from '@/context/NotificationContext'
import type { FailoverPool, FailoverPoolStrategy, FailoverStatusEntry, Node } from '@/types'

function ModeBadge({ mode }: { mode: string }) {
  return (
    <Badge variant={mode === 'auto' ? 'success' : 'secondary'}>
      {mode === 'auto' ? 'Авто' : 'Ручное'}
    </Badge>
  )
}

function StrategyBadge({ strategy }: { strategy: FailoverPoolStrategy }) {
  return strategy === 'dnat_front' ? (
    <Badge variant="outline" title="Один статический конфиг у клиента, переключает панель на фронте">
      Фронт (DNAT)
    </Badge>
  ) : (
    <Badge variant="outline" title="Несколько конфигов, переключается само устройство">
      На устройстве
    </Badge>
  )
}

function StatusRow({ entry }: { entry: FailoverStatusEntry }) {
  const age = Date.now() - new Date(entry.reported_at).getTime()
  const stale = age > 5 * 60 * 1000
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card/40 px-3 py-2 text-sm">
      {entry.healthy && !stale ? (
        <CheckCircle2 size={16} className="shrink-0 text-emerald-500" />
      ) : (
        <XCircle size={16} className="shrink-0 text-destructive" />
      )}
      <span className="font-medium">{entry.device_label}</span>
      <span className="text-muted-foreground">
        активен: {entry.active_node_name ?? '—'}
      </span>
      {entry.detail && <span className="text-muted-foreground">· {entry.detail}</span>}
      <span className="ml-auto text-xs text-muted-foreground">
        {stale ? 'нет свежих данных · ' : ''}
        {new Date(entry.reported_at).toLocaleString('ru-RU')}
      </span>
    </div>
  )
}

function ClientLinkPanel({
  pool,
  clientName,
  onUnlinked,
}: {
  pool: FailoverPool
  clientName: string
  onUnlinked: () => void
}) {
  const { success, error: notifyError } = useNotifications()
  const [statuses, setStatuses] = useState<FailoverStatusEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setStatuses(await getFailoverClientStatus(pool.id, clientName))
    } catch (err) {
      notifyError(err instanceof Error ? err.message : 'Не удалось загрузить статус')
    } finally {
      setLoading(false)
    }
  }, [pool.id, clientName, notifyError])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="space-y-2 rounded-lg border border-border/70 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{clientName}</span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={syncing}
            onClick={async () => {
              setSyncing(true)
              try {
                const result = await resyncFailoverClient(pool.id, clientName)
                if (result.errors.length > 0) {
                  notifyError(`Синхронизировано: ${result.synced_nodes.join(', ') || 'никого'}. Ошибки: ${result.errors.join('; ')}`)
                } else {
                  success(`Пир синхронизирован на: ${result.synced_nodes.join(', ') || 'уже был синхронизирован везде'}`)
                }
              } catch (err) {
                notifyError(err instanceof Error ? err.message : 'Ошибка синхронизации')
              } finally {
                setSyncing(false)
              }
            }}
          >
            <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} />
            Синхронизировать пиры
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              try {
                await unlinkFailoverClient(pool.id, clientName)
                success('Клиент отвязан от пула')
                onUnlinked()
              } catch (err) {
                notifyError(err instanceof Error ? err.message : 'Ошибка')
              }
            }}
          >
            <Trash2 size={14} />
          </Button>
        </div>
      </div>
      {loading ? (
        <p className="text-xs text-muted-foreground">Загрузка статуса…</p>
      ) : statuses.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Ни одно устройство ещё не отметилось (проверьте, что приложение/роутер запущены и получили токен).
        </p>
      ) : (
        <div className="space-y-1.5">
          {statuses.map((s) => (
            <StatusRow key={s.device_label} entry={s} />
          ))}
        </div>
      )}
    </div>
  )
}

function FrontPanel({
  pool,
  nodes,
  onChanged,
}: {
  pool: FailoverPool
  nodes: Node[]
  onChanged: () => void
}) {
  const { success, error: notifyError } = useNotifications()
  const [frontNodeId, setFrontNodeId] = useState<string>(pool.front_node_id ? String(pool.front_node_id) : '')
  const [frontPort, setFrontPort] = useState<string>(pool.front_port ? String(pool.front_port) : '')
  const [checking, setChecking] = useState(false)
  const [mirroringId, setMirroringId] = useState<number | null>(null)

  const proxyNodes = nodes.filter((n) => n.node_kind === 'proxy')
  const activeMember = pool.members.find((m) => m.id === pool.active_member_id)

  return (
    <div className="space-y-3 rounded-lg border border-border/70 p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Фронт — клиент указывает конфигом только сюда, этот адрес никогда не меняется
      </p>
      {pool.front_node_id ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="outline">
            {nodes.find((n) => n.id === pool.front_node_id)?.name ?? `#${pool.front_node_id}`}
          </Badge>
          <span className="text-muted-foreground">порт {pool.front_port}</span>
          <span className="ml-auto text-muted-foreground">
            активен: {activeMember ? activeMember.label || activeMember.node_name : '— ещё не переключалось'}
          </span>
        </div>
      ) : (
        <p className="text-xs text-amber-600 dark:text-amber-400">Фронт ещё не назначен.</p>
      )}

      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={async (e) => {
          e.preventDefault()
          if (!frontNodeId || !frontPort) return
          try {
            await setFailoverFront(pool.id, {
              front_node_id: Number(frontNodeId),
              front_port: Number(frontPort),
            })
            success('Фронт назначен')
            onChanged()
          } catch (err) {
            notifyError(err instanceof Error ? err.message : 'Ошибка')
          }
        }}
      >
        <div className="space-y-1">
          <Label className="text-xs">Фронт-узел (тип «Прокси»)</Label>
          <select
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            value={frontNodeId}
            onChange={(e) => setFrontNodeId(e.target.value)}
          >
            <option value="">Выберите узел…</option>
            {proxyNodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name} ({n.host})
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">UDP-порт AmneziaWG 2.0</Label>
          <Input
            className="h-9 w-32 text-sm"
            type="number"
            min={1}
            max={65535}
            placeholder="напр. 39001"
            value={frontPort}
            onChange={(e) => setFrontPort(e.target.value)}
          />
        </div>
        <Button size="sm" type="submit" disabled={!frontNodeId || !frontPort}>
          {pool.front_node_id ? 'Сохранить' : 'Назначить фронт'}
        </Button>
        {pool.front_node_id && (
          <Button
            size="sm"
            variant="outline"
            type="button"
            disabled={checking}
            onClick={async () => {
              setChecking(true)
              try {
                const result = await switchCheckFailoverPool(pool.id)
                if (result.errors.length > 0) {
                  notifyError(result.errors.join('; '))
                } else if (result.switched) {
                  success('Переключено')
                } else {
                  success('Проверено — переключение не требуется')
                }
                onChanged()
              } catch (err) {
                notifyError(err instanceof Error ? err.message : 'Ошибка')
              } finally {
                setChecking(false)
              }
            }}
          >
            <RefreshCw size={14} className={checking ? 'animate-spin' : ''} />
            Проверить и переключить
          </Button>
        )}
      </form>

      {proxyNodes.length === 0 && (
        <p className="text-xs text-muted-foreground">
          Нет ни одного узла типа «Прокси» — добавьте выделенный фронт-сервер на странице «Узлы»
          (с установленным proxy_agent), прежде чем назначать его сюда.
        </p>
      )}
      {pool.last_switch_error && (
        <p className="text-xs text-destructive">Последняя ошибка переключения: {pool.last_switch_error}</p>
      )}
      {pool.last_switch_at && (
        <p className="text-xs text-muted-foreground">
          Последнее переключение: {new Date(pool.last_switch_at).toLocaleString('ru-RU')}
        </p>
      )}

      <div className="space-y-1.5">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Identity узлов — должна совпадать с основным (первым по приоритету), иначе хендшейк
          клиента на этом узле не пройдёт
        </p>
        {pool.members.length === 0 && (
          <p className="text-xs text-muted-foreground">Сначала добавьте узлы в пул выше.</p>
        )}
        {pool.members.map((m, idx) => (
          <div
            key={m.id}
            className="flex flex-wrap items-center gap-2 rounded-lg border bg-card/40 px-3 py-2 text-sm"
          >
            <span className="font-medium">{m.label || m.node_name}</span>
            {idx === 0 ? (
              <Badge variant="success">основной — источник identity</Badge>
            ) : m.identity_mirrored_at ? (
              <Badge variant="success">
                клонирована {new Date(m.identity_mirrored_at).toLocaleString('ru-RU')}
              </Badge>
            ) : (
              <Badge variant="warning">не клонирована</Badge>
            )}
            {idx !== 0 && (
              <Button
                size="sm"
                variant="outline"
                className="ml-auto"
                disabled={mirroringId === m.id}
                onClick={async () => {
                  setMirroringId(m.id)
                  try {
                    await mirrorFailoverMemberIdentity(pool.id, m.id)
                    success(`Identity склонирована на ${m.node_name}`)
                    onChanged()
                  } catch (err) {
                    notifyError(err instanceof Error ? err.message : 'Ошибка клонирования')
                  } finally {
                    setMirroringId(null)
                  }
                }}
              >
                <RefreshCw size={14} className={mirroringId === m.id ? 'animate-spin' : ''} />
                {m.identity_mirrored_at ? 'Обновить' : 'Клонировать identity'}
              </Button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function PoolCard({
  pool,
  nodes,
  onChanged,
}: {
  pool: FailoverPool
  nodes: Node[]
  onChanged: () => void
}) {
  const { success, error: notifyError } = useNotifications()
  const [memberNodeId, setMemberNodeId] = useState<string>('')
  const [memberLabel, setMemberLabel] = useState('')
  const [clientName, setClientName] = useState('')
  const linkedClients = pool.client_names

  const availableNodes = nodes.filter((n) => !pool.members.some((m) => m.node_id === n.id))

  return (
    <div className="space-y-4 rounded-xl border bg-card/50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold">{pool.name}</h3>
            <StrategyBadge strategy={pool.strategy} />
            <ModeBadge mode={pool.mode} />
            {!pool.enabled && <Badge variant="warning">Выключен</Badge>}
          </div>
          <p className="text-xs text-muted-foreground">
            health-check: {pool.health_check_target} · интервал {pool.health_check_interval_s} с ·
            порог {pool.down_threshold}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={async () => {
              try {
                await updateFailoverPool(pool.id, { mode: pool.mode === 'auto' ? 'manual' : 'auto' })
                onChanged()
              } catch (err) {
                notifyError(err instanceof Error ? err.message : 'Ошибка')
              }
            }}
          >
            {pool.mode === 'auto' ? 'Сделать ручным' : 'Сделать авто'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              try {
                await deleteFailoverPool(pool.id)
                success(`Пул «${pool.name}» удалён (узлы и пиры на них не затронуты)`)
                onChanged()
              } catch (err) {
                notifyError(err instanceof Error ? err.message : 'Ошибка')
              }
            }}
          >
            <Trash2 size={14} />
          </Button>
        </div>
      </div>

      <div>
        <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Участники ({pool.members.length})
        </p>
        {pool.members.length === 0 ? (
          <p className="text-xs text-muted-foreground">Пока нет ни одного сервера в пуле.</p>
        ) : (
          <div className="space-y-1.5">
            {pool.members.map((m) => (
              <div
                key={m.id}
                className="flex flex-wrap items-center gap-2 rounded-lg border bg-card/40 px-3 py-2 text-sm"
              >
                <Badge variant="outline">#{m.priority}</Badge>
                <span className="font-medium">{m.label || m.node_name}</span>
                <span className="text-xs text-muted-foreground">{m.node_host}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto"
                  onClick={async () => {
                    try {
                      await removeFailoverPoolMember(pool.id, m.id)
                      onChanged()
                    } catch (err) {
                      notifyError(err instanceof Error ? err.message : 'Ошибка')
                    }
                  }}
                >
                  <Trash2 size={14} />
                </Button>
              </div>
            ))}
          </div>
        )}
        <form
          className="mt-2 flex flex-wrap items-end gap-2"
          onSubmit={async (e) => {
            e.preventDefault()
            if (!memberNodeId) return
            try {
              await addFailoverPoolMember(pool.id, {
                node_id: Number(memberNodeId),
                priority: (pool.members[pool.members.length - 1]?.priority ?? 0) + 10,
                label: memberLabel.trim() || null,
              })
              setMemberNodeId('')
              setMemberLabel('')
              onChanged()
            } catch (err) {
              notifyError(err instanceof Error ? err.message : 'Ошибка')
            }
          }}
        >
          <div className="space-y-1">
            <Label className="text-xs">Узел</Label>
            <select
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={memberNodeId}
              onChange={(e) => setMemberNodeId(e.target.value)}
            >
              <option value="">Выберите узел…</option>
              {availableNodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name} ({n.host})
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Домен/IP для клиента (необязательно)</Label>
            <Input
              className="h-9 w-56 text-sm"
              placeholder="по умолчанию — хост узла"
              value={memberLabel}
              onChange={(e) => setMemberLabel(e.target.value)}
            />
          </div>
          <Button size="sm" type="submit" disabled={!memberNodeId}>
            <Plus size={14} />
            Добавить узел
          </Button>
        </form>
      </div>

      {pool.strategy === 'dnat_front' ? (
        <FrontPanel pool={pool} nodes={nodes} onChanged={onChanged} />
      ) : (
        <div>
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Клиенты в пуле
          </p>
          {linkedClients.length === 0 && (
            <p className="mb-2 text-xs text-muted-foreground">
              Привяжите клиента по имени (у него уже должен быть конфиг AmneziaWG 2.0 на одном из узлов) —
              пир сразу синхронизируется на остальные узлы пула.
            </p>
          )}
          <div className="space-y-2">
            {linkedClients.map((name) => (
              <ClientLinkPanel key={name} pool={pool} clientName={name} onUnlinked={onChanged} />
            ))}
          </div>
          <form
            className="mt-2 flex flex-wrap items-end gap-2"
            onSubmit={async (e) => {
              e.preventDefault()
              const name = clientName.trim()
              if (!name) return
              try {
                await linkFailoverClient(pool.id, name)
                success(`Клиент «${name}» привязан и синхронизирован`)
                setClientName('')
                onChanged()
              } catch (err) {
                notifyError(err instanceof Error ? err.message : 'Ошибка')
              }
            }}
          >
            <div className="space-y-1">
              <Label className="text-xs">Имя клиента</Label>
              <Input
                className="h-9 w-56 text-sm"
                placeholder="например AT_Keenetic"
                value={clientName}
                onChange={(e) => setClientName(e.target.value)}
              />
            </div>
            <Button size="sm" type="submit" disabled={pool.members.length < 2}>
              <Plus size={14} />
              Привязать клиента
            </Button>
          </form>
          {pool.members.length < 2 && (
            <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
              Добавьте минимум 2 узла в пул, прежде чем привязывать клиентов.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export default function FailoverPoolsPage() {
  const { success, error: notifyError } = useNotifications()
  const [pools, setPools] = useState<FailoverPool[]>([])
  const [nodes, setNodes] = useState<Node[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [newPoolName, setNewPoolName] = useState('')
  const [newPoolStrategy, setNewPoolStrategy] = useState<FailoverPoolStrategy>('client_sync')

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const [poolsData, nodesData] = await Promise.all([listFailoverPools(), getNodes()])
      setPools(poolsData)
      setNodes(nodesData)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Не удалось загрузить пулы автопереключения')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Автопереключение</h1>
        <p className="text-sm text-muted-foreground">
          Пулы серверов AmneziaWG 2.0 для автопереключения между узлами. Два способа: «На
          устройстве» — приложение/роутер сами хранят несколько конфигов и решают, на какой
          переключиться; «На фронт-сервере» — у клиента один статический конфиг (годится для
          штатного AmneziaWG в прошивке роутера, без стороннего приложения), а куда реально идёт
          трафик решает панель через выделенный фронт-узел.
        </p>
      </div>

      {loadError && (
        <SettingsAlert variant="danger" title="Ошибка загрузки">
          {loadError}
        </SettingsAlert>
      )}

      <form
        className="flex flex-wrap items-end gap-2 rounded-xl border bg-card/50 p-4"
        onSubmit={async (e) => {
          e.preventDefault()
          const name = newPoolName.trim()
          if (!name) return
          try {
            await createFailoverPool({ name, strategy: newPoolStrategy })
            setNewPoolName('')
            success(`Пул «${name}» создан`)
            void load()
          } catch (err) {
            notifyError(err instanceof Error ? err.message : 'Ошибка')
          }
        }}
      >
        <div className="space-y-1">
          <Label className="text-xs">Новый пул</Label>
          <Input
            className="h-9 w-64 text-sm"
            placeholder="например «AmneziaWG 2.0 — основной»"
            value={newPoolName}
            onChange={(e) => setNewPoolName(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Как переключается</Label>
          <select
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
            value={newPoolStrategy}
            onChange={(e) => setNewPoolStrategy(e.target.value as FailoverPoolStrategy)}
          >
            <option value="client_sync">На устройстве (приложение/роутер, несколько конфигов)</option>
            <option value="dnat_front">На фронт-сервере (один статический конфиг у клиента)</option>
          </select>
        </div>
        <Button size="sm" type="submit" disabled={!newPoolName.trim()}>
          <Plus size={14} />
          Создать пул
        </Button>
      </form>

      {loading ? (
        <p className="text-sm text-muted-foreground">Загрузка…</p>
      ) : pools.length === 0 ? (
        <EmptyState
          title="Пулов пока нет"
          description="Создайте первый пул выше, добавьте в него минимум 2 узла и привяжите клиента."
        />
      ) : (
        <div className="space-y-4">
          {pools.map((pool) => (
            <PoolCard key={pool.id} pool={pool} nodes={nodes} onChanged={() => void load()} />
          ))}
        </div>
      )}
    </div>
  )
}
