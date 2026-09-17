import { useState } from 'react'
import { Check, ChevronDown, Globe, Server } from 'lucide-react'
import NodeProxySection from '@/components/nodes/NodeProxySection'
import ProxyLinkBadge from '@/components/proxy/ProxyLinkBadge'
import { NodeStatusBadge } from '@/components/NodeSelector'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { Node, NodeSyncGroup, NodeTransportId } from '@/types'
import NodeActions from './NodeActions'
import NodeConnectionErrorAlert from './NodeConnectionErrorAlert'
import NodeTransportBadge from './NodeTransportBadge'
import { formatLastSeen, getNodeMeta } from './nodeHelpers'
import { isProxyNode } from './nodeKind'

export type NodeActionHandlers = {
  isActive: boolean
  healthLoading: boolean
  activateLoading: boolean
  onActivate: () => void
  onHealth: () => void
  onUpdate: () => void
  onRestart: () => void
  onRotateKey: () => void
  onTransportChange: (transport: NodeTransportId) => void
  onEdit: () => void
  onDelete: () => void
}

function statusDotClass(node: Node, isActive: boolean) {
  if (isActive) return 'bg-primary'
  if (node.status === 'online') return 'bg-emerald-500'
  if (node.status === 'offline') return 'bg-destructive'
  return 'bg-muted-foreground/40'
}

/** One node's full detail body (IP/services/agent/связь/badges/actions) -
 * shared between the top-level card and a nested paired-front sub-card, so
 * the two never drift apart. */
