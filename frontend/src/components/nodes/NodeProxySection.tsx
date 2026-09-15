import { useState } from 'react'
import { ChevronDown, Unplug } from 'lucide-react'
import FailoverFrontRoleCard from '@/components/nodes/FailoverFrontRoleCard'
import ProxyNodePanel from '@/components/nodes/ProxyNodePanel'
import { cn } from '@/lib/utils'
import type { Node, NodeSyncGroup } from '@/types'

/** Collapsed by default — FailoverFrontRoleCard + ProxyNodePanel together are
 * a lot of near-boilerplate per proxy/front node (proxy.sh status, DESTINATION
 * field, front pool summary), repeated once per node in a list that can have
 * several. Expand on demand instead of always rendering it all inline. */
export default function NodeProxySection({
  node,
  nodes,
  syncGroups,
  onUpdated,
}: {
  node: Node
  nodes: Node[]
  syncGroups: NodeSyncGroup[]
  onUpdated?: () => void | Promise<void>
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-lg border border-border/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        <Unplug size={14} className="shrink-0" />
        <span>Прокси / Фронт автопереключения</span>
        <ChevronDown size={14} className={cn('ml-auto shrink-0 transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-border/60 p-3 pt-3">
          <FailoverFrontRoleCard node={node} />
          <ProxyNodePanel node={node} nodes={nodes} syncGroups={syncGroups} onUpdated={onUpdated} />
        </div>
      )}
    </div>
  )
}
