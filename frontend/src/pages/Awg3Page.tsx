import { useCallback, useEffect, useState } from 'react'
import {
  createAwg3Client,
  deleteAwg3Client,
  getAwg3ClientConfig,
  getAwg3Health,
  getAwg3Monitoring,
  listAwg3Clients,
} from '@/api/client'
import SettingsAlert from '@/components/settings/SettingsAlert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import type { Awg3Client, Awg3HealthResponse, Awg3MonitoringResponse } from '@/types'

function formatHandshake(ts: number): string {
  if (!ts) return 'не было'
  const age = Math.floor(Date.now() / 1000) - ts
  if (age < 180) return `${age} с назад`
  return new Date(ts * 1000).toLocaleString('ru-RU')
}

function downloadText(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export default function Awg3Page() {
  const [health, setHealth] = useState<Awg3HealthResponse | null>(null)
  const [monitoring, setMonitoring] = useState<Awg3MonitoringResponse | null>(null)
  const [clients, setClients] = useState<Awg3Client[]>([])
  const [newName, setNewName] = useState('')
  const [newMode, setNewMode] = useState<'split' | 'full'>('split')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [h, m, c] = await Promise.all([getAwg3Health(), getAwg3Monitoring(), listAwg3Clients()])
      setHealth(h)
      setMonitoring(m)
      setClients(c.items)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось загрузить AmneziaWG 3.0')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const handshakeFor = (publicKey: string): number => {
    if (!monitoring) return 0
    for (const iface of Object.values(monitoring.ifaces)) {
      const peer = iface.peers.find((p) => p.public_key === publicKey)
      if (peer) return peer.latest_handshake
    }
    return 0
  }

  const onCreate = async () => {
    const name = newName.trim()
    if (!name) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const created = await createAwg3Client(name, newMode)
      setNewName('')
      setNotice(`Клиент «${created.name}» создан, IP ${created.ip}. Скачайте конфиг.`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось создать клиента')
    } finally {
      setBusy(false)
    }
  }

  const onDownload = async (name: string) => {
    setError(null)
    try {
      const res = await getAwg3ClientConfig(name)
      downloadText(res.filename, res.config)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось получить конфиг')
    }
  }

  const onDelete = async (name: string) => {
    if (!window.confirm(`Удалить клиента «${name}»?`)) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await deleteAwg3Client(name)
      setNotice(`Клиент «${name}» удалён.`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось удалить клиента')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">AmneziaWG 3.0</h1>
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
          Обновить
        </Button>
      </div>

      {error && <SettingsAlert variant="danger">{error}</SettingsAlert>}
      {notice && <SettingsAlert variant="info">{notice}</SettingsAlert>}

      {health && (
        <section className="grid gap-3 sm:grid-cols-2">
          <Card><CardContent className="p-4">
            <div className="text-sm text-muted-foreground">Инструменты awg</div>
            <div>{health.tools_present ? 'установлены' : 'не найдены'}</div>
          </CardContent></Card>
          <Card><CardContent className="p-4">
            <div className="text-sm text-muted-foreground">amneziawg-go (userspace)</div>
            <div>{health.userspace_present ? 'установлен' : 'не найден'}</div>
          </CardContent></Card>
        </section>
      )}

      {health && (
        <section className="space-y-2">
          <h2 className="text-lg font-medium">Интерфейсы</h2>
          {health.ifaces.map((iface) => (
            <Card key={iface.name}><CardContent className="flex flex-wrap items-center gap-4 p-4">
              <div className="font-mono">{iface.name}</div>
              <div className="text-sm">UDP {iface.port}</div>
              <div className="text-sm">{iface.subnet}</div>
              <div className="text-sm">{iface.conf_present ? 'конфиг есть' : 'конфига нет'}</div>
              <Badge variant={iface.up ? 'default' : 'secondary'}>
                {iface.up ? 'поднят' : 'выключен'}
              </Badge>
            </CardContent></Card>
          ))}
        </section>
      )}

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Клиенты</h2>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col text-sm">
            Имя клиента
            <Input
              className="mt-1 w-64"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="например, router-cudy"
              maxLength={32}
            />
          </label>
          <label className="flex flex-col text-sm">
            Режим
            <select
              className="mt-1 h-10 rounded-md border border-input bg-background px-3"
              value={newMode}
              onChange={(e) => setNewMode(e.target.value as 'split' | 'full')}
            >
              <option value="split">Антизапрет (только заблокированное)</option>
              <option value="full">Полный VPN (весь трафик)</option>
            </select>
          </label>
          <Button onClick={() => void onCreate()} disabled={busy || !newName.trim()}>
            Создать клиента
          </Button>
        </div>

        {clients.length === 0 && !loading && <div className="text-sm text-muted-foreground">Клиентов AWG 3.0 пока нет.</div>}

        {clients.map((c) => (
          <Card key={c.name}><CardContent className="flex flex-wrap items-center gap-4 p-4 text-sm">
            <span className="font-medium">{c.name}</span>
            <Badge variant="outline">{c.mode === 'full' ? 'полный VPN' : 'антизапрет'}</Badge>
            <span className="font-mono">{c.ip}</span>
            <span>handshake: {formatHandshake(handshakeFor(c.public_key))}</span>
            <Button variant="outline" size="sm" onClick={() => void onDownload(c.name)}>
              Скачать .conf
            </Button>
            <Button variant="destructive" size="sm" onClick={() => void onDelete(c.name)} disabled={busy}>
              Удалить
            </Button>
          </CardContent></Card>
        ))}
      </section>
    </div>
  )
}
