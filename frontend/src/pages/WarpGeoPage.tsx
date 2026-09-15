import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Cloud, ChevronDown, ChevronRight, CircleCheck, CircleX, Loader2, RefreshCw, Satellite } from 'lucide-react'
import {
  applyWarpChanges,
  checkWarpGeo,
  getWarpGeoStatus,
  listWarpGeoNodes,
  saveWarpProtonConfig,
  setWarpProvider,
  testCloudflareWarpPreview,
} from '@/api/warpGeo'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
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
  const [protonDraft, setProtonDraft] = useState<{ antizapret: string; vpn: string }>({ antizapret: '', vpn: '' })
  const [savingScope, setSavingScope] = useState<'antizapret' | 'vpn' | null>(null)
  const [manageError, setManageError] = useState<string | null>(null)
  const [manageMessage, setManageMessage] = useState<string | null>(null)
  const [switchingProvider, setSwitchingProvider] = useState(false)
  const [applying, setApplying] = useState(false)
  const [expandedProtonScope, setExpandedProtonScope] = useState<'antizapret' | 'vpn' | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewResult, setPreviewResult] = useState<WarpGeoCheckResponse | null>(null)

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
    setProtonDraft({ antizapret: '', vpn: '' })
    setManageError(null)
    setManageMessage(null)
    setExpandedProtonScope(null)
    setPreviewResult(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId])

  const handleSaveProtonConfig = async (scope: 'antizapret' | 'vpn') => {
    if (nodeId === null) return
    const raw = protonDraft[scope].trim()
    if (!raw) return
    setSavingScope(scope)
    setManageError(null)
    setManageMessage(null)
    try {
      await saveWarpProtonConfig(nodeId, scope, raw)
      setProtonDraft((prev) => ({ ...prev, [scope]: '' }))
      setManageMessage('Ключ сохранён в конфиг. Нажмите «Применить», чтобы поднять туннель.')
      loadStatus(nodeId)
    } catch (err) {
      setManageError(err instanceof Error ? err.message : 'Не удалось сохранить конфиг')
    } finally {
      setSavingScope(null)
    }
  }

  const handleSwitchProvider = async (provider: 'proton' | 'cloudflare') => {
    if (nodeId === null || status?.warp_provider === provider) return
    setSwitchingProvider(true)
    setManageError(null)
    setManageMessage(null)
    try {
      await setWarpProvider(nodeId, provider)
      setManageMessage('Провайдер сохранён в конфиг. Нажмите «Применить», чтобы переключить туннель.')
      loadStatus(nodeId)
    } catch (err) {
      setManageError(err instanceof Error ? err.message : 'Не удалось сменить провайдера')
    } finally {
      setSwitchingProvider(false)
    }
  }

  const handlePreviewCloudflare = async () => {
    if (nodeId === null) return
    setPreviewLoading(true)
    setPreviewResult(null)
    setManageError(null)
    try {
      const result = await testCloudflareWarpPreview(nodeId)
      setPreviewResult(result)
    } catch (err) {
      setPreviewResult({
        scope: 'raw',
        interface: null,
        error: err instanceof Error ? err.message : 'Не удалось выполнить предпросмотр',
      })
    } finally {
      setPreviewLoading(false)
    }
  }

  const handleApply = async () => {
    if (nodeId === null) return
    setApplying(true)
    setManageError(null)
    setManageMessage(null)
    try {
      const result = await applyWarpChanges(nodeId)
      setManageMessage(result.success ? 'Применено — туннели подняты заново.' : `up.sh завершился с ошибкой: ${result.output}`)
      loadStatus(nodeId)
    } catch (err) {
      setManageError(err instanceof Error ? err.message : 'Не удалось применить изменения')
    } finally {
      setApplying(false)
    }
  }

  const renderCheckBadges = (result: WarpGeoCheckResponse) => (
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
          {result.gemini_status && result.gemini_status !== 'unknown' && (
            <Badge
              variant={result.gemini_status === 'blocked' ? 'destructive' : 'outline'}
              title="У gemini.google.com нет отдельного поля страны в ответе (в отличие от YouTube) - страна здесь из бейджа YouTube рядом: тот же IP, тот же geoIP у Google."
            >
              Gemini {result.gemini_status === 'blocked' ? 'заблокирован' : 'видит как'}
              {result.gemini_status !== 'blocked' && result.youtube_gl ? `: ${result.youtube_gl}` : ''}
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
          {(result.checked_fields ?? 0) < 2 && (
            <Badge
              variant="warning"
              title="YouTube и/или Gemini не ответили за время проверки - вердикт опирается только на один источник (обычно Cloudflare loc), может ошибаться. Нажмите «Обновить» ещё раз."
            >
              мало данных, попробуйте ещё раз
            </Badge>
          )}
        </>
      )}
    </div>
  )

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
        <CardHeader>
          <CardTitle className="text-base">Управление WARP</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">Провайдер:</span>
            <Button
              size="sm"
              variant={status?.warp_provider === 'proton' ? 'default' : 'outline'}
              disabled={nodeId === null || switchingProvider}
              onClick={() => handleSwitchProvider('proton')}
            >
              Proton
            </Button>
            <Button
              size="sm"
              variant={status?.warp_provider === 'cloudflare' ? 'default' : 'outline'}
              disabled={nodeId === null || switchingProvider}
              onClick={() => handleSwitchProvider('cloudflare')}
            >
              Cloudflare WARP
            </Button>
            {switchingProvider && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          </div>

          <div className="flex flex-col gap-2 rounded-md border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm">
                Как будет видеть нас Cloudflare WARP — без смены провайдера, без обрыва текущих
                тоннелей (временный отдельный интерфейс, снимается сразу после проверки).
              </span>
              <Button
                size="sm"
                variant="outline"
                className="shrink-0 gap-1.5"
                disabled={nodeId === null || previewLoading}
                onClick={handlePreviewCloudflare}
              >
                {previewLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Cloud className="h-4 w-4" />}
                Проверить Cloudflare без переключения
              </Button>
            </div>
            {previewLoading && (
              <p className="text-xs text-muted-foreground">
                Проверяем (с повторами на нестабильном временном тоннеле) — обычно несколько секунд,
                иногда до минуты…
              </p>
            )}
            {previewResult && renderCheckBadges(previewResult)}
          </div>

          {status?.warp_provider === 'proton' &&
            (['antizapret', 'vpn'] as const).map((scope) => {
              const expanded = expandedProtonScope === scope
              const configured = scope === 'antizapret' ? status.proton_antizapret_configured : status.proton_vpn_configured
              return (
                <div key={scope} className="flex flex-col gap-2 rounded-md border p-3">
                  <button
                    type="button"
                    className="flex items-center gap-2 text-left text-sm font-medium"
                    onClick={() => setExpandedProtonScope(expanded ? null : scope)}
                  >
                    {expanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                    Proton-конфиг для {scope === 'antizapret' ? 'AntiZapret VPN' : 'полного VPN'}
                    <span className="text-xs font-normal text-muted-foreground">
                      (сейчас: {configured ? 'ключ задан' : 'ключ не задан'})
                    </span>
                  </button>
                  {expanded && (
                    <>
                      <Textarea
                        placeholder={'[Interface]\nPrivateKey = ...\nAddress = 10.2.0.2/32\n\n[Peer]\nPublicKey = ...\nEndpoint = host:port'}
                        value={protonDraft[scope]}
                        onChange={(e) => setProtonDraft((prev) => ({ ...prev, [scope]: e.target.value }))}
                        className="min-h-[120px] font-mono text-xs"
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        className="self-start"
                        disabled={nodeId === null || !protonDraft[scope].trim() || savingScope === scope}
                        onClick={() => handleSaveProtonConfig(scope)}
                      >
                        {savingScope === scope ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Сохранить ключ'}
                      </Button>
                    </>
                  )}
                </div>
              )
            })}

          <div className="flex flex-col gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
            <div className="flex items-start gap-2 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <span>
                Смена провайдера{status?.warp_provider === 'proton' ? ' и новые ключи' : ''} не
                действует, пока не нажата «Применить» — это выполняет{' '}
                <code className="font-mono">up.sh</code> на узле, который кратко (на секунды)
                обрывает ВСЕ активные туннели на этом сервере, не только WARP.
              </span>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="self-start gap-1.5"
              disabled={nodeId === null || applying}
              onClick={handleApply}
            >
              {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              Применить (перезапустить туннели)
            </Button>
          </div>

          {manageMessage && <p className="text-sm text-muted-foreground">{manageMessage}</p>}
          {manageError && <p className="text-sm text-destructive">{manageError}</p>}
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
                {result && renderCheckBadges(result)}
              </div>
            )
          })}
        </CardContent>
      </Card>
    </div>
  )
}
