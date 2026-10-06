// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  mountWhileActiveNodeResolves,
  resetNodeState,
  testNode,
  updateNodeState,
} from '@/test/nodePageHarness'
import type { Node } from '@/types'
import MonitoringPage from './MonitoringPage'

const api = vi.hoisted(() => ({
  getMonitoring: vi.fn(),
  getNocIncidents: vi.fn(),
  getConnectionHistory: vi.fn(),
  getResourceHistory: vi.fn(),
  openMonitoringStream: vi.fn(),
}))

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  ...api,
}))
vi.mock('@/context/AuthContext', async () => (await import('@/test/nodePageHarness')).authModule)
vi.mock('@/context/NodeContext', async () => (await import('@/test/nodePageHarness')).nodeContextModule)
vi.mock('@/context/FeatureModulesContext', async () => (await import('@/test/nodePageHarness')).featureModulesModule)
vi.mock('@/context/NotificationContext', async () => (await import('@/test/nodePageHarness')).notificationModule)
vi.mock('@/context/ProgressContext', async () => (await import('@/test/nodePageHarness')).progressModule)
vi.mock('@/components/dashboard/GeoRoutingHintBanner', () => ({ default: () => null }))
vi.mock('@/components/monitoring/MonitoringCharts', () => ({ default: () => null }))
vi.mock('@/components/monitoring/ResourceHistoryCharts', () => ({ default: () => null }))
vi.mock('@/components/monitoring/PanelResourceHistoryCharts', () => ({ default: () => null }))

const secondNode = { ...testNode, id: 2, name: 'edge', is_local: false } as Node
const page = () => <MonitoringPage />

function callCounts() {
  return {
    overview: api.getMonitoring.mock.calls.length,
    incidents: api.getNocIncidents.mock.calls.length,
    connectionHistory: api.getConnectionHistory.mock.calls.length,
    resourceHistory: api.getResourceHistory.mock.calls.length,
    stream: api.openMonitoringStream.mock.calls.length,
  }
}

const once = { overview: 1, incidents: 1, connectionHistory: 1, resourceHistory: 1, stream: 1 }

