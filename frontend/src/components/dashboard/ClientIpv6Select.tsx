import type { ClientIpv6Profile } from '@/api/ipv6'
import Ipv6Badge, { useIpv6Status } from '@/components/dashboard/Ipv6Badge'
import { cn } from '@/lib/utils'

const OPTIONS: { value: ClientIpv6Profile; label: string }[] = [

  { value: 'ipv4', label: 'Только IPv4' },
  { value: 'dual', label: 'Dual-Stack (IPv4 + IPv6)' },
]

interface Props {
  id: string
  value: ClientIpv6Profile
  onChange: (value: ClientIpv6Profile) => void
  disabled?: boolean
  className?: string
}

export default function ClientIpv6Select({ id, value, onChange, disabled, className }: Props) {
  const status = useIpv6Status()
  // «default» в интерфейсе не показываем: берём IPV6_PROFILE_DEFAULT узла
  const shown = value === 'default' ? (status?.profile_default === 'ipv4' ? 'ipv4' : 'dual') : value
  return (
    <div className="space-y-1.5">
      <select
        id={id}
        className={cn('w-full rounded-md border border-input bg-background px-3 text-sm', className)}
        value={shown}
        onChange={(e) => onChange(e.target.value as ClientIpv6Profile)}
        disabled={disabled}
      >
        {OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <Ipv6Badge status={status} />
      {status?.effective_mode === 'a' && shown === 'dual' && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          На этом VPS сейчас выключен выходной IPv6 (работает защита от утечек через IPv4). Для работы Dual-Stack включите IPv6 на узле
          (Native/WARP/Proton) или выберите узел с активным IPv6.
        </p>
      )}
    </div>
  )
}