function NodeCardBody({
  node,
  showProxyUi,
  nodes,
  syncGroups,
  actions,
  onProxyUpdated,
}: {
  node: Node
  showProxyUi: boolean
  nodes: Node[]
  syncGroups: NodeSyncGroup[]
  actions: NodeActionHandlers
  onProxyUpdated?: () => void | Promise<void>
}) {
  const meta = getNodeMeta(node)
  const isProxy = isProxyNode(node)
  const showProxyAffordance = showProxyUi && isProxy

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
        <div>
          <p className="text-xs text-muted-foreground">IP сервера</p>
          <p className="font-mono text-xs">{meta.serverIp ?? '—'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Службы</p>
          <p>{meta.servicesLabel ?? '—'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Agent</p>
          <p className="font-mono text-xs">{meta.agentVersion ?? '—'}</p>
        </div>
      </div>
      <div className="rounded-md border border-border/60 bg-muted/20 p-3 space-y-2">
        <p className="text-xs font-medium text-muted-foreground">Связь</p>
        <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
          <div>
            <span className="text-muted-foreground">TLS </span>
            {meta.listenTls === null && meta.expectedTls === null ? (
              <span>— (обновите агент ≥1.8)</span>
            ) : (
              <span>
                факт {meta.listenTls == null ? '—' : meta.listenTls ? 'HTTPS' : 'HTTP'}
                {' / '}
                ожид. {meta.expectedTls == null ? '—' : meta.expectedTls ? 'HTTPS' : 'HTTP'}
                {meta.tlsMismatch ? ' · mismatch' : ''}
              </span>
            )}
          </div>
          <div>
            <span className="text-muted-foreground">Uptime </span>
            <span>
              {meta.uptimeSec == null
                ? '—'
                : meta.uptimeSec < 120
                  ? `${meta.uptimeSec} с`
                  : `${Math.floor(meta.uptimeSec / 60)} мин`}
            </span>
          </div>
          <div className="sm:col-span-2">
            <span className="text-muted-foreground">Последний ok </span>
            <span>{meta.lastHealthOkAt ? formatLastSeen(meta.lastHealthOkAt) : '—'}</span>
          </div>
          {meta.lastLinkError?.message && (
            <div className="sm:col-span-2 text-amber-800 dark:text-amber-100">
              <span className="font-mono">{meta.lastLinkError.code ?? 'error'}</span>
              {': '}
              {meta.lastLinkError.message}
              {meta.lastLinkError.hint ? ` — ${meta.lastLinkError.hint}` : ''}
            </div>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {node.is_local ? (
          <Badge variant="secondary">Локальный</Badge>
        ) : (
          <Badge variant="outline">
            <Globe size={10} />
            Удалённый
          </Badge>
        )}
        <NodeTransportBadge node={node} />
      </div>
      {node.status === 'offline' && meta.lastError && (
        <NodeConnectionErrorAlert node={node} lastError={meta.lastError} />
      )}
      {showProxyAffordance && (
        <NodeProxySection node={node} nodes={nodes} syncGroups={syncGroups} onUpdated={onProxyUpdated} />
      )}
      <NodeActions
        node={node}
        isActive={actions.isActive}
        isProxy={isProxy}
        healthLoading={actions.healthLoading}
        activateLoading={actions.activateLoading}
        onActivate={actions.onActivate}
        onHealth={actions.onHealth}
        onUpdate={actions.onUpdate}
        onRestart={actions.onRestart}
        onRotateKey={actions.onRotateKey}
        onTransportChange={actions.onTransportChange}
        onEdit={actions.onEdit}
        onDelete={actions.onDelete}
      />
    </div>
  )
}

export type NodeCardProps = {
  node: Node
  actions: NodeActionHandlers
  /** The (front)-proxy node paired to this VPN node via linked_vpn_node_id, if any - rendered nested inside this card instead of as its own top-level card. */
  pairedProxyNode?: Node | null
  pairedActions?: NodeActionHandlers | null
  showProxyUi: boolean
  nodes: Node[]
  syncGroups: NodeSyncGroup[]
  selected?: boolean
  onToggleSelect?: () => void
  onProxyUpdated?: () => void | Promise<void>
  /** Name of the enabled pool this node is the *live* dnat_front of right now, if any - every node has proxy_agent, most aren't actually fronting anyone. */
  frontPoolName?: string | null
  pairedFrontPoolName?: string | null
}

export default function NodeCard({
  node,
  actions,
  pairedProxyNode,
  pairedActions,
  showProxyUi,
  nodes,
  syncGroups,
  selected = false,
  onToggleSelect,
  onProxyUpdated,
  frontPoolName,
  pairedFrontPoolName,
}: NodeCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [pairedExpanded, setPairedExpanded] = useState(false)
  const lastSeen = formatLastSeen(node.last_seen_at)
  const isProxy = isProxyNode(node)
  const showProxyAffordance = showProxyUi && isProxy

  return (
    <Card
      className={cn(
        'overflow-hidden border-border/70 transition-colors',
        actions.isActive && 'border-primary/40 bg-primary/[0.04]',
        node.status === 'offline' && !actions.isActive && 'border-destructive/20',
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-start gap-2 px-4 py-3 text-left"
      >
        {onToggleSelect && (
          <input
            type="checkbox"
            checked={selected}
            onChange={(e) => {
              e.stopPropagation()
              onToggleSelect()
            }}
            onClick={(e) => e.stopPropagation()}
            aria-label={`Выбрать ${node.name}`}
            className="mt-1 h-4 w-4 shrink-0 rounded border"
          />
        )}
        <span
          className={cn('mt-1.5 h-2 w-2 shrink-0 rounded-full', statusDotClass(node, actions.isActive))}
          aria-hidden
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <Server size={14} className="shrink-0 text-muted-foreground" />
            <span className="truncate text-sm font-semibold tracking-tight">{node.name}</span>
            {isProxy && (
              <Badge
                variant="outline"
                className="max-w-[14rem] truncate border-amber-500/40 text-[10px] text-amber-800 dark:text-amber-100"
                title={frontPoolName ? `Сейчас активный фронт: ${frontPoolName}` : 'proxy_agent есть, но фронтом ни одного пула сейчас не назначен'}
              >
                {frontPoolName ? `Фронт: ${frontPoolName}` : 'Прокси'}
              </Badge>
            )}
            {isProxy && !showProxyUi && (
              <Badge variant="secondary" className="text-[10px]">
                модуль выкл
              </Badge>
            )}
            {showProxyAffordance && (
              <ProxyLinkBadge
                linkedVpnNodeId={node.linked_vpn_node_id}
                nodes={nodes}
                syncGroups={syncGroups}
                showUnlinked
              />
            )}
            {actions.isActive && (
              <Badge variant="default" className="text-[10px]">
                <Check size={10} />
                активный
              </Badge>
            )}
            <span className="ml-auto shrink-0">
              <NodeStatusBadge status={node.status} />
            </span>
          </span>
          <span className="mt-0.5 block truncate font-mono text-xs text-muted-foreground">
            {node.is_local ? 'local' : `${node.host}:${node.port}`}
            {lastSeen ? ` · проверено ${lastSeen}` : ''}
          </span>
        </span>
        <ChevronDown
          size={16}
          className={cn('mt-1 shrink-0 text-muted-foreground transition-transform', expanded && 'rotate-180')}
        />
      </button>

      {expanded && (
        <div className="space-y-4 border-t border-border/60 px-4 pb-4 pt-3">
          <NodeCardBody
            node={node}
            showProxyUi={showProxyUi}
            nodes={nodes}
            syncGroups={syncGroups}
            actions={actions}
            onProxyUpdated={onProxyUpdated}
          />

          {pairedProxyNode && pairedActions && (
            <div className="rounded-lg border border-border/60">
              <button
                type="button"
                onClick={() => setPairedExpanded((v) => !v)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left"
              >
                <span
                  className={cn(
                    'h-2 w-2 shrink-0 rounded-full',
                    statusDotClass(pairedProxyNode, pairedActions.isActive),
                  )}
                  aria-hidden
                />
                <span className="truncate text-sm font-medium">{pairedProxyNode.name}</span>
                <Badge
                  variant="outline"
                  className="max-w-[14rem] truncate border-amber-500/40 text-[10px] text-amber-800 dark:text-amber-100"
                  title={
                    pairedFrontPoolName
                      ? `Сейчас активный фронт: ${pairedFrontPoolName}`
                      : 'proxy_agent есть, но фронтом ни одного пула сейчас не назначен'
                  }
                >
                  {pairedFrontPoolName ? `Фронт: ${pairedFrontPoolName}` : 'Прокси (не фронт)'}
                </Badge>
                <span className="ml-auto shrink-0">
                  <NodeStatusBadge status={pairedProxyNode.status} />
                </span>
                <ChevronDown
                  size={14}
                  className={cn('shrink-0 text-muted-foreground transition-transform', pairedExpanded && 'rotate-180')}
                />
              </button>
              {pairedExpanded && (
                <div className="border-t border-border/60 p-3 pt-3">
                  <NodeCardBody
                    node={pairedProxyNode}
                    showProxyUi={showProxyUi}
                    nodes={nodes}
                    syncGroups={syncGroups}
                    actions={pairedActions}
                    onProxyUpdated={onProxyUpdated}
                  />
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
