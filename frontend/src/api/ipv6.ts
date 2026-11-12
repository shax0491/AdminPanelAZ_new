import { apiFetch } from './http'
import type { Ipv6Status } from '@/types'

export async function getIpv6Status(): Promise<Ipv6Status> {
  return apiFetch<Ipv6Status>('/ipv6')
}

export async function applyIpv6(): Promise<Ipv6Status> {
  return apiFetch<Ipv6Status>('/ipv6/apply', { method: 'POST' })
}
