import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Network,
  Puzzle,
  RefreshCw,
  Server,
  Settings2,
} from 'lucide-react'
import { ApiError } from '@/api/client'
import HaReplicaBanner from '@/components/dashboard/HaReplicaBanner'
import { NodeStatusBadge } from '@/components/NodeSelector'
import ProxyNodePanel, { AZ_PROXY_SH_DOCS_URL } from '@/components/nodes/ProxyNodePanel'
import { DOCS } from '@/lib/docsUrls'
import { isProxyNode } from '@/components/nodes/nodeKind'
import ProxyLinkBadge from '@/components/proxy/ProxyLinkBadge'
import RemoteHostsCard from '@/components/proxy/RemoteHostsCard'
import PageSectionHeader from '@/components/shared/PageSectionHeader'
import SettingsAlert from '@/components/settings/SettingsAlert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import EmptyState from '@/components/ui/EmptyState'
import Spinner from '@/components/ui/Spinner'
import { useNode } from '@/context/NodeContext'
import { useNotifications } from '@/context/NotificationContext'
import type { Node, NodeSyncGroup } from '@/types'


const ANTIZAPRET_REMOTES_HASH = encodeURIComponent('section-Адреса подключения')

function DormantProxyCard({
  node,
  nodes,
  syncGroups,
  onUpdated,
}: {
  node: Node
  nodes: Node[]
  syncGroups: NodeSyncGroup[]
  onUpdated: () => Promise<void>
}) {
  const [expanded, setExpanded] = useState(false)
  return (
    <Card>
      <button
        type="button"
        className="flex w-full flex-wrap items-start justify-between gap-3 p-4 text-left"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex min-w-0 items-start gap-2">
          {expanded ? (
            <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base">{node.name}</CardTitle>
              <Badge variant="outline" className="text-[10px]">
                Прокси
              </Badge>
              <ProxyLinkBadge
                linkedVpnNodeId={node.linked_vpn_node_id}
                nodes={nodes}
                syncGroups={syncGroups}
                showUnlinked
              />
              <NodeStatusBadge status={node.status} />
            </div>
            <CardDescription className="font-mono text-xs">
              {node.host}:{node.port}
            </CardDescription>
          </div>
        </div>
      </button>
      {expanded && (
        <CardContent className="pt-0">
          <div className="flex justify-end pb-2">
            <Button type="button" variant="ghost" size="sm" asChild>
              <Link to="/nodes">Открыть на Узлах</Link>
            </Button>
          </div>
          <ProxyNodePanel node={node} nodes={nodes} syncGroups={syncGroups} onUpdated={onUpdated} />
        </CardContent>
      )}
    </Card>
  )
}

const QUICK_LINKS = [
  {
    to: '/nodes',
    label: 'Узлы',
    description: 'Добавить или изменить прокси-узел',
    icon: Server,
  },
  {
    to: `/antizapret#${ANTIZAPRET_REMOTES_HASH}`,
    label: 'Конфиг AntiZapret',
    description: 'Адреса подключения и параметры setup',
    icon: Settings2,
  },
  {
    to: '/monitoring',
    label: 'NOC → Подключения',
    description: 'Домашний IP и пометка «через прокси»',
    icon: Activity,
  },
  {
    to: '/settings/modules',
    label: 'Настройки → Модули',
    description: 'Включить или выключить Прокси-узлы',
    icon: Puzzle,
  },
] as const

export default function ProxyHubView() {
  const { activeNode, nodes, syncGroups, refreshNodes, refreshSyncGroups } = useNode()
  const { error: notifyError } = useNotifications()
  const [bootstrapped, setBootstrapped] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const proxyNodes = useMemo(() => nodes.filter(isProxyNode), [nodes])

  // A proxy node auto-installed alongside node_agent on the SAME box as its
  // linked VPN node (same host) is just latent dnat_front capability, not a
  // real RU-relay - it'll never have proxy.sh and showing that warning for
  // every single VPN node here is pure noise ("у тебя все прокси фронты и
  // нихуя не ясно кто из них кто" - same complaint already fixed on Узлы).
  // A genuine standalone RU-proxy (different host from its linked VPN node,
  // e.g. a home/RU box relaying to a foreign backend) still gets full billing.
  const { standaloneProxyNodes, dormantProxyNodes } = useMemo(() => {
    const standalone: typeof proxyNodes = []
    const dormant: typeof proxyNodes = []
    for (const node of proxyNodes) {
      const linkedVpn = nodes.find((n) => n.id === node.linked_vpn_node_id)
      if (linkedVpn && linkedVpn.host === node.host) {
        dormant.push(node)
      } else {
        standalone.push(node)
      }
    }
    return { standaloneProxyNodes: standalone, dormantProxyNodes: dormant }
  }, [proxyNodes, nodes])
  const [showDormant, setShowDormant] = useState(false)

  const load = useCallback(async () => {
    setRefreshing(true)
    setLoadError(null)
    try {
      await Promise.all([refreshNodes(), refreshSyncGroups()])
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Не удалось загрузить прокси-узлы'
      setLoadError(message)
      notifyError(message)
    } finally {
      setRefreshing(false)
      setBootstrapped(true)
    }
  }, [notifyError, refreshNodes, refreshSyncGroups])

  useEffect(() => {
    void load()
    // Mount bootstrap only — manual refresh uses load(); panel updates use refreshNodes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handlePanelUpdated = useCallback(async () => {
    try {
      await refreshNodes()
    } catch {
      // Best-effort; panel already showed its own success/error toast.
    }
  }, [refreshNodes])

  const activeNodeLabel = useMemo(() => {
    if (!activeNode) return null
    return activeNode.name
  }, [activeNode])

  return (
    <div className="space-y-6">
      <HaReplicaBanner />

      <PageSectionHeader
        icon={Network}
        title="Прокси"
        docsHref={DOCS.proxyNodes}
        docsLabel="Инструкция"
        description={
          <>
            Сводка по прокси-узлам и адресам OpenVPN активного VPN. У каждого прокси можно указать,
            к какой HA-группе или серверу он относится. Панель не ставит и не запускает{' '}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">proxy.sh</code>.
          </>
        }
        actions={
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()} disabled={refreshing}>
              <RefreshCw className="mr-1.5 h-4 w-4" />
              Обновить
            </Button>
            <Button type="button" variant="outline" size="sm" asChild>
              <a href={AZ_PROXY_SH_DOCS_URL} target="_blank" rel="noopener noreferrer">
                AntiZapret proxy.sh
                <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
              </a>
            </Button>
          </div>
        }
      />

      <section className="space-y-3">
        <div className="space-y-1">
          <h3 className="text-sm font-semibold tracking-tight">Адреса подключения (активный VPN)</h3>
          <p className="text-xs text-muted-foreground">
            Список remote OpenVPN для{' '}
            {activeNodeLabel ? (
              <span className="font-medium text-foreground">{activeNodeLabel}</span>
            ) : (
              'активного узла'
            )}
            . Полный блок setup — в{' '}
            <Link
              to={`/antizapret#${ANTIZAPRET_REMOTES_HASH}`}
              className="font-medium text-primary underline-offset-4 hover:underline"
            >
              Конфиг AntiZapret
            </Link>
            .
          </p>
        </div>
        <RemoteHostsCard nodeId={activeNode?.id ?? null} variant="card" />
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold tracking-tight">Прокси-узлы</h3>
          {bootstrapped && !loadError && (
            <Badge variant="secondary">{standaloneProxyNodes.length}</Badge>
          )}
        </div>

        {!bootstrapped ? (
          <Spinner label="Загрузка прокси-узлов..." className="py-8" />
        ) : loadError ? (
          <div className="space-y-3">
            <SettingsAlert variant="danger" title="Ошибка загрузки">
              {loadError}
            </SettingsAlert>
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
              <RefreshCw className="mr-1.5 h-4 w-4" />
              Повторить
            </Button>
          </div>
        ) : proxyNodes.length === 0 ? (
          <EmptyState
            icon={Network}
            title="Нет прокси-узлов"
            description="Добавьте узел типа «Прокси» на странице Узлы (модуль Прокси-узлы должен быть включён)."
            action={
              <Button asChild>
                <Link to="/nodes">Добавить на Узлах</Link>
              </Button>
            }
          />
        ) : standaloneProxyNodes.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Нет отдельных RU-прокси — только автопарные узлы для автопереключения ниже.
          </p>
        ) : (
          <div className="space-y-4">
            {standaloneProxyNodes.map((node) => (
              <Card key={node.id}>
                <CardHeader className="pb-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <CardTitle className="text-base">{node.name}</CardTitle>
                        <Badge variant="outline" className="text-[10px]">
                          Прокси
                        </Badge>
                        <ProxyLinkBadge
                          linkedVpnNodeId={node.linked_vpn_node_id}
                          nodes={nodes}
                          syncGroups={syncGroups}
                          showUnlinked
                        />
                        <NodeStatusBadge status={node.status} />
                      </div>
                      <CardDescription className="font-mono text-xs">
                        {node.host}:{node.port}
                      </CardDescription>
                    </div>
                    <Button type="button" variant="ghost" size="sm" asChild>
                      <Link to="/nodes">Открыть на Узлах</Link>
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  <ProxyNodePanel
                    node={node}
                    nodes={nodes}
                    syncGroups={syncGroups}
                    onUpdated={handlePanelUpdated}
                  />
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {bootstrapped && !loadError && dormantProxyNodes.length > 0 && (
          <div className="space-y-3 border-t pt-3">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              onClick={() => setShowDormant((v) => !v)}
            >
              {showDormant ? 'Скрыть' : 'Показать'} автопарные узлы для автопереключения (
              {dormantProxyNodes.length})
            </Button>
            {showDormant && (
              <div className="space-y-4">
                <p className="text-xs text-muted-foreground">
                  Эти прокси стоят на том же сервере, что и их VPN-узел — авто-установлены вместе с
                  node_agent, чтобы любой узел мог стать фронтом пула автопереключения без отдельной
                  установки. Пока не назначены фронтом — ничего не делают, и{' '}
                  <code className="rounded bg-muted px-1 py-0.5 font-mono">proxy.sh</code> на них не
                  нужен.
                </p>
                {dormantProxyNodes.map((node) => (
                  <DormantProxyCard
                    key={node.id}
                    node={node}
                    nodes={nodes}
                    syncGroups={syncGroups}
                    onUpdated={handlePanelUpdated}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold tracking-tight">Быстрые ссылки</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {QUICK_LINKS.map((item) => {
            const Icon = item.icon
            return (
              <Link
                key={item.to}
                to={item.to}
                className="group flex items-start gap-3 rounded-xl border bg-card p-4 transition-colors hover:bg-muted/40"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium group-hover:text-foreground">{item.label}</p>
                  <p className="text-xs text-muted-foreground">{item.description}</p>
                </div>
              </Link>
            )
          })}
        </div>
      </section>
    </div>
  )
}
