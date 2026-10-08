import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Cloud, ChevronDown, ChevronRight, CircleCheck, CircleX, Loader2, RefreshCw, Satellite } from 'lucide-react'
import {
  applyWarpChanges,
  checkWarpGeo,
  getWarpGeoStatus,
  listWarpGeoNodes,
  saveWarpProtonFields,
  setWarpModes,
  setWarpProvider,
  testCloudflareWarpPreview,
  type ProtonFields,
} from '@/api/warpGeo'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import ConfirmDialog from '@/components/shared/ConfirmDialog'
import WarpDnsCard from '@/components/warp/WarpDnsCard'
import {
  ANTIZAPRET_WARP_OPTIONS,
  describeModeSwitch,
  isScopePending,
  VPN_WARP_OPTIONS,
  warpModeLabel,
  warpModeUsesList,
} from '@/lib/warpModes'
import type { WarpGeoCheckResponse, WarpGeoNodesResponse, WarpGeoStatusResponse } from '@/types'

const EMPTY_PROTON_FIELDS: ProtonFields = {
  private_key: '',
  public_key: '',
  address: '',
  endpoint_host: '',
  endpoint_port: '',
}

const SCOPE_LABELS: Record<'antizapret' | 'vpn' | 'raw', string> = {
  antizapret: 'AntiZapret VPN (antizapret-*)',
  vpn: 'Полный VPN (vpn-*)',
  raw: 'Сырой IP хоста (без WARP)',
}

