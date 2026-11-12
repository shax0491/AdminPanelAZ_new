import { useEffect, useState } from 'react'
import { getIpv6Status } from '@/api/ipv6'
import { Badge } from '@/components/ui/badge'
import { useNode } from '@/context/NodeContext'
import { cn } from '@/lib/utils'
import type { Ipv6Status } from '@/types'

export type Ipv6BadgeInfo = { tone: 'ok' | 'safe' | 'error'; text: string }

const EGRESS_LABEL: Record<string, string> = { native: 'Native', warp: 'WARP', proton: 'Proton', relay: 'Relay' }

/** Что показать про IPv6 узла: Dual-Stack / защита от утечек (только IPv4) / ошибка выхода (откат на IPv4). */
export function ipv6BadgeInfo(s: Ipv6Status | null): Ipv6BadgeInfo | null {
  if (!s) return null
  const wanted = s.enabled && s.requested_mode !== 'a'
  if (wanted && s.effective_mode === 'a') return { tone: 'error', text: 'IPv6: Ошибка выхода (Откат на IPv4)' }
  if (s.enabled && s.effective_mode !== 'a') {
    return { tone: 'ok', text: `IPv6: Dual-Stack (${EGRESS_LABEL[s.active_egress || s.egress] ?? 'Native'})` }
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

export default function Ipv6Badge({ status, className }: { status: Ipv6Status | null; className?: string }) {
  const info = ipv6BadgeInfo(status)
  if (!info) return null
  return (
    <Badge variant="outline" title={status?.reason || undefined} className={cn('h-5 px-1.5 text-[10px] font-normal', TONE[info.tone], className)}>
      {info.text}
    </Badge>
  )
}
