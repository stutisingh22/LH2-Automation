let count = 0
const listeners = new Set<(n: number) => void>()

export function subscribeApiLoading(fn: (n: number) => void): () => void {
  listeners.add(fn)
  fn(count)
  return () => {
    listeners.delete(fn)
  }
}

function emit() {
  listeners.forEach((fn) => fn(count))
}

export function apiLoadingBegin(): void {
  count += 1
  emit()
}

export function apiLoadingEnd(): void {
  count = Math.max(0, count - 1)
  emit()
}
