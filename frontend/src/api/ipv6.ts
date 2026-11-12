import { apiFetch } from './http'
import type { Ipv6Status } from '@/types'

export type NodeIpv6Summary = Partial<Pick<Ipv6Status, 'enabled' | 'requested_mode' | 'effective_mode' | 'egress' | 'active_egress' | 'reason' | 'fake6_mappings'>> & {
  error?: string
}

export async function applyNodeIpv6(nodeId: number, enable: boolean): Promise<NodeIpv6Summary> {
  return apiFetch<NodeIpv6Summary>(`/nodes/${nodeId}/ipv6/apply?enable=${enable}`, { method: 'POST' })
}

export async function getNodesIpv6Summary(): Promise<Record<string, NodeIpv6Summary>> {
  return apiFetch<Record<string, NodeIpv6Summary>>('/nodes/ipv6-summary')
}

export async function getIpv6Status(): Promise<Ipv6Status> {
  return apiFetch<Ipv6Status>('/ipv6')
}

export async function applyIpv6(): Promise<Ipv6Status> {
  return apiFetch<Ipv6Status>('/ipv6/apply', { method: 'POST' })
}

export type ClientIpv6Profile = 'default' | 'ipv4' | 'dual'

export async function getClientIpv6Profiles(): Promise<Record<string, 'ipv4' | 'dual'>> {
  return (await apiFetch<{ profiles: Record<string, 'ipv4' | 'dual'> }>('/client-ipv6')).profiles
}

export async function setClientIpv6Profile(
  client: string,
  profile: ClientIpv6Profile,
  regenerate = true,
): Promise<{ notes: string[] }> {
  return apiFetch<{ notes: string[] }>(`/client-ipv6/${encodeURIComponent(client)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile, regenerate }),
  })
}
