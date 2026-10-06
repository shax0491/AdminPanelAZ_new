// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import WarpGeoPage from './WarpGeoPage'

const api = vi.hoisted(() => ({
  listWarpGeoNodes: vi.fn(),
  getWarpGeoStatus: vi.fn(),
  checkWarpGeo: vi.fn(),
  saveWarpProtonFields: vi.fn(),
  setWarpProvider: vi.fn(),
  setWarpModes: vi.fn(),
  applyWarpChanges: vi.fn(),
  testCloudflareWarpPreview: vi.fn(),
}))

vi.mock('@/api/warpGeo', () => api)

const FIELDS = { public_key: '', address: '', endpoint_host: '', endpoint_port: '' }

function status(overrides: Record<string, unknown> = {}) {
  return {
    warp_provider: 'proton',
    antizapret_warp: '2',
    vpn_warp: '2',
    live_antizapret_warp: 'all',
    live_vpn_warp: 'all',
    pending_apply: false,
    pending_scopes: [],
    proton_antizapret_configured: true,
    proton_vpn_configured: true,
    proton_antizapret_fields: FIELDS,
    proton_vpn_fields: FIELDS,
    ...overrides,
  }
}

async function openPage() {
  render(<WarpGeoPage />)
  await screen.findByText('Режим WARP')
}

describe('WarpGeoPage mode switching', () => {
  beforeEach(() => {
    api.listWarpGeoNodes.mockResolvedValue({ nodes: [{ id: 5, name: 'nl1', status: 'online' }] })
    api.getWarpGeoStatus.mockResolvedValue(status())
    api.checkWarpGeo.mockResolvedValue({ scope: 'antizapret', interface: 'warp-antizapret' })
    api.setWarpModes.mockResolvedValue({ success: true, antizapret_warp: '4', vpn_warp: null })
    api.applyWarpChanges.mockResolvedValue({ success: true, output: 'ok' })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('does not write anything until the user confirms, then saves and applies in that order', async () => {
    await openPage()

    fireEvent.click(screen.getAllByRole('button', { name: 'Только список' })[0])

    await screen.findByText('Сохранить и применить')
    expect(screen.getByText('Весь трафик → Только список')).toBeTruthy()
    expect(api.setWarpModes).not.toHaveBeenCalled()
    expect(api.applyWarpChanges).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Сохранить и применить' }))

    await waitFor(() => expect(api.applyWarpChanges).toHaveBeenCalledTimes(1))
    expect(api.setWarpModes).toHaveBeenCalledWith(5, { antizapret: '4' })
    expect(api.setWarpModes.mock.invocationCallOrder[0]).toBeLessThan(api.applyWarpChanges.mock.invocationCallOrder[0])
    await screen.findByText(/Режим сохранён и применён/)
  })

  it('cancel leaves the node untouched', async () => {
    await openPage()
    fireEvent.click(screen.getAllByRole('button', { name: 'Только список' })[0])
    fireEvent.click(await screen.findByRole('button', { name: 'Отмена' }))

    await waitFor(() => expect(screen.queryByText('Сохранить и применить')).toBeNull())
    expect(api.setWarpModes).not.toHaveBeenCalled()
    expect(api.applyWarpChanges).not.toHaveBeenCalled()
  })

  it('switches the full VPN scope through the same confirmation', async () => {
    await openPage()
    fireEvent.click(screen.getAllByRole('button', { name: 'Выключен' }).slice(-1)[0])
    fireEvent.click(await screen.findByRole('button', { name: 'Сохранить и применить' }))
    await waitFor(() => expect(api.setWarpModes).toHaveBeenCalledWith(5, { vpn: '1' }))
    expect(api.applyWarpChanges).toHaveBeenCalledTimes(1)
  })

  it('shows the up.sh failure instead of claiming success', async () => {
    api.applyWarpChanges.mockResolvedValue({ success: false, output: 'iptables: boom' })
    await openPage()
    fireEvent.click(screen.getAllByRole('button', { name: 'Только список' })[0])
    fireEvent.click(await screen.findByRole('button', { name: 'Сохранить и применить' }))
    await screen.findByText(/up\.sh завершился с ошибкой: iptables: boom/)
  })

  it('does not apply when saving the mode fails', async () => {
    api.setWarpModes.mockRejectedValue(new Error('Файл setup на узле не найден'))
    await openPage()
    fireEvent.click(screen.getAllByRole('button', { name: 'Только список' })[0])
    fireEvent.click(await screen.findByRole('button', { name: 'Сохранить и применить' }))
    await screen.findByText('Файл setup на узле не найден')
    expect(api.applyWarpChanges).not.toHaveBeenCalled()
  })

  it('marks a scope whose saved mode is not applied yet and lets the user re-apply the current value', async () => {
    api.getWarpGeoStatus.mockResolvedValue(
      status({ antizapret_warp: '4', live_antizapret_warp: 'all', pending_apply: true, pending_scopes: ['antizapret'] }),
    )
    await openPage()

    expect(await screen.findByText('не применено')).toBeTruthy()

    // the already selected mode can be clicked again exactly because it was never applied
    fireEvent.click(screen.getAllByRole('button', { name: 'Только список' })[0])
    fireEvent.click(await screen.findByRole('button', { name: 'Сохранить и применить' }))
    await waitFor(() => expect(api.applyWarpChanges).toHaveBeenCalledTimes(1))
  })

  it('ignores a click on the active and already applied mode', async () => {
    await openPage()
    fireEvent.click(screen.getAllByRole('button', { name: 'Весь трафик' })[0])
    expect(screen.queryByText('Сохранить и применить')).toBeNull()
    expect(api.setWarpModes).not.toHaveBeenCalled()
  })
})