export default function WarpGeoPage() {
  const [nodes, setNodes] = useState<WarpGeoNodesResponse['nodes']>([])
  const [nodeId, setNodeId] = useState<number | null>(null)
  const [status, setStatus] = useState<WarpGeoStatusResponse | null>(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [checkResults, setCheckResults] = useState<Partial<Record<'antizapret' | 'vpn' | 'raw', WarpGeoCheckResponse>>>({})
  const [checking, setChecking] = useState<string | null>(null)
  const [protonFieldsDraft, setProtonFieldsDraft] = useState<{ antizapret: ProtonFields; vpn: ProtonFields }>({
    antizapret: EMPTY_PROTON_FIELDS,
    vpn: EMPTY_PROTON_FIELDS,
  })
  const [savingScope, setSavingScope] = useState<'antizapret' | 'vpn' | null>(null)
  const [manageError, setManageError] = useState<string | null>(null)
  const [manageMessage, setManageMessage] = useState<string | null>(null)
  const [switchingProvider, setSwitchingProvider] = useState(false)
  const [switchingMode, setSwitchingMode] = useState<'antizapret' | 'vpn' | null>(null)
  const [modeDraft, setModeDraft] = useState<{ scope: 'antizapret' | 'vpn'; value: string } | null>(null)
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
          setProtonFieldsDraft({
            antizapret: { ...EMPTY_PROTON_FIELDS, ...data.proton_antizapret_fields },
            vpn: { ...EMPTY_PROTON_FIELDS, ...data.proton_vpn_fields },
          })
          runAllChecks(id)
        })
        .catch((err) => setStatusError(err instanceof Error ? err.message : 'Не удалось загрузить статус WARP'))
        .finally(() => setStatusLoading(false))
    },
    [runAllChecks],
  )

  useEffect(() => {
    if (nodeId !== null) loadStatus(nodeId)
    setProtonFieldsDraft({ antizapret: EMPTY_PROTON_FIELDS, vpn: EMPTY_PROTON_FIELDS })
    setManageError(null)
    setManageMessage(null)
    setExpandedProtonScope(null)
    setPreviewResult(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId])

  const handleSaveProtonFields = async (scope: 'antizapret' | 'vpn') => {
    if (nodeId === null) return
    const fields = protonFieldsDraft[scope]
    if (!fields.public_key.trim() || !fields.address.trim() || !fields.endpoint_host.trim() || !fields.endpoint_port.trim()) {
      return
    }
    setSavingScope(scope)
    setManageError(null)
    setManageMessage(null)
    try {
      await saveWarpProtonFields(nodeId, scope, fields)
      // Сохранённый в setup конфиг без up.sh ничего не меняет на узле - применяем сразу
      const result = await applyWarpChanges(nodeId)
      setManageMessage(
        result.success
          ? 'Конфиг сохранён и применён: туннель поднят с новым сервером Proton.'
          : `Конфиг сохранён, но up.sh завершился с ошибкой: ${result.output}`,
      )
      loadStatus(nodeId)
    } catch (err) {
      setManageError(err instanceof Error ? err.message : 'Не удалось сохранить конфиг')
    } finally {
      setSavingScope(null)
    }
  }

  const updateProtonField = (scope: 'antizapret' | 'vpn', field: keyof ProtonFields, value: string) => {
    setProtonFieldsDraft((prev) => ({ ...prev, [scope]: { ...prev[scope], [field]: value } }))
  }

  const handleSwitchProvider = async (provider: 'proton' | 'cloudflare') => {
    if (nodeId === null || status?.warp_provider === provider) return
    setSwitchingProvider(true)
    setManageError(null)
    setManageMessage(null)
    try {
      await setWarpProvider(nodeId, provider)
      const result = await applyWarpChanges(nodeId)
      setManageMessage(
        result.success
          ? 'Провайдер сохранён и применён: туннели подняты заново.'
          : `Провайдер записан, но up.sh завершился с ошибкой: ${result.output}`,
      )
      loadStatus(nodeId)
    } catch (err) {
      setManageError(err instanceof Error ? err.message : 'Не удалось сменить провайдера')
    } finally {
      setSwitchingProvider(false)
    }
  }

  // Выбор режима только открывает подтверждение: запись в setup без up.sh ничего не меняет на узле.
  const handleSwitchMode = (scope: 'antizapret' | 'vpn', value: string) => {
    const current = scope === 'antizapret' ? status?.antizapret_warp : status?.vpn_warp
    if (nodeId === null || (current === value && !isScopePending(status?.pending_scopes, scope))) return
    setModeDraft({ scope, value })
  }

  const confirmModeSwitch = async () => {
    if (nodeId === null || modeDraft === null) return
    const { scope, value } = modeDraft
    setSwitchingMode(scope)
    setManageError(null)
    setManageMessage(null)
    try {
      await setWarpModes(nodeId, scope === 'antizapret' ? { antizapret: value } : { vpn: value })
      const result = await applyWarpChanges(nodeId)
      setManageMessage(
        result.success
          ? 'Режим сохранён и применён: правила AmneziaWG, WireGuard и OpenVPN пересобраны, туннели подняты заново.'
          : `Режим записан, но up.sh завершился с ошибкой: ${result.output}`,
      )
      setModeDraft(null)
      loadStatus(nodeId)
    } catch (err) {
      setManageError(err instanceof Error ? err.message : 'Не удалось сменить режим WARP')
    } finally {
      setSwitchingMode(null)
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
          <h1 className="text-2xl font-semibold tracking-tight">WARP Geolocation</h1>
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
                <div className="font-medium">{warpModeLabel(ANTIZAPRET_WARP_OPTIONS, status.antizapret_warp)}</div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">WARP для полного VPN</div>
                <div className="font-medium">{warpModeLabel(VPN_WARP_OPTIONS, status.vpn_warp)}</div>
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

          <div className="flex flex-col gap-3 rounded-md border p-3">
            <div className="text-sm font-medium">Режим WARP</div>
            {(
              [
                { scope: 'antizapret' as const, title: 'AntiZapret VPN', options: ANTIZAPRET_WARP_OPTIONS, current: status?.antizapret_warp },
                { scope: 'vpn' as const, title: 'Полный VPN', options: VPN_WARP_OPTIONS, current: status?.vpn_warp },
              ]
            ).map(({ scope, title, options, current }) => (
              <div key={scope} className="flex flex-col gap-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="w-28 shrink-0 text-sm text-muted-foreground">{title}:</span>
                  {isScopePending(status?.pending_scopes, scope) && (
                    <Badge variant="outline" className="border-amber-500 text-amber-700 dark:text-amber-400" title="Режим записан в setup, но правила узла ещё от прежнего. Выберите режим ещё раз или нажмите «Применить».">
                      не применено
                    </Badge>
                  )}
                  {options.map((option) => (
                    <Button
                      key={option.value}
                      size="sm"
                      variant={current === option.value ? 'default' : 'outline'}
                      title={option.hint}
                      disabled={nodeId === null || switchingMode !== null}
                      onClick={() => handleSwitchMode(scope, option.value)}
                    >
                      {option.label}
                    </Button>
                  ))}
                  {switchingMode === scope && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
                </div>
                <p className="pl-0 text-xs text-muted-foreground sm:pl-[7.5rem]">
                  {options.find((option) => option.value === current)?.hint ?? 'Режим не распознан.'}
                </p>
                {scope === 'antizapret' && warpModeUsesList(current) && (
                  <p className="text-xs text-amber-700 dark:text-amber-400 sm:pl-[7.5rem]">
                    Режим использует список WARP: домены добавляются во вкладке «Редактор файлов → WARP» и должны быть
                    также в «Включить домены», иначе они не получат fake-IP и в WARP не попадут.
                  </p>
                )}
              </div>
            ))}
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
                Проверяем (с повторами на нестабильном временном тоннеле, при неудаче — с полной
                перерегистрацией нового пира) — обычно несколько секунд, иногда до полутора минут…
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
                      <p className="text-xs text-muted-foreground">
                        Значения — из <code className="font-mono">setup</code> на узле, те же поля, что в
                        WireGuard-конфиге из личного кабинета Proton VPN. Правьте точечно (например только
                        порт) — остальное менять не нужно.
                      </p>
                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <label className="flex flex-col gap-1 text-xs">
                          PublicKey
                          <Input
                            value={protonFieldsDraft[scope].public_key}
                            onChange={(e) => updateProtonField(scope, 'public_key', e.target.value)}
                            placeholder="base64-ключ"
                            className="font-mono text-xs"
                          />
                        </label>
                        <label className="flex flex-col gap-1 text-xs">
                          Address
                          <Input
                            value={protonFieldsDraft[scope].address}
                            onChange={(e) => updateProtonField(scope, 'address', e.target.value)}
                            placeholder="10.2.0.2"
                            className="font-mono text-xs"
                          />
                        </label>
                        <label className="flex flex-col gap-1 text-xs">
                          Endpoint host
                          <Input
                            value={protonFieldsDraft[scope].endpoint_host}
                            onChange={(e) => updateProtonField(scope, 'endpoint_host', e.target.value)}
                            placeholder="146.70.xxx.xxx"
                            className="font-mono text-xs"
                          />
                        </label>
                        <label className="flex flex-col gap-1 text-xs">
                          Endpoint port
                          <Input
                            value={protonFieldsDraft[scope].endpoint_port}
                            onChange={(e) => updateProtonField(scope, 'endpoint_port', e.target.value)}
                            placeholder="51820"
                            className="font-mono text-xs"
                          />
                        </label>
                        <label className="flex flex-col gap-1 text-xs sm:col-span-2">
                          PrivateKey
                          <Input
                            value={protonFieldsDraft[scope].private_key}
                            onChange={(e) => updateProtonField(scope, 'private_key', e.target.value)}
                            placeholder={configured ? 'оставьте пустым, чтобы не менять текущий' : 'base64-ключ'}
                            className="font-mono text-xs"
                            type="password"
                          />
                        </label>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        className="self-start"
                        disabled={
                          nodeId === null ||
                          savingScope === scope ||
                          !protonFieldsDraft[scope].public_key.trim() ||
                          !protonFieldsDraft[scope].address.trim() ||
                          !protonFieldsDraft[scope].endpoint_host.trim() ||
                          !protonFieldsDraft[scope].endpoint_port.trim() ||
                          (!configured && !protonFieldsDraft[scope].private_key.trim())
                        }
                        onClick={() => handleSaveProtonFields(scope)}
                      >
                        {savingScope === scope ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Сохранить и применить'}
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
                Смена провайдера, режима WARP{status?.warp_provider === 'proton' ? ' и сохранение ключей Proton' : ''}{' '}
                применяются сразу: на узле выполняется <code className="font-mono">up.sh</code>, который кратко (на
                секунды) обрывает ВСЕ активные туннели на этом сервере, не только WARP. «Применить» — повторить вручную.
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
          {modeDraft && (
            <ConfirmDialog
              open
              onOpenChange={(open) => {
                if (!open && switchingMode === null) setModeDraft(null)
              }}
              title={describeModeSwitch(
                modeDraft.scope === 'antizapret' ? 'AntiZapret VPN' : 'Полный VPN',
                modeDraft.scope === 'antizapret' ? ANTIZAPRET_WARP_OPTIONS : VPN_WARP_OPTIONS,
                modeDraft.scope === 'antizapret' ? status?.antizapret_warp : status?.vpn_warp,
                modeDraft.value,
              ).title}
              description={
                <>
                  <strong>
                    {describeModeSwitch(
                      modeDraft.scope === 'antizapret' ? 'AntiZapret VPN' : 'Полный VPN',
                      modeDraft.scope === 'antizapret' ? ANTIZAPRET_WARP_OPTIONS : VPN_WARP_OPTIONS,
                      modeDraft.scope === 'antizapret' ? status?.antizapret_warp : status?.vpn_warp,
                      modeDraft.value,
                    ).summary}
                  </strong>
                  <br />
                  Режим будет записан в setup узла и сразу применён: выполняется <code className="font-mono">up.sh</code>,
                  правила WireGuard, AmneziaWG 1.5, 2.0 и 3.1 пересобираются под новый режим.
                </>
              }
              alert={{
                variant: 'warning',
                title: 'Туннели узла прервутся на несколько секунд',
                children: 'Все активные подключения на этом сервере (OpenVPN, WireGuard, AmneziaWG) переподключатся.',
              }}
              confirmLabel="Сохранить и применить"
              cancelLabel="Отмена"
              destructive
              loading={switchingMode !== null}
              onConfirm={confirmModeSwitch}
            />
          )}
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

      <WarpDnsCard nodeId={nodeId} />
    </div>
  )
}
