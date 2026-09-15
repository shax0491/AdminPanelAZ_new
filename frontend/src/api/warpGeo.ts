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
