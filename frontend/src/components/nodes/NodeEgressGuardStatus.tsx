import { useEffect, useRef, useState, type ReactNode } from 'react';
import { RefreshCw, ShieldCheck } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { getNodeEgressStatus } from '@/api/client';
import type { EgressGuardStatus, Node } from '@/types';

const V6_STATUS_LABELS: Record<string, string> = {
  disabled: 'IPv6 отключён',
  no_v6_subnets: 'нет IPv6-подсетей',
  profile_without_v6: 'профиль без IPv6',
  profile_missing: 'профиль не найден',
  active: 'активен',
};

function formatAge(age: number | null | undefined): string {
  if (age === null || age === undefined) return 'нет handshake';
  const total = Math.max(0, Math.floor(age));
  if (total < 60) return `${total} с назад`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  return `${Math.floor(minutes / 60)} ч назад`;
}

function routeBadge(state: string): ReactNode {
  if (state === 'live') {
    return <Badge className="bg-green-600 hover:bg-green-600">live</Badge>;
  }
  if (state === 'dead') {
    return <Badge variant="destructive">dead</Badge>;
  }
  return <Badge variant="secondary">missing</Badge>;
}

function Flag({ label, value }: { label: string; value: boolean | undefined }) {
  return (
    <span className="inline-flex items-center gap-1 text-sm">
      <span className="text-muted-foreground">{label}:</span>
      <Badge variant={value ? 'default' : 'secondary'}>{value ? 'да' : 'нет'}</Badge>
    </span>
  );
}

export function NodeEgressGuardStatus({ node }: { node: Node }) {
  const [status, setStatus] = useState<EgressGuardStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ignoreRef = useRef(false);

  useEffect(() => {
    return () => {
      ignoreRef.current = true;
    };
  }, []);

  if (node.node_kind !== 'vpn') return null;

  async function refresh() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await getNodeEgressStatus(node.id);
      if (ignoreRef.current) return;
      setStatus(result);
      setLoaded(true);
    } catch (e) {
      if (ignoreRef.current) return;
      setError(e instanceof Error ? e.message : 'Не удалось загрузить статус Egress Guard');
    } finally {
      if (!ignoreRef.current) setBusy(false);
    }
  }

  const guardBadge: ReactNode = (() => {
    if (!loaded || !status) return null;
    if (!status.installed) {
      return <Badge variant="secondary">Guard: не установлен</Badge>;
    }
    if (status.guard_active) {
      return <Badge className="bg-green-600 hover:bg-green-600">Guard: активен</Badge>;
    }
    return <Badge variant="destructive">Guard: не активен</Badge>;
  })();

  const errors = status?.errors ?? [];
  const egressIfaces = status?.egress_ifaces ?? [];
  const layers = status?.layers ?? [];
  const layersV6 = status?.layers_v6 ?? [];

  const flags: Array<[string, boolean | undefined]> = [];
  if (status?.installed) {
    if (status.jump_first) {
      const j = status.jump_first;
      const hasV6 = status.has_ipv6 === true;
      flags.push(['jump filter первым', j.filter]);
      if (hasV6) flags.push(['jump filter6 первым', j.filter6]);
      flags.push(['jump mangle первым', j.mangle]);
      if (hasV6) flags.push(['jump mangle6 первым', j.mangle6]);
      flags.push(['jump nat первым', j.nat]);
      if (hasV6) flags.push(['jump nat6 первым', j.nat6]);
    }
    flags.push(['fwmark-правило', status.fwmark_rule]);
    flags.push(['blackhole', status.blackhole]);
    flags.push(['панель изолирована', status.panel_isolation]);
  }

  return (
    <div className="rounded-md border p-3 space-y-3" data-testid="node-egress-guard">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4" aria-hidden />
          <span className="font-medium">Egress Guard</span>
          {guardBadge}
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void refresh()}
          disabled={busy}
        >
          <RefreshCw className={`mr-1 h-4 w-4 ${busy ? 'animate-spin' : ''}`} aria-hidden />
          Обновить
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-600">{error}</p>
      )}

      {!loaded && !busy && !error && (
        <p className="text-sm text-muted-foreground">
          Статус не загружен. Нажмите «Обновить», чтобы проверить состояние guard на узле
          (запрос выполняется через SSH/agent и автоматически не выполняется).
        </p>
      )}

      {loaded && status && !status.installed && (
        <p className="text-sm text-muted-foreground">
          az-guard не установлен на узле (см. README, установка через setup.sh).
        </p>
      )}

      {loaded && status?.installed && (
        <div className="space-y-3">
          {errors.length > 0 && (
            <ul role="alert" className="list-disc pl-5 space-y-1 text-sm text-red-600">
              {errors.map((msg, i) => (
                <li key={i}>{msg}</li>
              ))}
            </ul>
          )}

          {egressIfaces.length > 0 && (
            <div>
              <h4 className="mb-1 text-sm font-medium">Egress-интерфейсы</h4>
              <ul className="space-y-1">
                {egressIfaces.map((iface) => {
                  const routeState = status.egress_routes?.[iface] ?? 'missing';
                  const age = formatAge(status.handshake_ages?.[iface]);
                  return (
                    <li key={iface} className="flex items-center gap-2 text-sm">
                      <code className="font-mono">{iface}</code>
                      {routeBadge(routeState)}
                      <span className="text-muted-foreground">{age}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {layers.length > 0 && (
            <div>
              <h4 className="mb-1 text-sm font-medium">Слои</h4>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-muted-foreground">
                    <th className="py-1 pr-2 font-medium">Слой</th>
                    <th className="py-1 pr-2 font-medium">Интерфейс</th>
                    <th className="py-1 pr-2 font-medium">IPv4</th>
                    <th className="py-1 pr-2 font-medium">IPv6</th>
                  </tr>
                </thead>
                <tbody>
                  {layers.map((layer) => (
                    <tr key={`${layer.iface}:${layer.name}`} className="border-t">
                      <td className="py-1 pr-2">{layer.name}</td>
                      <td className="py-1 pr-2">
                        <code className="font-mono">{layer.iface}</code>
                      </td>
                      <td className="py-1 pr-2">{(layer.v4 ?? []).join(', ') || '—'}</td>
                      <td className="py-1 pr-2">{(layer.v6 ?? []).join(', ') || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {status.has_ipv6 === true && layersV6.length > 0 && (
            <div>
              <h4 className="mb-1 text-sm font-medium">IPv6-слои</h4>
              <ul className="space-y-1">
                {layersV6.map((layer) => (
                  <li
                    key={`${layer.iface}:${layer.name}`}
                    className="flex items-center gap-2 text-sm"
                  >
                    <span>{layer.name}</span>
                    <code className="font-mono text-muted-foreground">{layer.iface}</code>
                    <Badge variant="outline">
                      {V6_STATUS_LABELS[layer.v6_status] ?? layer.v6_status}
                    </Badge>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {flags.length > 0 && (
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {flags.map(([label, value]) => (
                <Flag key={label} label={label} value={value} />
              ))}
            </div>
          )}

          {status.reverse && (
            <p className="text-sm">
              Reverse-туннель: <code className="font-mono">{status.reverse.iface}</code>{' '}
              <Badge variant={status.reverse.exists ? 'default' : 'secondary'}>
                {status.reverse.exists ? 'настроен' : 'не настроен'}
              </Badge>{' '}
              <span className="text-muted-foreground">
                {formatAge(status.reverse.handshake_age)}
              </span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
