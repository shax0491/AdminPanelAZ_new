import { apiFetch } from './http'
import type { WarpDnsResponse, WarpGeoCheckResponse, WarpGeoNodesResponse, WarpGeoStatusResponse } from '../types'

export async function listWarpGeoNodes() {
  return apiFetch<WarpGeoNodesResponse>('/warp-geo/nodes')
}

export async function getWarpGeoStatus(nodeId: number) {
  return apiFetch<WarpGeoStatusResponse>(`/warp-geo/${nodeId}/status`)
}

export async function checkWarpGeo(nodeId: number, scope: 'antizapret' | 'vpn' | 'raw') {
  return apiFetch<WarpGeoCheckResponse>(`/warp-geo/${nodeId}/check?scope=${scope}`)
}

export async function getWarpDns(nodeId: number) {
  return apiFetch<WarpDnsResponse>(`/warp-geo/${nodeId}/dns`)
}

export async function saveWarpProtonConfig(nodeId: number, scope: 'antizapret' | 'vpn', rawConfig: string) {
  return apiFetch<{ success: boolean; scope: string }>(`/warp-geo/${nodeId}/proton-config?scope=${scope}`, {
    method: 'POST',
    body: JSON.stringify({ raw_config: rawConfig }),
  })
}

export type ProtonFields = {
  private_key: string
  public_key: string
  address: string
  endpoint_host: string
  endpoint_port: string
}

export async function saveWarpProtonFields(nodeId: number, scope: 'antizapret' | 'vpn', fields: ProtonFields) {
  return apiFetch<{ success: boolean; scope: string }>(`/warp-geo/${nodeId}/proton-fields?scope=${scope}`, {
    method: 'POST',
    body: JSON.stringify(fields),
  })
}

export async function setWarpProvider(nodeId: number, provider: 'proton' | 'cloudflare') {
  return apiFetch<{ success: boolean; warp_provider: string }>(`/warp-geo/${nodeId}/provider`, {
    method: 'POST',
    body: JSON.stringify({ provider }),
  })
}

export async function setWarpModes(nodeId: number, modes: { antizapret?: string; vpn?: string }) {
  return apiFetch<{ success: boolean; antizapret_warp: string | null; vpn_warp: string | null }>(
    `/warp-geo/${nodeId}/modes`,
    { method: 'POST', body: JSON.stringify({ antizapret: modes.antizapret ?? null, vpn: modes.vpn ?? null }) },
  )
}

export async function applyWarpChanges(nodeId: number) {
  return apiFetch<{ success: boolean; output: string }>(`/warp-geo/${nodeId}/apply`, { method: 'POST' })
}

export async function testCloudflareWarpPreview(nodeId: number) {
  return apiFetch<WarpGeoCheckResponse>(`/warp-geo/${nodeId}/test-cloudflare`, { method: 'POST' })
}
