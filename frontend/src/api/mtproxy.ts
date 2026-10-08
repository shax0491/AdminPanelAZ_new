import { apiFetch } from './http'
import type { MtproxyNodeStatus } from '@/types'

export async function getMtproxyStatus(refresh = false): Promise<{ nodes: MtproxyNodeStatus[] }> {
  return apiFetch<{ nodes: MtproxyNodeStatus[] }>(`/mtproxy/status${refresh ? '?refresh=true' : ''}`)
}
