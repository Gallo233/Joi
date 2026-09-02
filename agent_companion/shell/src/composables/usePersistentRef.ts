/**
 * A ref that remembers its value across launches.
 *
 * Layout choices the user makes deliberately -- collapsing the character stage
 * to give the conversation the whole window -- should not be undone every time
 * the app restarts. Making someone re-set the same preference daily teaches
 * them the setting does not work.
 *
 * Storage failures are swallowed on purpose. A webview can have storage
 * disabled or full, and a preference that cannot be written is a preference
 * that lasts for this session -- not a reason to fail the surrounding feature.
 */

import { ref, watch, type Ref } from 'vue'

const NAMESPACE = 'joi.shell'

export function usePersistentRef<T>(key: string, fallback: T): Ref<T> {
  const storageKey = `${NAMESPACE}.${key}`
  const state = ref(read(storageKey, fallback)) as Ref<T>

  watch(state, (value) => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(value))
    } catch {
      // Preference stays for this session only.
    }
  })

  return state
}

function read<T>(storageKey: string, fallback: T): T {
  try {
    const stored = window.localStorage.getItem(storageKey)
    if (stored === null) return fallback
    const parsed = JSON.parse(stored) as unknown
    // A stored value whose shape no longer matches the code is worse than none:
    // it silently puts the UI into a state this version cannot represent.
    return typeof parsed === typeof fallback ? (parsed as T) : fallback
  } catch {
    return fallback
  }
}
