import { useCallback, useEffect, useState } from 'react'
import { CloudOff } from 'lucide-react'
import {
  getAwg2Health,
  getAwg2Monitoring,
  getAwg2MonitoringAll,
  getAwg3Health,
  getAwg3Monitoring,
  getAwg3MonitoringAll,
} from '@/api/client'
import Awg2ClientsTable from '@/components/awg2/Awg2ClientsTable'
import Awg2HelpStub from '@/components/awg2/Awg2HelpStub'
import Awg2Hero from '@/components/awg2/Awg2Hero'
import Awg2OverviewCards from '@/components/awg2/Awg2OverviewCards'
import Awg3HelpStub from '@/components/awg2/Awg3HelpStub'
import { formatAwg2NodeLabel } from '@/components/awg2/utils'
import EmptyState from '@/components/ui/EmptyState'
import SettingsAlert from '@/components/settings/SettingsAlert'
import { useNode } from '@/context/NodeContext'
import type {
  Awg2HealthResponse,
  Awg2MonitoringAllResponse,
  Awg2MonitoringResponse,
  Awg3HealthResponse,
  Awg3MonitoringAllResponse,
  Awg3MonitoringResponse,
} from '@/types'

type ViewMode = 'current' | 'all'
type Health = Awg2HealthResponse | Awg3HealthResponse
type Monitoring = Awg2MonitoringResponse | Awg3MonitoringResponse
type MonitoringAll = Awg2MonitoringAllResponse | Awg3MonitoringAllResponse

function isReady(version: 2 | 3, health: Health | null): boolean {
  if (!health) return false
  if (version === 2) return Boolean((health as Awg2HealthResponse).installed)
  const h3 = health as Awg3HealthResponse
  return Boolean(h3.tools_present && h3.userspace_present)
}

/** Live AmneziaWG 2/3 state (peers, handshakes) of the current node or all nodes; part of the Traffic page. */
export default function AwgMonitorView({ version }: { version: 2 | 3 }) {
  const { activeNode, loading: nodeLoading } = useNode()
  const [health, setHealth] = useState<Health | null>(null)
  const [monitoring, setMonitoring] = useState<Monitoring | null>(null)
  const [monitoringAll, setMonitoringAll] = useState<MonitoringAll | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingAll, setLoadingAll] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('current')
  const variant = version === 3 ? 'awg3' : undefined

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const healthData: Health = version === 3 ? await getAwg3Health() : await getAwg2Health()
      setHealth(healthData)
      if (isReady(version, healthData)) {
        const fetchMonitoring = version === 3 ? getAwg3Monitoring : getAwg2Monitoring
        setMonitoring(await fetchMonitoring().catch(() => null))
      } else {
        setMonitoring(null)
      }
    } catch (err) {
      setHealth(null)
      setMonitoring(null)
      setLoadError(err instanceof Error ? err.message : `Не удалось загрузить AmneziaWG ${version}`)
    } finally {
      setLoading(false)
    }
  }, [version])

  const loadAll = useCallback(async () => {
    setLoadingAll(true)
    try {
      setMonitoringAll(version === 3 ? await getAwg3MonitoringAll() : await getAwg2MonitoringAll())
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Не удалось загрузить сводный список узлов')
    } finally {
      setLoadingAll(false)
    }
  }, [version])

  useEffect(() => {
    setMonitoringAll(null)
  }, [version])

  useEffect(() => {
    if (nodeLoading) return
    void load()
  }, [load, nodeLoading, activeNode?.id])

  useEffect(() => {
    if (viewMode === 'all' && !monitoringAll) {
      void loadAll()
    }
  }, [viewMode, monitoringAll, loadAll])

  const nodeLabel = formatAwg2NodeLabel(version === 2 ? (health as Awg2HealthResponse | null) : null, activeNode)
  const ready = isReady(version, health)

  const combinedRows =
    monitoringAll?.nodes.flatMap((n) => n.clients.map((c) => ({ ...c, nodeName: n.node_name }))) ?? []

  return (
    <div className="space-y-6">
      <Awg2Hero
        health={health}
        loading={loading}
        nodeLabel={nodeLabel}
        onRefresh={() => void (viewMode === 'all' ? loadAll() : load())}
        variant={variant}
      />

      {loadError && (
        <SettingsAlert variant="danger" title="Ошибка загрузки">
          {loadError}
        </SettingsAlert>
      )}

      <div className="flex overflow-hidden rounded-md border text-xs w-fit">
        {(['current', 'all'] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => {
              setViewMode(mode)
              if (mode === 'all') void loadAll()
            }}
            className={`px-3 py-1.5 transition-colors ${
              viewMode === mode ? 'bg-primary text-primary-foreground' : 'bg-card hover:bg-muted/60'
            }`}
          >
            {mode === 'current' ? 'Текущий узел' : 'Все узлы'}
          </button>
        ))}
      </div>

      {viewMode === 'all' ? (
        <div className="space-y-4">
          {loadingAll && !monitoringAll ? (
            <p className="text-sm text-muted-foreground">Загрузка со всех узлов…</p>
          ) : (
            <>
              {monitoringAll?.nodes.some((n) => n.error) && (
                <SettingsAlert variant="warning" title="Некоторые узлы недоступны">
                  {monitoringAll.nodes
                    .filter((n) => n.error)
                    .map((n) => `${n.node_name}: ${n.error}`)
                    .join(' · ')}
                </SettingsAlert>
              )}
              <Awg2ClientsTable monitoring={null} rows={combinedRows} variant={variant} />
            </>
          )}
        </div>
      ) : ready ? (
        <div className="space-y-4">
          <Awg2OverviewCards health={health} monitoring={monitoring} loading={loading} variant={variant} />
          <Awg2ClientsTable monitoring={monitoring} variant={variant} />
          {version === 3 ? <Awg3HelpStub /> : <Awg2HelpStub />}
        </div>
      ) : !loading ? (
        <div className="rounded-xl border bg-card/50 p-6">
          <EmptyState
            icon={CloudOff}
            title={`AmneziaWG ${version} не установлен на узле`}
            description={`На узле ${nodeLabel} AmneziaWG ${version} не найден. Установка — через setup.sh --update по SSH (команда есть в «Настройки → Модули»).`}
          />
        </div>
      ) : null}
    </div>
  )
}
