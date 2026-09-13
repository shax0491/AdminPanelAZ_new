import { useCallback, useEffect, useState } from 'react'
import { CloudOff } from 'lucide-react'
import { getAwg2Health, getAwg2Monitoring } from '@/api/client'
import Awg2ClientsTable from '@/components/awg2/Awg2ClientsTable'
import Awg2HelpStub from '@/components/awg2/Awg2HelpStub'
import Awg2Hero from '@/components/awg2/Awg2Hero'
import Awg2OverviewCards from '@/components/awg2/Awg2OverviewCards'
import { formatAwg2NodeLabel } from '@/components/awg2/utils'
import EmptyState from '@/components/ui/EmptyState'
import SettingsAlert from '@/components/settings/SettingsAlert'
import { useNode } from '@/context/NodeContext'
import type { Awg2HealthResponse, Awg2MonitoringResponse } from '@/types'

export default function Awg2Page() {
  const { activeNode } = useNode()
  const [health, setHealth] = useState<Awg2HealthResponse | null>(null)
  const [monitoring, setMonitoring] = useState<Awg2MonitoringResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

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

  useEffect(() => {
    void load()
  }, [load, activeNode?.id])

  const nodeLabel = formatAwg2NodeLabel(health, activeNode)
  const ready = Boolean(health?.installed)

  return (
    <div className="space-y-6">
      <Awg2Hero health={health} loading={loading} nodeLabel={nodeLabel} onRefresh={() => void load()} />

      {loadError && (
        <SettingsAlert variant="danger" title="Ошибка загрузки">
          {loadError}
        </SettingsAlert>
      )}

      {ready && <Awg2OverviewCards health={health} monitoring={monitoring} loading={loading} />}

      {ready ? (
        <div className="space-y-4">
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
