import { apiFetch } from './http'
import type {
  FailoverClientLink,
  FailoverPool,
  FailoverPoolCreate,
  FailoverPoolFrontUpdate,
  FailoverPoolUpdate,
  FailoverStatusEntry,
  FailoverSwitchResult,
  FailoverSyncResult,
} from '../types'

export async function listFailoverPools() {
  return apiFetch<FailoverPool[]>('/failover-pools')
}

export async function createFailoverPool(payload: FailoverPoolCreate) {
  return apiFetch<FailoverPool>('/failover-pools', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateFailoverPool(poolId: number, payload: FailoverPoolUpdate) {
  return apiFetch<FailoverPool>(`/failover-pools/${poolId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function deleteFailoverPool(poolId: number) {
  return apiFetch<{ message: string }>(`/failover-pools/${poolId}`, { method: 'DELETE' })
}

export async function addFailoverPoolMember(
  poolId: number,
  payload: { node_id: number; priority?: number; label?: string | null },
) {
  return apiFetch<FailoverPool>(`/failover-pools/${poolId}/members`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function removeFailoverPoolMember(poolId: number, memberId: number) {
  return apiFetch<FailoverPool>(`/failover-pools/${poolId}/members/${memberId}`, {
    method: 'DELETE',
  })
}

export async function linkFailoverClient(poolId: number, clientName: string) {
  return apiFetch<FailoverClientLink>(`/failover-pools/${poolId}/clients`, {
    method: 'POST',
    body: JSON.stringify({ client_name: clientName }),
  })
}

export async function unlinkFailoverClient(poolId: number, clientName: string) {
  return apiFetch<{ message: string }>(
    `/failover-pools/${poolId}/clients/${encodeURIComponent(clientName)}`,
    { method: 'DELETE' },
  )
}

export async function resyncFailoverClient(poolId: number, clientName: string) {
  return apiFetch<FailoverSyncResult>(
    `/failover-pools/${poolId}/clients/${encodeURIComponent(clientName)}/sync`,
    { method: 'POST' },
  )
}

export async function getFailoverClientStatus(poolId: number, clientName: string) {
  return apiFetch<FailoverStatusEntry[]>(
    `/failover-pools/${poolId}/clients/${encodeURIComponent(clientName)}/status`,
  )
}

export async function setFailoverFront(poolId: number, payload: FailoverPoolFrontUpdate) {
  return apiFetch<FailoverPool>(`/failover-pools/${poolId}/front`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function mirrorFailoverMemberIdentity(poolId: number, memberId: number) {
  return apiFetch<FailoverPool>(`/failover-pools/${poolId}/members/${memberId}/mirror-identity`, {
    method: 'POST',
  })
}

export async function forceSwitchFailoverMember(poolId: number, memberId: number) {
  return apiFetch<FailoverSwitchResult>(`/failover-pools/${poolId}/members/${memberId}/force-switch`, {
    method: 'POST',
  })
}

export async function switchCheckFailoverPool(poolId: number) {
  return apiFetch<FailoverSwitchResult>(`/failover-pools/${poolId}/switch-check`, {
    method: 'POST',
  })
}
