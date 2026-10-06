import { useCallback, useEffect, useState } from 'react'
import { getAwg3Health, getAwg3Monitoring } from '@/api/client'
import SettingsAlert from '@/components/settings/SettingsAlert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import type { Awg3HealthResponse, Awg3MonitoringResponse } from '@/types'

function formatHandshake(ts: number): string {
  if (!ts) return 'не было'
  const age = Math.floor(Date.now() / 1000) - ts
  if (age < 180) return `${age} с назад`
  return new Date(ts * 1000).toLocaleString('ru-RU')
}

export default function Awg3Page() {
  const [health, setHealth] = useState<Awg3HealthResponse | null>(null)
  const [monitoring, setMonitoring] = useState<Awg3MonitoringResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [h, m] = await Promise.all([getAwg3Health(), getAwg3Monitoring()])
      setHealth(h)
      setMonitoring(m)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить AmneziaWG 3.1')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">AmneziaWG 3.1</h1>
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
          Обновить
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        Статус и мониторинг интерфейса awg1. Клиентов AWG 3.1 создают на странице «Клиенты» (протокол AmneziaWG 3.1, режим антизапрет или полный VPN).
      </p>

      {error && <SettingsAlert variant="danger">{error}</SettingsAlert>}

      {health && (
        <section className="grid gap-3 sm:grid-cols-2">
          <Card>
            <CardContent className="p-4">
              <div className="text-sm text-muted-foreground">Инструменты awg</div>
              <div>{health.tools_present ? 'установлены' : 'не найдены'}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="text-sm text-muted-foreground">amneziawg-go (userspace)</div>
              <div>{health.userspace_present ? 'установлен' : 'не найден'}</div>
            </CardContent>
          </Card>
        </section>
      )}

      {health && (
        <section className="space-y-2">
          <h2 className="text-lg font-medium">Интерфейсы</h2>
          {health.ifaces.map((iface) => (
            <Card key={iface.name}>
              <CardContent className="flex flex-wrap items-center gap-4 p-4">
                <div className="font-mono">{iface.name}</div>
                <div className="text-sm">UDP {iface.port}</div>
                <div className="text-sm">{iface.subnet}</div>
                <Badge variant={iface.up ? 'default' : 'secondary'}>{iface.up ? 'поднят' : 'выключен'}</Badge>
              </CardContent>
            </Card>
          ))}
        </section>
      )}

      {monitoring && (
        <section className="space-y-2">
          <h2 className="text-lg font-medium">Пиры</h2>
          {Object.values(monitoring.ifaces).every((i) => i.peers.length === 0) && !loading && (
            <div className="text-sm text-muted-foreground">Клиентов AWG 3.1 пока нет.</div>
          )}
          {Object.values(monitoring.ifaces).flatMap((iface) =>
            iface.peers.map((peer) => (
              <Card key={`${iface.name}-${peer.public_key}`}>
                <CardContent className="flex flex-wrap gap-4 p-4 text-sm">
                  <span className="font-mono">{iface.name}</span>
                  <span className="font-mono">{peer.allowed_ips}</span>
                  <span>handshake: {formatHandshake(peer.latest_handshake)}</span>
                  <span>rx {peer.rx} / tx {peer.tx}</span>
                </CardContent>
              </Card>
            )),
          )}
        </section>
      )}
    </div>
  )
}
