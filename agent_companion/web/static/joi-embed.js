/**
 * Embed one Joi session in a host page, as one shell that changes size.
 *
 * The obvious build is two iframes -- a docked stage and a floating pet -- and
 * it works, because both connect to the same visitor Core and the Core
 * broadcasts to all of its clients. It also costs the visitor two Vue apps, two
 * WebSockets and two WebGL contexts for one character, which a phone feels.
 *
 * So there is one iframe, and it never moves in the DOM: reparenting an iframe
 * reloads it, which would drop the socket and restart the model every time the
 * page scrolls. Only the wrapper's geometry changes -- `absolute` at the
 * placeholder's document coordinates while docked, `fixed` in a corner while
 * floating -- and the shell is told which of the two it is showing. Docking by
 * document coordinates rather than viewport coordinates is what keeps this off
 * the scroll handler entirely.
 */

const STORAGE_PREFIX = 'joi.web.session.v1:'
const DEFAULT_PET_SIZE = { width: 300, height: 340 }
const GRAB_MARGIN = 80

function required(value, name) {
  if (!value) throw new Error(`${name} is required`)
  return String(value).replace(/\/$/, '')
}

function finite(value, fallback) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function frameUrl(shellBase, session, parentOrigin) {
  // `index.html`, not the directory: a static host that serves the shell out of
  // a framework's public/ directory has no directory-index rule, so
  // `/joi-shell/` is a 404 there while `/joi-shell/index.html` is the page.
  const url = new URL(`${shellBase}/index.html`, window.location.href)
  url.searchParams.set('mode', 'full')
  url.searchParams.set('guest', '1')
  url.searchParams.set('core', session.ws_url)
  url.searchParams.set('token', session.token)
  url.searchParams.set('parent_origin', parentOrigin)
  return url.toString()
}

