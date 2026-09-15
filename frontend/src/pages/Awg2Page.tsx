import { useCallback, useEffect, useState } from 'react'
import { CloudOff } from 'lucide-react'
import { getAwg2Health, getAwg2Monitoring, getAwg2MonitoringAll } from '@/api/client'
import Awg2ClientsTable from '@/components/awg2/Awg2ClientsTable'
import Awg2HelpStub from '@/components/awg2/Awg2HelpStub'
import Awg2Hero from '@/components/awg2/Awg2Hero'
import Awg2OverviewCards from '@/components/awg2/Awg2OverviewCards'
import { formatAwg2NodeLabel } from '@/components/awg2/utils'
import EmptyState from '@/components/ui/EmptyState'
import SettingsAlert from '@/components/settings/SettingsAlert'
import { useNode } from '@/context/NodeContext'
import type { Awg2HealthResponse, Awg2MonitoringAllResponse, Awg2MonitoringResponse } from '@/types'

type ViewMode = 'current' | 'all'

export default function Awg2Page() {
  const { activeNode } = useNode()
  const [health, setHealth] = useState<Awg2HealthResponse | null>(null)
  const [monitoring, setMonitoring] = useState<Awg2MonitoringResponse | null>(null)
  const [monitoringAll, setMonitoringAll] = useState<Awg2MonitoringAllResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingAll, setLoadingAll] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('current')

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const healthData = await getAwg2Health()
      setHealth(healthData)
      if (healthData.installed) {
        const monitoringData = await getAwg2Monitoring().catch(() => null)
        setMonitoring(monitoringData)
      } else {
        setMonitoring(null)
      }
    } catch (err) {
      setHealth(null)
      setMonitoring(null)
      setLoadError(err instanceof Error ? err.message : 'Не удалось загрузить AmneziaWG 2.0')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadAll = useCallback(async () => {
    setLoadingAll(true)
    try {
      setMonitoringAll(await getAwg2MonitoringAll())
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Не удалось загрузить сводный список узлов')
    } finally {
      setLoadingAll(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load, activeNode?.id])

  useEffect(() => {
    if (viewMode === 'all' && !monitoringAll) {
      void loadAll()
    }
  }, [viewMode, monitoringAll, loadAll])

  const nodeLabel = formatAwg2NodeLabel(health, activeNode)
  const ready = Boolean(health?.installed)

  const combinedRows =
    monitoringAll?.nodes.flatMap((n) => n.clients.map((c) => ({ ...c, nodeName: n.node_name }))) ?? []

  return (
    <div className="space-y-6">
      <Awg2Hero health={health} loading={loading} nodeLabel={nodeLabel} onRefresh={() => void load()} />

      {loadError && (
        <SettingsAlert variant="danger" title="Ошибка загрузки">
          {loadError}
        </SettingsAlert>
      )}

      <div className="flex overflow-hidden rounded-md border text-xs w-fit">
        <button
          type="button"
          onClick={() => setViewMode('current')}
          className={`px-3 py-1.5 transition-colors ${
            viewMode === 'current' ? 'bg-primary text-primary-foreground' : 'bg-card hover:bg-muted/60'
          }`}
        >
          Текущий узел
        </button>
        <button
          type="button"
          onClick={() => {
            setViewMode('all')
            void loadAll()
          }}
          className={`px-3 py-1.5 transition-colors ${
            viewMode === 'all' ? 'bg-primary text-primary-foreground' : 'bg-card hover:bg-muted/60'
          }`}
        >
          Все узлы
        </button>
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
              <Awg2ClientsTable monitoring={null} rows={combinedRows} />
            </>
          )}
        </div>
      ) : ready ? (
        <div className="space-y-4">
          <Awg2OverviewCards health={health} monitoring={monitoring} loading={loading} />
          <Awg2ClientsTable monitoring={monitoring} />
          <Awg2HelpStub />
        </div>
      ) : !loading ? (
        <div className="rounded-xl border bg-card/50 p-6">
          <EmptyState
            icon={CloudOff}
            title="Нативный AmneziaWG 2.0 не найден"
            description={`На узле ${nodeLabel} не найден бинарь awg. Пересоберите его через setup.sh (amneziawg-go + amneziawg-tools) по SSH — из панели это недоступно, поскольку это часть базового VPN-стека.`}
          />
        </div>
      ) : null}
    </div>
  )
}
