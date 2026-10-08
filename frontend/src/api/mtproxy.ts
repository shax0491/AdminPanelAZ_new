import { apiFetch } from './http'
import type { MtproxyNodeStatus } from '@/types'

export async function getMtproxyStatus(refresh = false): Promise<{ nodes: MtproxyNodeStatus[] }> {
  return apiFetch<{ nodes: MtproxyNodeStatus[] }>(`/mtproxy/status${refresh ? '?refresh=true' : ''}`)
}

export type MtproxyAction = 'setlimits' | 'enable' | 'disable' | 'link' | 'reset_traffic' | 'restart'

export interface MtproxyActionPayload {
  action: MtproxyAction
  label?: string
  max_conns?: number
  max_ips?: number
  quota_gb?: number
  expires?: string
}

export interface MtproxyActionResult {
  ok: boolean
  action: MtproxyAction
  label: string | null
  output: string
  links?: string[]
}

export async function runMtproxyAction(nodeId: number, payload: MtproxyActionPayload): Promise<MtproxyActionResult> {
  return apiFetch<MtproxyActionResult>(`/mtproxy/nodes/${nodeId}/action`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
