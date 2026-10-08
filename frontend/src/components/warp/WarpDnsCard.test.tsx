// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import WarpDnsCard from './WarpDnsCard'
import type { WarpDnsResponse } from '@/types'

const api = vi.hoisted(() => ({ getWarpDns: vi.fn() }))
vi.mock('@/api/warpGeo', () => api)

function dns(overrides: Partial<WarpDnsResponse> = {}): WarpDnsResponse {
  return {
    status: 'ok',
    status_text: 'Зарубежный DNS (kresd@2) идёт через WARP с адреса 10.2.0.2',
    enabled: true,
    warp_active: true,
    dns1: '1',
    dns2: '2',
    kresd: {
      'kresd@1': { reachable: true, outgoing: null },
      'kresd@2': { reachable: true, outgoing: '10.2.0.2' },
    },
    resolvers: [
      { instance: 'kresd@2', set: '2', role: 'основной', ip: '1.1.1.1', interface: 'warp-antizapret', via_warp: true },
      { instance: 'kresd@1', set: '1', role: 'основной', ip: '62.76.76.62', interface: 'eth0', via_warp: false },
    ],
    counters: {
      intercepted: [{ subnet: '10.29.0.0/16', label: 'AntiZapret VPN', udp: 10, tcp: 2 }],
      foreign: null,
    },
    checked_at: 1000,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('WarpDnsCard', () => {
  it('shows status, counters and asks to update node scripts when foreign counter is missing', async () => {
    api.getWarpDns.mockResolvedValue(dns())
    render(<WarpDnsCard nodeId={1} />)

    expect(await screen.findByText('DNS через WARP')).toBeTruthy()
    expect(screen.getByText('AntiZapret VPN')).toBeTruthy()
    expect(screen.getByText(/появится после обновления скриптов узла/)).toBeTruthy()
    expect(api.getWarpDns).toHaveBeenCalledWith(1)
  })

  it('shows leak badge and growth since previous refresh', async () => {
    api.getWarpDns.mockResolvedValueOnce(dns({ status: 'leak', status_text: 'Утечка' }))
    render(<WarpDnsCard nodeId={1} />)
    expect(await screen.findByText('Утечка DNS')).toBeTruthy()

    api.getWarpDns.mockResolvedValueOnce(
      dns({
        status: 'leak',
        checked_at: 1060,
        counters: { intercepted: [{ subnet: '10.29.0.0/16', label: 'AntiZapret VPN', udp: 25, tcp: 2 }], foreign: [] },
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: /Обновить/ }))
    await waitFor(() => expect(screen.getByText('+15 за 60 с')).toBeTruthy())
  })

  it('shows off state like upstream', async () => {
    api.getWarpDns.mockResolvedValue(dns({ status: 'off', status_text: 'Выключено', enabled: false }))
    render(<WarpDnsCard nodeId={2} />)
    expect(await screen.findByText('Выключено (как у апстрима)')).toBeTruthy()
  })
})
