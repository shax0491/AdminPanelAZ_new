import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, RefreshCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { getWarpDns } from '@/api/warpGeo'
import type { WarpDnsCounterRow, WarpDnsResponse } from '@/types'

const STATUS_BADGE: Record<WarpDnsResponse['status'], { label: string; variant: 'success' | 'destructive' | 'warning' | 'outline' }> = {
  ok: { label: 'DNS через WARP', variant: 'success' },
  leak: { label: 'Утечка DNS', variant: 'destructive' },
  off: { label: 'Выключено (как у апстрима)', variant: 'outline' },
  no_warp: { label: 'WARP не используется', variant: 'warning' },
  russian_set: { label: 'Российский набор DNS', variant: 'warning' },
}

type Snapshot = { at: number; rows: Record<string, number> }

function counterKey(kind: 'intercepted' | 'foreign', row: WarpDnsCounterRow) {
  return `${kind}:${row.subnet}`
}

function takeSnapshot(data: WarpDnsResponse): Snapshot {
  const rows: Record<string, number> = {}
  for (const row of data.counters.intercepted) rows[counterKey('intercepted', row)] = row.udp + row.tcp
  for (const row of data.counters.foreign ?? []) rows[counterKey('foreign', row)] = row.udp + row.tcp
  return { at: data.checked_at, rows }
}

function CounterTable({
  kind,
  rows,
  previous,
  checkedAt,
}: {
  kind: 'intercepted' | 'foreign'
  rows: WarpDnsCounterRow[]
  previous: Snapshot | null
  checkedAt: number
}) {
  const seconds = previous ? Math.max(1, checkedAt - previous.at) : 0
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Подсеть клиентов</TableHead>
          <TableHead className="text-right">UDP</TableHead>
          <TableHead className="text-right">TCP</TableHead>
          <TableHead className="text-right">С прошлого обновления</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const total = row.udp + row.tcp
          const before = previous?.rows[counterKey(kind, row)]
          const delta = before === undefined ? null : total - before
          return (
            <TableRow key={row.subnet}>
              <TableCell>
                <div className="text-sm">{row.label}</div>
                <div className="font-mono text-xs text-muted-foreground">{row.subnet}</div>
              </TableCell>
              <TableCell className="text-right font-mono">{row.udp}</TableCell>
              <TableCell className="text-right font-mono">{row.tcp}</TableCell>
              <TableCell className="text-right font-mono text-muted-foreground">
                {delta === null ? '—' : `+${delta} за ${Math.round(seconds)} с`}
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

export default function WarpDnsCard({ nodeId }: { nodeId: number | null }) {
  const [data, setData] = useState<WarpDnsResponse | null>(null)
  const [previous, setPrevious] = useState<Snapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showResolvers, setShowResolvers] = useState(false)

  const load = useCallback(
    async (id: number, keepPrevious: boolean) => {
      setLoading(true)
      setError(null)
      try {
        const next = await getWarpDns(id)
        setPrevious(keepPrevious && data ? takeSnapshot(data) : null)
        setData(next)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Ошибка проверки DNS')
      } finally {
        setLoading(false)
      }
    },
    [data],
  )

  useEffect(() => {
    setData(null)
    setPrevious(null)
    if (nodeId !== null) void load(nodeId, false)
    // load зависит от data только ради прироста счётчиков; при смене узла начинаем с нуля
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId])

  const badge = data ? STATUS_BADGE[data.status] : null
  const kresd2 = data?.kresd['kresd@2']

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">DNS</CardTitle>
        <Button
          size="sm"
          variant="ghost"
          disabled={nodeId === null || loading}
          onClick={() => nodeId !== null && void load(nodeId, true)}
          className="gap-1.5"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Обновить
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {data && badge && (
          <>
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={badge.variant}>{badge.label}</Badge>
                <span className="text-sm text-muted-foreground">{data.status_text}</span>
              </div>
              <p className="text-xs text-muted-foreground">
                Наборы DNS: kresd@1 (российские домены) — {data.dns1 || '?'}, kresd@2 (заблокированные домены, WARP,
                полный VPN) — {data.dns2 || '?'}. Адрес источника kresd@2:{' '}
                <span className="font-mono">{kresd2?.outgoing ?? 'адрес сервера'}</span>
                {kresd2 && !kresd2.reachable && ' (kresd@2 не ответил на управляющий сокет)'}
              </p>
            </div>

            <div className="flex flex-col gap-2">
              <span className="text-sm font-medium">DNS-запросы клиентов, которые отвечает AntiZapret</span>
              <p className="text-xs text-muted-foreground">
                Любой запрос клиента на порт 53, попавший в туннель, сервер перенаправляет на свой kresd. Считаются
                DNS-соединения (первый пакет), а не отдельные запросы. Если клиентов много, а прирост почти нулевой —
                устройства или роутеры резолвят мимо туннеля, и фейковые IP (режимы WARP 3/4) у них не работают.
              </p>
              <CounterTable kind="intercepted" rows={data.counters.intercepted} previous={previous} checkedAt={data.checked_at} />
            </div>

            <div className="flex flex-col gap-2">
              <span className="text-sm font-medium">Запросы клиентов к чужим DNS</span>
              {data.counters.foreign === null ? (
                <p className="text-xs text-muted-foreground">
                  Счётчик появится после обновления скриптов узла (setup.sh --update): он показывает устройства с
                  прописанным 1.1.1.1/8.8.8.8 вместо DNS туннеля.
                </p>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground">
                    Запросы на порт 53 не к DNS туннеля (10.x.x.1), а к 1.1.1.1, 8.8.8.8 и т.п. Они тоже перехватываются,
                    но значит, у устройства или роутера прописан чужой DNS.
                  </p>
                  <CounterTable kind="foreign" rows={data.counters.foreign} previous={previous} checkedAt={data.checked_at} />
                </>
              )}
            </div>

            <div className="flex flex-col gap-2">
              <button
                type="button"
                className="flex items-center gap-1 text-left text-sm font-medium"
                onClick={() => setShowResolvers((v) => !v)}
              >
                {showResolvers ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                Пути к резолверам ({data.resolvers.length})
              </button>
              {showResolvers && (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Экземпляр</TableHead>
                      <TableHead>Роль</TableHead>
                      <TableHead>Резолвер</TableHead>
                      <TableHead>Интерфейс</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.resolvers.map((r) => (
                      <TableRow key={`${r.instance}-${r.role}-${r.ip}`}>
                        <TableCell className="font-mono text-xs">{r.instance}</TableCell>
                        <TableCell className="text-xs">{r.role}</TableCell>
                        <TableCell className="font-mono text-xs">{r.ip}</TableCell>
                        <TableCell>
                          <Badge variant={r.via_warp ? 'success' : 'outline'} className="font-mono">
                            {r.interface ?? '?'}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
