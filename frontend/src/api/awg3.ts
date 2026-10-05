import { apiFetch } from './http'

export async function getAwg3Health() {
  return apiFetch<import('../types').Awg3HealthResponse>('/awg3/health')
}

export async function getAwg3Monitoring() {
  return apiFetch<import('../types').Awg3MonitoringResponse>('/awg3/monitoring')
}

export async function listAwg3Clients() {
  return apiFetch<import('../types').Awg3ClientListResponse>('/awg3/clients')
}

export async function createAwg3Client(name: string, mode: 'split' | 'full' = 'split') {
  return apiFetch<import('../types').Awg3ClientCreated>('/awg3/clients', {
    method: 'POST',
    body: JSON.stringify({ name, mode }),
  })
}

export async function deleteAwg3Client(name: string) {
  return apiFetch<void>(`/awg3/clients/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export async function getAwg3ClientConfig(name: string) {
  return apiFetch<import('../types').Awg3ClientConfigResponse>(`/awg3/clients/${encodeURIComponent(name)}/config`)
}
