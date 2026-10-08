import { useCallback, useEffect, useState } from 'react'
import { Loader2, Send } from 'lucide-react'
import { Navigate } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import MiniPageHeader from '@/tg-mini/components/MiniPageHeader'
import { getTgMtproxyStatus } from '@/tg-mini/api'
import { useTgAuth } from '@/tg-mini/context/TgAuthContext'
import type { TgMiniMtproxyNode } from '@/types'

function availabilityBadge(pct: number | null | undefined) {
  if (pct == null) return <Badge variant="secondary">нет данных</Badge>
  const variant = pct >= 80 ? 'success' : pct >= 50 ? 'warning' : 'destructive'
  return <Badge variant={variant}>{pct}% из России</Badge>
}

/** MTProxy (MTProxyL) на всех узлах: работает ли, домен, подключения и доступность из России. */
export default function Mtproxy() {
  const { isAdmin } = useTgAuth()
  const [nodes, setNodes] = useState<TgMiniMtproxyNode[] | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setRefreshing(true)
    setError(null)
    try {
      setNodes((await getTgMtproxyStatus()).nodes)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Ошибка загрузки')
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    if (isAdmin) void load()
  }, [isAdmin, load])

  if (!isAdmin) return <Navigate to="/" replace />

  return (
    <div className="tg-mini-dashboard space-y-4">
      <MiniPageHeader
        title="MTProxy"
        subtitle="MTProxyL на всех узлах (только просмотр)"
        onRefresh={() => void load()}
        refreshing={refreshing}
      />

      {error && (
        <div className="tg-mini-inline-alert" role="alert">
          {error}
          <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => void load()}>
            Повторить
          </Button>
        </div>
      )}

      {nodes === null && !error && (
        <div className="tg-mini-center py-6">
          <Loader2 size={20} className="animate-spin text-muted-foreground" aria-hidden />
        </div>
      )}

      {nodes !== null && nodes.length === 0 && (
        <p className="text-sm text-muted-foreground">
          MTProxyL не найден ни на одном узле (или агенты узлов не обновлены).
        </p>
      )}

      {nodes?.map((node) => (
        <Card key={node.node_id}>
          <CardContent className="space-y-2 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Send size={16} className="text-muted-foreground" aria-hidden />
              <span className="font-semibold">{node.node_name}</span>
              <Badge variant={node.running ? 'success' : 'destructive'}>
                {node.running ? 'работает' : node.status || 'остановлен'}
              </Badge>
              {availabilityBadge(node.availability?.percentage)}
            </div>
            <p className="text-xs text-muted-foreground">
              {[
                node.domain && `домен ${node.domain}`,
                node.port &&
                  (node.public_port && node.public_port !== node.port
                    ? `порт ${node.public_port} (слушает ${node.port})`
                    : `порт ${node.port}`),
                node.connections != null && `подключений ${node.connections}`,
                node.version && `v${node.version}`,
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
            {(node.users?.length ?? 0) > 0 && (
              <p className="text-xs">
                👥 онлайн {node.users!.filter((u) => u.connections > 0).length} из {node.users!.length}
                {node.users!.some((u) => u.connections > 0) &&
                  `: ${node.users!
                    .filter((u) => u.connections > 0)
                    .sort((a, b) => b.connections - a.connections)
                    .map((u) => `${u.label} (${u.connections})`)
                    .join(', ')}`}
              </p>
            )}
            {node.users
              ?.filter((u) => u.quota_pct != null && u.quota_pct >= 90)
              .map((u) => (
                <p key={u.label} className="text-xs text-destructive">
                  {(u.quota_pct ?? 0) >= 100 ? '⛔' : '⚠️'} {u.label}: {u.quota_pct}% квоты
                </p>
              ))}
            {node.error && <p className="text-xs text-destructive">{node.error}</p>}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