describe('MonitoringPage initial load', () => {
  beforeEach(() => {
    window.localStorage.clear()
    resetNodeState()
    api.getMonitoring.mockResolvedValue({ services: [], openvpn_clients: [], wireguard_peers: [], timestamp: '' })
    api.getNocIncidents.mockResolvedValue({ items: [] })
    api.getConnectionHistory.mockResolvedValue({ points: [] })
    api.getResourceHistory.mockResolvedValue({ points: [] })
    api.openMonitoringStream.mockReturnValue({ close: vi.fn() })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('fetches once on the first visit (scope not stored yet)', async () => {
    await mountWhileActiveNodeResolves(page, { nodes: [testNode] })

    expect(callCounts()).toEqual(once)
    expect(api.getMonitoring).toHaveBeenCalledWith('node', 'dedupe')
  })

  it('fetches once when the scope is already stored', async () => {
    window.localStorage.setItem('noc-monitoring:scope', 'node')

    await mountWhileActiveNodeResolves(page, { nodes: [testNode] })

    expect(callCounts()).toEqual(once)
  })

  describe('first visit with several nodes', () => {
    it('defaults to all nodes when the node list arrives after the active node', async () => {
      const result = await mountWhileActiveNodeResolves(page, { nodes: [], nodesLoading: true })

      expect(api.getMonitoring).not.toHaveBeenCalled()
      expect(window.localStorage.getItem('noc-monitoring:scope')).toBeNull()

      await updateNodeState(result, page, { nodes: [testNode, secondNode], nodesLoading: false })

      expect(callCounts()).toEqual(once)
      expect(api.getMonitoring).toHaveBeenCalledWith('all', 'dedupe')
      expect(api.getConnectionHistory).toHaveBeenCalledWith('1h', 'all')
      expect(window.localStorage.getItem('noc-monitoring:scope')).toBe('all')
    })

    it('defaults to all nodes when the node list arrives before the active node', async () => {
      await mountWhileActiveNodeResolves(page, { nodes: [testNode, secondNode], nodesLoading: false })

      expect(callCounts()).toEqual(once)
      expect(api.getMonitoring).toHaveBeenCalledWith('all', 'dedupe')
      expect(window.localStorage.getItem('noc-monitoring:scope')).toBe('all')
    })
  })
})

describe('MonitoringPage incidents', () => {
  const overview = { services: [], openvpn_clients: [], wireguard_peers: [], timestamp: '' }

  beforeEach(() => {
    window.localStorage.clear()
    resetNodeState()
    api.getMonitoring.mockResolvedValue(overview)
    api.getNocIncidents.mockResolvedValue({ items: [] })
    api.getConnectionHistory.mockResolvedValue({ points: [] })
    api.getResourceHistory.mockResolvedValue({ points: [] })
    api.openMonitoringStream.mockReturnValue({ close: vi.fn() })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('fetches once when the stream payload arrives before the overview', async () => {
    let resolveOverview: (value: typeof overview) => void = () => {}
    let resolveIncidents: (value: { items: [] }) => void = () => {}
    api.getMonitoring.mockReturnValue(new Promise((resolve) => (resolveOverview = resolve)))
    api.getNocIncidents.mockReturnValue(new Promise((resolve) => (resolveIncidents = resolve)))

    await mountWhileActiveNodeResolves(page, { nodes: [testNode] })
    const onPayload = api.openMonitoringStream.mock.calls[0][0] as (payload: typeof overview) => void
    await act(async () => onPayload(overview))
    expect(api.getNocIncidents).toHaveBeenCalledTimes(1)

    await act(async () => resolveOverview(overview))
    await act(async () => resolveIncidents({ items: [] }))

    expect(api.getNocIncidents).toHaveBeenCalledTimes(1)
  })

  it('refetches on manual refresh', async () => {
    await mountWhileActiveNodeResolves(page, { nodes: [testNode] })
    expect(api.getNocIncidents).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Обновить' }))
    await act(async () => {})

    expect(api.getNocIncidents).toHaveBeenCalledTimes(2)
  })

  it('reuses the pending request on manual refresh', async () => {
    api.getNocIncidents.mockReturnValue(new Promise(() => {}))
    await mountWhileActiveNodeResolves(page, { nodes: [testNode] })

    fireEvent.click(screen.getByRole('button', { name: 'Обновить' }))
    await act(async () => {})

    expect(api.getMonitoring).toHaveBeenCalledTimes(2)
    expect(api.getNocIncidents).toHaveBeenCalledTimes(1)
  })

  it('retries on manual refresh after a failed request', async () => {
    api.getNocIncidents.mockRejectedValueOnce(new Error('boom'))
    await mountWhileActiveNodeResolves(page, { nodes: [testNode] })
    expect(api.getNocIncidents).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Обновить' }))
    await act(async () => {})

    expect(api.getNocIncidents).toHaveBeenCalledTimes(2)
  })

  it('does not refetch when the scope changes', async () => {
    await mountWhileActiveNodeResolves(page, { nodes: [testNode, secondNode] })
    expect(api.getNocIncidents).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Активный узел' }))
    await act(async () => {})

    expect(api.getMonitoring).toHaveBeenLastCalledWith('node', 'dedupe')
    expect(api.getNocIncidents).toHaveBeenCalledTimes(1)
  })

  it('refetches when the active node changes (service incidents belong to it)', async () => {
    const result = await mountWhileActiveNodeResolves(page, { nodes: [testNode, secondNode] })
    expect(api.getNocIncidents).toHaveBeenCalledTimes(1)

    await updateNodeState(result, page, { activeNode: secondNode })

    expect(api.getNocIncidents).toHaveBeenCalledTimes(2)
  })
})

describe('MonitoringPage AmneziaWG 3', () => {
  const fresh = new Date().toISOString()
  const stale = new Date(Date.now() - 3_600_000).toISOString()
  const awgPeer = (name: string, iface: string, handshake: string, rx: number, tx: number) => ({
    interface: iface,
    public_key: `pk-${name}`,
    client_name: name,
    endpoint: '203.0.113.9:51900',
    latest_handshake: handshake,
    transfer_rx: rx,
    transfer_tx: tx,
  })
  const overview = {
    services: [],
    openvpn_clients: [],
    wireguard_peers: [],
    amneziawg2_peers: [awgPeer('old-user', 'antizapret2', fresh, 1000, 2000)],
    amneziawg3_peers: [
      awgPeer('alice31', 'antizapret', fresh, 5000, 6000),
      awgPeer('bob31', 'vpn', stale, 10, 20),
    ],
    total_connected_amneziawg3: 1,
    timestamp: fresh,
  }

  beforeEach(() => {
    window.localStorage.clear()
    // jsdom has no matchMedia; the responsive client table needs it once there are rows (wide screen).
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        matches: true,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    })
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
    resetNodeState()
    api.getMonitoring.mockResolvedValue(overview)
    api.getNocIncidents.mockResolvedValue({ items: [] })
    api.getConnectionHistory.mockResolvedValue({ points: [] })
    api.getResourceHistory.mockResolvedValue({ points: [] })
    api.openMonitoringStream.mockReturnValue({ close: vi.fn() })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
  })

  it('shows AWG 3 clients, their protocol badge and an online card', async () => {
    await mountWhileActiveNodeResolves(page, { nodes: [testNode] })

    expect(await screen.findByText('alice31')).toBeTruthy()
    expect(screen.getAllByText('AWG 3').length).toBeGreaterThan(0)
    expect(screen.getByText('AWG 3 онлайн')).toBeTruthy()
    const card = screen.getByText('AWG 3 онлайн').closest('div')!.parentElement!
    expect(card.textContent).toContain('из 2 пиров')
    // AWG 2 is still there next to it
    expect(screen.getByText('old-user')).toBeTruthy()
  })

  it('counts only fresh AWG 3 handshakes as online and includes them in the total', async () => {
    await mountWhileActiveNodeResolves(page, { nodes: [testNode] })
    await screen.findByText('alice31')

    expect(screen.getByText(/AWG 3 1/)).toBeTruthy()
    // total = 1 online AWG 2 + 1 online AWG 3 (the stale bob31 is not counted)
    expect(screen.getByText(/OVPN 0 · WG 0 · AWG 2 1 · AWG 3 1/)).toBeTruthy()
  })

  it('does not break on a server response without the AWG 3 block', async () => {
    api.getMonitoring.mockResolvedValue({ services: [], openvpn_clients: [], wireguard_peers: [], timestamp: fresh })
    await mountWhileActiveNodeResolves(page, { nodes: [testNode] })
    expect(await screen.findByText('AWG 3 онлайн')).toBeTruthy()
    const card = screen.getByText('AWG 3 онлайн').closest('div')!.parentElement!
    expect(card.textContent).toContain('из 0 пиров')
  })
})
