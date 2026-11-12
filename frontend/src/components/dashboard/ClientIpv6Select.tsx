import type { ClientIpv6Profile } from '@/api/ipv6'
import { cn } from '@/lib/utils'

const OPTIONS: { value: ClientIpv6Profile; label: string }[] = [
  { value: 'default', label: 'По умолчанию узла' },
  { value: 'ipv4', label: 'Только IPv4' },
  { value: 'dual', label: 'IPv4 + IPv6 (dual)' },
]

interface Props {
  id: string
  value: ClientIpv6Profile
  onChange: (value: ClientIpv6Profile) => void
  disabled?: boolean
  className?: string
}

export default function ClientIpv6Select({ id, value, onChange, disabled, className }: Props) {
  return (
    <select
      id={id}
      className={cn('w-full rounded-md border border-input bg-background px-3 text-sm', className)}
      value={value}
      onChange={(e) => onChange(e.target.value as ClientIpv6Profile)}
      disabled={disabled}
    >
      {OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}