async function fetchSession(brokerBase, stored) {
  if (stored?.session_id && stored?.token) {
    try {
      const response = await fetch(`${brokerBase}/session/${encodeURIComponent(stored.session_id)}`, {
        headers: { Authorization: `Bearer ${stored.token}` },
        cache: 'no-store',
      })
      if (response.ok) return response.json()
    } catch (_) {
      // A stale session is replaced below.
    }
  }
  const response = await fetch(`${brokerBase}/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
    cache: 'no-store',
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok || !payload.ok) {
    const error = new Error(payload.message || payload.error || 'Joi session unavailable')
    error.code = payload.error || 'session_unavailable'
    throw error
  }
  return payload
}

export async function mountJoi(options) {
  const brokerBase = required(options?.brokerBase, 'brokerBase')
  const shellBase = required(options?.shellBase, 'shellBase')
  const container = typeof options?.container === 'string'
    ? document.querySelector(options.container)
    : options?.container
  if (!(container instanceof HTMLElement)) throw new Error('A Joi container element is required')

  const storageKey = `${STORAGE_PREFIX}${new URL(brokerBase, window.location.href).origin}`
  let stored = null
  try { stored = JSON.parse(sessionStorage.getItem(storageKey) || 'null') } catch (_) {}
  const session = await fetchSession(brokerBase, stored)
  try {
    sessionStorage.setItem(storageKey, JSON.stringify({ session_id: session.session_id, token: session.token }))
  } catch (_) {
    // A session that cannot be remembered still works for this page view.
  }

  const parentOrigin = window.location.origin
  const shellOrigin = new URL(shellBase, window.location.href).origin

  // The placeholder holds the layout space so the surrounding article does not
  // reflow when the stage lifts out to the corner.
  const placeholder = document.createElement('div')
  placeholder.className = 'joi-embed-placeholder'
  container.replaceChildren(placeholder)

  const wrapper = document.createElement('div')
  wrapper.className = 'joi-embed-frame is-docked'
  const frame = document.createElement('iframe')
  frame.className = 'joi-embed-iframe'
  frame.title = '体验 Joi'
  frame.src = frameUrl(shellBase, session, parentOrigin)
  frame.allow = 'microphone; autoplay'
  frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms')
  wrapper.appendChild(frame)
  document.body.appendChild(wrapper)

  const margin = Math.max(8, finite(options?.petMargin, 24))
  const positionKey = `${storageKey}:position`
  let petSize = { ...DEFAULT_PET_SIZE }
  let petPosition = null
  let floating = false
  let frameId = 0
  try { petPosition = JSON.parse(sessionStorage.getItem(positionKey) || 'null') } catch (_) {}

  function defaultPetPosition() {
    return {
      x: window.innerWidth - petSize.width - margin,
      y: window.innerHeight - petSize.height - margin,
    }
  }

  /** Keep enough of her on screen that she can always be grabbed again. */
  function clampPet(position) {
    const fallback = defaultPetPosition()
    const x = finite(position?.x, fallback.x)
    const y = finite(position?.y, fallback.y)
    return {
      x: Math.min(window.innerWidth - GRAB_MARGIN, Math.max(GRAB_MARGIN - petSize.width, x)),
      y: Math.min(window.innerHeight - GRAB_MARGIN, Math.max(GRAB_MARGIN - petSize.height, y)),
    }
  }

  function applyDocked() {
    const rect = placeholder.getBoundingClientRect()
    // Document coordinates, so scrolling needs no handler at all.
    wrapper.style.position = 'absolute'
    wrapper.style.left = `${Math.round(rect.left + window.scrollX)}px`
    wrapper.style.top = `${Math.round(rect.top + window.scrollY)}px`
    wrapper.style.width = `${Math.round(rect.width)}px`
    wrapper.style.height = `${Math.round(rect.height)}px`
  }

  function applyFloating() {
    petPosition = clampPet(petPosition)
    wrapper.style.position = 'fixed'
    wrapper.style.left = `${Math.round(petPosition.x)}px`
    wrapper.style.top = `${Math.round(petPosition.y)}px`
    wrapper.style.width = `${petSize.width}px`
    wrapper.style.height = `${petSize.height}px`
    try { sessionStorage.setItem(positionKey, JSON.stringify(petPosition)) } catch (_) {}
  }

  function apply() {
    if (floating) applyFloating()
    else applyDocked()
  }

  function schedule() {
    if (frameId) return
    frameId = window.requestAnimationFrame(() => {
      frameId = 0
      apply()
    })
  }

  function setFloating(next) {
    if (floating === next) return
    floating = next
    wrapper.classList.toggle('is-floating', floating)
    wrapper.classList.toggle('is-docked', !floating)
    if (floating && !petPosition) petPosition = defaultPetPosition()
    // Docked is `absolute` in document coordinates and floating is `fixed` in
    // viewport coordinates, so `top` means two different things on either side
    // of this line. Letting CSS interpolate between them sends her through
    // coordinates that belong to neither -- off-screen for the whole
    // transition. Jump the geometry with transitions suppressed; they still
    // run for every move and scale *within* a mode, which is where they read
    // as motion rather than as a glitch.
    wrapper.classList.add('is-switching')
    apply()
    void wrapper.offsetWidth
    wrapper.classList.remove('is-switching')
    frame.contentWindow?.postMessage(
      { source: 'joi-embed', type: 'joi.set_compact', compact: floating },
      shellOrigin,
    )
  }

  apply()
  const observer = new IntersectionObserver(([entry]) => {
    setFloating(!(entry?.isIntersecting ?? true))
  }, { threshold: 0.12 })
  observer.observe(placeholder)

  // The docked box tracks its placeholder's size, not the window's: the article
  // around it can reflow without the window ever changing.
  const resizeObserver = new ResizeObserver(schedule)
  resizeObserver.observe(placeholder)

  function receive(event) {
    if (event.origin !== shellOrigin || event.source !== frame.contentWindow) return
    const message = event.data && typeof event.data === 'object' ? event.data : {}
    if (message.source !== 'joi-shell') return
    if (message.type === 'joi.resize') {
      petSize = {
        width: Math.min(520, Math.max(180, finite(message.width, petSize.width))),
        height: Math.min(760, Math.max(200, finite(message.height, petSize.height))),
      }
      if (floating) applyFloating()
    } else if (message.type === 'joi.drag' && floating) {
      petPosition = clampPet({
        x: clampPet(petPosition).x + finite(message.dx, 0),
        y: clampPet(petPosition).y + finite(message.dy, 0),
      })
      applyFloating()
      frame.contentWindow?.postMessage(
        { source: 'joi-embed', type: 'joi.position', ...petPosition },
        shellOrigin,
      )
    } else if (message.type === 'joi.position.restore') {
      petPosition = clampPet({ x: message.x, y: message.y })
      if (floating) applyFloating()
    } else if (message.type === 'joi.restore' || message.type === 'joi.open_cabin') {
      if (message.type === 'joi.open_cabin') {
        frame.contentWindow?.postMessage(
          { source: 'joi-embed', type: 'joi.open_cabin', cabin: message.cabin },
          shellOrigin,
        )
      }
      placeholder.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }

  /**
   * Release the visitor's Core when the document goes away.
   *
   * A framework's unmount hook does not run on a real page load, and a site
   * whose navigation is ordinary `<a href>` does nothing but real page loads.
   * Without this, every visitor who clicks a link leaves a Core process and a
   * workspace behind until the broker's idle sweep notices, which on a busy
   * day is the difference between a handful of live sessions and the
   * concurrency cap.
   *
   * `pagehide` rather than `unload`: it is the one that fires for the
   * back/forward cache too. `keepalive` lets the request outlive the document,
   * which a plain fetch here would not.
   */
  function releaseOnPageHide() {
    try {
      fetch(`${brokerBase}/session/${encodeURIComponent(session.session_id)}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${session.token}` },
        keepalive: true,
      }).catch(() => undefined)
    } catch (_) {
      // Nothing left to do from a document that is already going away.
    }
  }

  window.addEventListener('message', receive)
  window.addEventListener('resize', schedule)
  window.addEventListener('scroll', schedule, { passive: true })
  window.addEventListener('pagehide', releaseOnPageHide)

  return {
    session,
    frame,
    async destroy({ endSession = false } = {}) {
      observer.disconnect()
      resizeObserver.disconnect()
      window.removeEventListener('message', receive)
      window.removeEventListener('resize', schedule)
      window.removeEventListener('scroll', schedule)
      window.removeEventListener('pagehide', releaseOnPageHide)
      if (frameId) window.cancelAnimationFrame(frameId)
      wrapper.remove()
      placeholder.remove()
      if (endSession) {
        try { sessionStorage.removeItem(storageKey) } catch (_) {}
        await fetch(`${brokerBase}/session/${encodeURIComponent(session.session_id)}`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${session.token}` },
          keepalive: true,
        }).catch(() => undefined)
      }
    },
  }
}

window.JoiWeb = Object.freeze({ mount: mountJoi })
