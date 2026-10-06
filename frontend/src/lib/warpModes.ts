/** Режимы встроенного WARP/Proton антизапрета: значения ANTIZAPRET_WARP и VPN_WARP в setup узла. */
export type WarpModeOption = { value: string; label: string; hint: string }

export const ANTIZAPRET_WARP_OPTIONS: readonly WarpModeOption[] = [
  { value: '1', label: 'Выключен', hint: 'Трафик выходит напрямую с адреса сервера, WARP не используется.' },
  { value: '2', label: 'Весь трафик', hint: 'Весь трафик антизапрета идёт через WARP. Самый медленный: всё упирается в один туннель.' },
  { value: '3', label: 'Домены + списки', hint: 'Через WARP идут все домены антизапрета (то, что получает fake-IP) и список WARP; IP-адреса без домена выходят напрямую.' },
  { value: '4', label: 'Только список', hint: 'Через WARP идёт только список WARP (include-warp-hosts.txt); остальной трафик антизапрета выходит напрямую с сервера. Самый быстрый вариант с WARP.' },
]

export const VPN_WARP_OPTIONS: readonly WarpModeOption[] = [
  { value: '1', label: 'Выключен', hint: 'Полный VPN выходит напрямую с адреса сервера.' },
  { value: '2', label: 'Весь трафик', hint: 'Весь трафик полного VPN идёт через WARP.' },
]

export function warpModeLabel(options: readonly WarpModeOption[], value: string | undefined): string {
  return options.find((option) => option.value === value)?.label ?? (value || '—')
}

/** Режимы 3 и 4 используют список WARP: без записей в нём через WARP ничего не пойдёт. */
export function warpModeUsesList(value: string | undefined): boolean {
  return value === '3' || value === '4'
}
