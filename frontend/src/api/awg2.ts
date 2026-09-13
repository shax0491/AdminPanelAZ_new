import { apiFetch } from './http'

export async function getAwg2Health() {
  return apiFetch<import('../types').Awg2HealthResponse>('/awg2/health')
}

export async function getAwg2Monitoring() {
  return apiFetch<import('../types').Awg2MonitoringResponse>('/awg2/monitoring')
}

export async function getAwg2ClientStats(clientName: string) {
  return apiFetch<import('../types').Awg2ClientStats>(`/awg2/clients/${encodeURIComponent(clientName)}/stats`)
}
