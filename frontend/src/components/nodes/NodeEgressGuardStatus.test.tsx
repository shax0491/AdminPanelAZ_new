// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getNodeEgressStatus } from '@/api/client';
import type { Node } from '@/types';
import { NodeEgressGuardStatus } from './NodeEgressGuardStatus';

vi.mock('@/api/client', () => ({
  getNodeEgressStatus: vi.fn(),
}));

const mockedGet = vi.mocked(getNodeEgressStatus);

const vpnNode = { id: 7, node_kind: 'vpn' } as unknown as Node;
const proxyNode = { id: 8, node_kind: 'proxy' } as unknown as Node;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('NodeEgressGuardStatus', () => {
  it('не рендерится для proxy-узла', () => {
    const { container } = render(<NodeEgressGuardStatus node={proxyNode} />);
    expect(container.innerHTML).toBe("");
    expect(mockedGet).not.toHaveBeenCalled();
  });

  it('не загружает статус автоматически, первая загрузка по кнопке', () => {
    render(<NodeEgressGuardStatus node={vpnNode} />);
    expect(mockedGet).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /обновить/i })).toBeTruthy();
  });

  it('installed=false показывает подсказку про az-guard', async () => {
    mockedGet.mockResolvedValue({ installed: false, errors: [] });
    render(<NodeEgressGuardStatus node={vpnNode} />);
    fireEvent.click(screen.getByRole('button', { name: /обновить/i }));
    expect(await screen.findByText(/az-guard не установлен/i)).toBeTruthy();
    expect(screen.getByText('Guard: не установлен')).toBeTruthy();
    expect(mockedGet).toHaveBeenCalledWith(7);
  });

  it('активный guard с живым egress и handshake', async () => {
    mockedGet.mockResolvedValue({
      installed: true,
      errors: [],
      has_ipv6: false,
      egress_ifaces: ['ens3'],
      ingress_ips: ['10.0.0.1'],
      layers: [{ name: 'wg', iface: 'ens3', v4: ['10.0.0.0/24'], v6: [] }],
      base_rules: [{ family: 'inet', args: '-A AZGUARD-EGRESS -j RETURN' }],
      jump_first: { filter: true, mangle: true, nat: false },
      fwmark_rule: true,
      blackhole: true,
      egress_routes: { ens3: 'live' },
      guard_active: true,
      handshake_ages: { ens3: 25 },
      reverse: { iface: 'wg0', exists: true, handshake_age: 90 },
      panel_isolation: true,
      egress_ifaces_v6: {},
      layers_v6: [],
    });
    render(<NodeEgressGuardStatus node={vpnNode} />);
    fireEvent.click(screen.getByRole('button', { name: /обновить/i }));
    expect(await screen.findByText('Guard: активен')).toBeTruthy();
    expect(screen.getAllByText('ens3').length).toBeGreaterThan(0);
    expect(screen.getByText('live')).toBeTruthy();
    expect(screen.getByText('25 с назад')).toBeTruthy();
    expect(screen.getByText('панель изолирована:')).toBeTruthy();
  });

  it('ошибка API отображается через role=alert', async () => {
    mockedGet.mockRejectedValue(new Error('SSH timeout'));
    render(<NodeEgressGuardStatus node={vpnNode} />);
    fireEvent.click(screen.getByRole('button', { name: /обновить/i }));
    expect((await screen.findByRole('alert')).textContent).toContain('SSH timeout');
  });

  it('двойной клик не запускает повторный запрос', () => {
    let resolveFn: ((v: { installed: boolean; errors: string[] }) => void) | undefined;
    mockedGet.mockImplementation(
      () =>
        new Promise((res) => {
          resolveFn = res;
        }),
    );
    render(<NodeEgressGuardStatus node={vpnNode} />);
    const btn = screen.getByRole('button', { name: /обновить/i });
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(mockedGet).toHaveBeenCalledTimes(1);
    resolveFn?.({ installed: false, errors: [] });
  });
});
