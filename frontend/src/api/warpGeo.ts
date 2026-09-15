import { apiFetch } from './http'
import type { WarpGeoCheckResponse, WarpGeoNodesResponse, WarpGeoStatusResponse } from '../types'

export async function listWarpGeoNodes() {
  return apiFetch<WarpGeoNodesResponse>('/warp-geo/nodes')
}

export async function getWarpGeoStatus(nodeId: number) {
  return apiFetch<WarpGeoStatusResponse>(`/warp-geo/${nodeId}/status`)
}

export async function checkWarpGeo(nodeId: number, scope: 'antizapret' | 'vpn' | 'raw') {
  return apiFetch<WarpGeoCheckResponse>(`/warp-geo/${nodeId}/check?scope=${scope}`)
}

export async function saveWarpProtonConfig(nodeId: number, scope: 'antizapret' | 'vpn', rawConfig: string) {
  return apiFetch<{ success: boolean; scope: string }>(`/warp-geo/${nodeId}/proton-config?scope=${scope}`, {
    method: 'POST',
    body: JSON.stringify({ raw_config: rawConfig }),
  })
}

export async function setWarpProvider(nodeId: number, provider: 'proton' | 'cloudflare') {
  return apiFetch<{ success: boolean; warp_provider: string }>(`/warp-geo/${nodeId}/provider`, {
    method: 'POST',
    body: JSON.stringify({ provider }),
  })
}

export async function applyWarpChanges(nodeId: number) {
  return apiFetch<{ success: boolean; output: string }>(`/warp-geo/${nodeId}/apply`, { method: 'POST' })
}
