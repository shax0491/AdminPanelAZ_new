import { useCallback, useEffect, useState } from 'react'
import { CircleCheck, CircleX, Loader2, Satellite } from 'lucide-react'
import { checkWarpGeo, getWarpGeoStatus, listWarpGeoNodes } from '@/api/warpGeo'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { WarpGeoCheckResponse, WarpGeoNodesResponse, WarpGeoStatusResponse } from '@/types'

const SCOPE_LABELS: Record<'antizapret' | 'vpn' | 'raw', string> = {
  antizapret: 'AntiZapret VPN (antizapret-*)',
  vpn: 'Полный VPN (vpn-*)',
  raw: 'Сырой IP хоста (без WARP)',
}

const WARP_LABELS: Record<string, string> = {
  '1': 'Выключен',
  '2': 'Весь трафик',
  '3': 'Домены + IP из списков',
  '4': 'Только домены из списков',
}

export default function WarpGeoPage() {
  const [nodes, setNodes] = useState<WarpGeoNodesResponse['nodes']>([])
  const [nodeId, setNodeId] = useState<number | null>(null)
  const [status, setStatus] = useState<WarpGeoStatusResponse | null>(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [checkResults, setCheckResults] = useState<Partial<Record<'antizapret' | 'vpn' | 'raw', WarpGeoCheckResponse>>>({})
  const [checking, setChecking] = useState<string | null>(null)

  useEffect(() => {
    listWarpGeoNodes()
      .then((data) => {
        setNodes(data.nodes)
        if (data.nodes.length > 0) setNodeId(data.nodes[0].id)
      })
      .catch(() => setNodes([]))
  }, [])

  const runCheck = useCallback(async (id: number, scope: 'antizapret' | 'vpn' | 'raw') => {
    setChecking(scope)
    try {
      const result = await checkWarpGeo(id, scope)
      setCheckResults((prev) => ({ ...prev, [scope]: result }))
    } catch (err) {
      setCheckResults((prev) => ({
        ...prev,
        [scope]: {
          scope,
          interface: null,
          error: err instanceof Error ? err.message : 'Ошибка проверки',
        },
      }))
    } finally {
      setChecking(null)
    }
  }, [])

  const runAllChecks = useCallback(
    (id: number) => {
      // Последовательно, а не Promise.all - три curl-запроса на самом узле,
      // параллельный запуск только продлевает каждый из-за конкуренции за сеть.
      void (async () => {
        await runCheck(id, 'antizapret')
        await runCheck(id, 'vpn')
        await runCheck(id, 'raw')
      })()
    },
    [runCheck],
  )

  const loadStatus = useCallback(
    (id: number) => {
      setStatusLoading(true)
      setStatusError(null)
      setCheckResults({})
      getWarpGeoStatus(id)
        .then((data) => {
          setStatus(data)
          runAllChecks(id)
        })
        .catch((err) => setStatusError(err instanceof Error ? err.message : 'Не удалось загрузить статус WARP'))
        .finally(() => setStatusLoading(false))
    },
    [runAllChecks],
  )

  useEffect(() => {
    if (nodeId !== null) loadStatus(nodeId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId])

  return (
    <div className="flex flex-col gap-6 p-6">
      <div className="flex items-center gap-3">
        <Satellite className="h-6 w-6 text-muted-foreground" />
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Warp Geo</h1>
          <p className="text-sm text-muted-foreground">
            Провайдер WARP и гео-проверка исходящего трафика по узлам — Cloudflare colo/страна и
            то, какой страной сервер видит YouTube. Проверка привязана к реальному WARP-интерфейсу
            узла, а не к сырому IP хоста.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Узел</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Select
            value={nodeId !== null ? String(nodeId) : undefined}
            onValueChange={(value) => setNodeId(Number(value))}
          >
            <SelectTrigger className="w-[260px]">
              <SelectValue placeholder="Выберите узел" />
            </SelectTrigger>
            <SelectContent>
              {nodes.map((node) => (
                <SelectItem key={node.id} value={String(node.id)}>
                  {node.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {nodes.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Нет доступных VPN-узлов. Добавьте узел на странице «Узлы».
            </p>
          )}

          {statusLoading && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Загрузка статуса...
            </p>
          )}
          {statusError && <p className="text-sm text-destructive">{statusError}</p>}

          {status && !statusLoading && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">Провайдер WARP</div>
                <div className="font-medium capitalize">{status.warp_provider || '—'}</div>
                {status.warp_provider === 'proton' && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    AntiZapret: {status.proton_antizapret_configured ? 'ключ задан' : 'ключ не задан'} ·
                    {' '}VPN: {status.proton_vpn_configured ? 'ключ задан' : 'ключ не задан'}
                  </div>
                )}
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">WARP для AntiZapret VPN</div>
                <div className="font-medium">{WARP_LABELS[status.antizapret_warp] ?? status.antizapret_warp}</div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">WARP для полного VPN</div>
                <div className="font-medium">{WARP_LABELS[status.vpn_warp] ?? status.vpn_warp}</div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base">Гео-проверка</CardTitle>
          <Button
            size="sm"
            variant="ghost"
            disabled={nodeId === null || checking !== null}
            onClick={() => nodeId !== null && runAllChecks(nodeId)}
            className="gap-1.5"
          >
            {checking !== null ? <Loader2 className="h-4 w-4 animate-spin" /> : <Satellite className="h-4 w-4" />}
            Обновить всё
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {(['antizapret', 'vpn', 'raw'] as const).map((scope) => {
            const result = checkResults[scope]
            return (
              <div key={scope} className="flex flex-col gap-2 rounded-md border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium">{SCOPE_LABELS[scope]}</span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={nodeId === null || checking === scope}
                    onClick={() => nodeId !== null && runCheck(nodeId, scope)}
                  >
                    {checking === scope ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Обновить'}
                  </Button>
                </div>
                {result?.tunnel_matches_config === false && (
                  <div className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-sm text-destructive">
                    ⚠️ Туннель не соответствует конфигу — проверка ниже может относиться к старому
                    провайдеру. {result.tunnel_mismatch_detail}
                  </div>
                )}
                {result && (
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    {result.error ? (
                      <span className="text-destructive">{result.error}</span>
                    ) : (
                      <>
                        {result.cloudflare_loc && (
                          <Badge variant="outline" title="Геолокация по сервису Cloudflare (не провайдер трафика)">
                            Гео-детект (Cloudflare): {result.cloudflare_loc} ({result.cloudflare_colo})
                          </Badge>
                        )}
                        {result.youtube_gl && (
                          <Badge variant="outline" title="Страна, которой YouTube определяет этот выход">
                            YouTube видит как: {result.youtube_gl}
                          </Badge>
                        )}
                        {result.cloudflare_ip && (
                          <Badge variant="outline" className="font-mono">
                            IP: {result.cloudflare_ip}
                          </Badge>
                        )}
                        {result.flagged_as_ru ? (
                          <Badge variant="destructive" className="gap-1">
                            <CircleX className="h-3 w-3" /> Видят как Россию
                          </Badge>
                        ) : (
                          <Badge variant="success" className="gap-1">
                            <CircleCheck className="h-3 w-3" /> Не Россия
                          </Badge>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </CardContent>
      </Card>
    </div>
  )
}
