import { useCallback, useEffect, useState } from 'react'
import { CloudOff } from 'lucide-react'
import { getAwg3Health, getAwg3Monitoring, getAwg3MonitoringAll } from '@/api/client'
import Awg2ClientsTable from '@/components/awg2/Awg2ClientsTable'
import Awg2Hero from '@/components/awg2/Awg2Hero'
import Awg2OverviewCards from '@/components/awg2/Awg2OverviewCards'
import Awg3HelpStub from '@/components/awg2/Awg3HelpStub'
import { formatAwg2NodeLabel } from '@/components/awg2/utils'
import EmptyState from '@/components/ui/EmptyState'
import SettingsAlert from '@/components/settings/SettingsAlert'
import { useNode } from '@/context/NodeContext'
import type { Awg3HealthResponse, Awg3MonitoringAllResponse, Awg3MonitoringResponse } from '@/types'

type ViewMode = 'current' | 'all'

/** AmneziaWG 3 page: same structure, cards, table and filters as the AmneziaWG 2 page. */
export default function Awg3Page() {
  const { activeNode, loading: nodeLoading } = useNode()
  const [health, setHealth] = useState<Awg3HealthResponse | null>(null)
  const [monitoring, setMonitoring] = useState<Awg3MonitoringResponse | null>(null)
  const [monitoringAll, setMonitoringAll] = useState<Awg3MonitoringAllResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingAll, setLoadingAll] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('current')

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const healthData = await getAwg3Health()
      setHealth(healthData)
      if (healthData.tools_present && healthData.userspace_present) {
        setMonitoring(await getAwg3Monitoring().catch(() => null))
      } else {
        setMonitoring(null)
      }
    } catch (err) {
      setHealth(null)
      setMonitoring(null)
      setLoadError(err instanceof Error ? err.message : 'Не удалось загрузить AmneziaWG 3')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadAll = useCallback(async () => {
    setLoadingAll(true)
    try {
      setMonitoringAll(await getAwg3MonitoringAll())
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Не удалось загрузить сводный список узлов')
    } finally {
      setLoadingAll(false)
    }
  }, [])

  useEffect(() => {
    if (nodeLoading) return
    void load()
  }, [load, nodeLoading, activeNode?.id])

  useEffect(() => {
    if (viewMode === 'all' && !monitoringAll) {
      void loadAll()
    }
  }, [viewMode, monitoringAll, loadAll])

  const nodeLabel = formatAwg2NodeLabel(null, activeNode)
  const ready = Boolean(health?.tools_present && health?.userspace_present)

  const combinedRows =
    monitoringAll?.nodes.flatMap((n) => n.clients.map((c) => ({ ...c, nodeName: n.node_name }))) ?? []

  return (
    <div className="space-y-6">
      <Awg2Hero
        health={health}
        loading={loading}
        nodeLabel={nodeLabel}
        onRefresh={() => void load()}
        variant="awg3"
      />

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
              <Awg2ClientsTable monitoring={null} rows={combinedRows} variant="awg3" />
            </>
          )}
        </div>
      ) : ready ? (
        <div className="space-y-4">
          <Awg2OverviewCards health={health} monitoring={monitoring} loading={loading} variant="awg3" />
          <Awg2ClientsTable monitoring={monitoring} variant="awg3" />
          <Awg3HelpStub />
        </div>
      ) : !loading ? (
        <div className="rounded-xl border bg-card/50 p-6">
          <EmptyState
            icon={CloudOff}
            title="AmneziaWG 3 не установлен на узле"
            description={`На узле ${nodeLabel} не найдены awg и amneziawg-go. Установка и переустановка — через setup.sh по SSH на сервере.`}
          />
        </div>
      ) : null}
    </div>
  )
}
