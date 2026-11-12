import { useEffect, useState } from 'react'
import { applyNodeIpv6, getIpv6Status, getNodesIpv6Summary, type NodeIpv6Summary } from '@/api/ipv6'
import { Badge } from '@/components/ui/badge'
import { useNode } from '@/context/NodeContext'
import { cn } from '@/lib/utils'
import type { Ipv6Status } from '@/types'

export type Ipv6BadgeInfo = { tone: 'ok' | 'safe' | 'error'; text: string }

const EGRESS_LABEL: Record<string, string> = { native: 'Native', warp: 'WARP', proton: 'Proton', relay: 'Relay' }

/** Что показать про IPv6 узла: Dual-Stack / защита от утечек (только IPv4) / ошибка выхода (откат на IPv4). */
export function ipv6BadgeInfo(s: NodeIpv6Summary | null): Ipv6BadgeInfo | null {
  if (!s || s.enabled === undefined) return null
  const wanted = s.enabled && s.requested_mode !== 'a'
  if (wanted && s.effective_mode === 'a') return { tone: 'error', text: 'IPv6: Ошибка выхода (Откат на IPv4)' }
  if (s.enabled && s.effective_mode !== 'a') {
    return { tone: 'ok', text: `IPv6: Dual-Stack (${EGRESS_LABEL[s.active_egress || s.egress || 'native'] ?? 'Native'})` }
  }
  return { tone: 'safe', text: 'IPv6: Защита от утечек (Только IPv4)' }
}

const TONE: Record<Ipv6BadgeInfo['tone'], string> = {
  ok: 'border-green-500/40 bg-green-500/10 text-green-700 dark:text-green-400',
  safe: 'border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-400',
  error: 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-400',
}

/** Состояние IPv6 активного узла; null, пока не загрузилось или не поддерживается. */
export function useIpv6Status(): Ipv6Status | null {
  const nodeId = useNode().activeNode?.id
  const [status, setStatus] = useState<Ipv6Status | null>(null)
  useEffect(() => {
    let alive = true
    setStatus(null)
    getIpv6Status()
      .then((s) => alive && setStatus(s))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [nodeId])
  return status
}

/** Бейдж IPv6 активного узла для шапки (рядом с выбором узла). */
export function ActiveNodeIpv6Badge() {
  return <Ipv6Badge status={useIpv6Status()} className="hidden lg:inline-flex" />
}

// Один запрос на все карточки «Узлов»; кэш 30 с, чтобы не опрашивать агентов при каждом рендере.
let summaryCache: { at: number; data: Promise<Record<string, NodeIpv6Summary>> } | null = null

function loadSummary(): Promise<Record<string, NodeIpv6Summary>> {
  if (!summaryCache || Date.now() - summaryCache.at > 30_000) {
    summaryCache = { at: Date.now(), data: getNodesIpv6Summary().catch(() => ({})) }
  }
  return summaryCache.data
}

/** Бейдж IPv6 на карточке узла в разделе «Узлы». */
export function NodeIpv6Badge({ nodeId }: { nodeId: number }) {
  const [item, setItem] = useState<NodeIpv6Summary | null | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let alive = true
    void loadSummary().then((m) => alive && setItem(m[String(nodeId)] ?? null))
    return () => {
      alive = false
    }
  }, [nodeId])
  if (item === undefined) return null
  if (!item || item.error) {
    return (
      <Badge variant="outline" className="h-5 px-1.5 text-[10px] font-normal text-muted-foreground" title={item?.error}>
        IPv6: нет данных
      </Badge>
    )
  }
  const run = async () => {
    setBusy(true)
    try {
      setItem(await applyNodeIpv6(nodeId, !item.enabled))
      summaryCache = null
    } catch {
      setItem({ ...item, reason: 'Не удалось применить IPv6' })
    } finally {
      setBusy(false)
    }
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Ipv6Badge status={item} />
      {item.enabled && (
        <span className="text-[10px] text-muted-foreground">
          режим {item.effective_mode} · выход {item.active_egress || item.egress} · подмен {item.fake6_mappings ?? 0}
        </span>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={() => void run()}
        className="rounded border border-input px-1.5 py-0.5 text-[10px] hover:bg-muted disabled:opacity-50"
        title="Перезапускает antizapret на узле"
      >
        {busy ? '…' : item.enabled ? 'Применить IPv6' : 'Включить IPv6'}
      </button>
    </span>
  )
}

export default function Ipv6Badge({ status, className }: { status: NodeIpv6Summary | null; className?: string }) {
  const info = ipv6BadgeInfo(status)
  if (!info) return null
  return (
    <Badge variant="outline" title={status?.reason || undefined} className={cn('h-5 px-1.5 text-[10px] font-normal', TONE[info.tone], className)}>
      {info.text}
    </Badge>
  )
}
