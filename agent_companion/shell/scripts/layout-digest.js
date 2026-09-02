/**
 * Layout digest: a diffable fingerprint of how a view actually renders.
 *
 * S3 of the shell refactor moves ~6000 lines of template into components and
 * claims to change nothing on screen. "Looks the same to me" is not evidence
 * for a claim that specific, and pixel baselines would mean adding a headless
 * browser to a project that has never needed one. Geometry plus typography
 * plus colour, per element, catches what actually regresses -- a bubble that
 * shifted 3px, text that got lighter, a panel that lost its fill -- and diffs
 * as plain text.
 *
 * Usage: with the view on screen, paste into the browser console (or the
 * preview javascript tool) and call `captureDigest('chat@1440')`. The dev
 * server writes it to tests/baseline/chat@1440.digest.txt.
 *
 * Note: the conversation view includes whatever turns are in the local Core
 * database, so capture "before" and "after" against the same Core state.
 */
globalThis.captureDigest = async (name) => {
  const round = (n) => Math.round(n)

  /*
   * Freeze-proofing.
   *
   * A headless or backgrounded tab does not paint, and a CSS animation in a
   * tab that does not paint sits at 0% forever: `getAnimations()` reports it
   * as "running" with `currentTime: 0`, and every animated element measures at
   * its `from` keyframe. That produced a 38-line "regression" in the project
   * sheet -- it read as x=-332, its slide-in start -- when the app on screen
   * was perfectly correct.
   *
   * The digest is meant to record where things come to rest, so cancel any
   * running animations and read the resting layout. This makes a capture
   * identical whether or not the tab happens to be painting.
   */
  for (const element of document.querySelectorAll('*')) {
    for (const animation of element.getAnimations?.() ?? []) animation.finish()
  }
  void document.body.offsetHeight // force style/layout flush before measuring

  const measure = () =>
    [...document.querySelectorAll('[class]')]
      .filter((el) => {
        const rect = el.getBoundingClientRect()
        return rect.width > 0 && rect.height > 0
      })

  /*
   * Wait for layout to settle.
   *
   * A panel opened moments ago is still laying out, and elements that have not
   * been given their size yet measure as zero and drop out of the digest
   * entirely. That produced a second false regression: the BYOK provider grid
   * appeared to vanish after a refactor when it was simply measured too early,
   * and re-measuring the same page a moment later matched the baseline exactly.
   *
   * Take the digest twice and only accept it once two consecutive reads agree.
   */
  let previous = ''
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const current = measure().length + ':' + measure().map((el) => el.getBoundingClientRect().height).join(',')
    if (current === previous) break
    previous = current
    await new Promise((resolve) => setTimeout(resolve, 100))
  }

  const digest = [...document.querySelectorAll('[class]')]
    .filter((el) => {
      const rect = el.getBoundingClientRect()
      return rect.width > 0 && rect.height > 0
    })
    .map((el) => {
      const rect = el.getBoundingClientRect()
      const style = getComputedStyle(el)
      const className = typeof el.className === 'string' ? el.className : el.getAttribute('class')
      return [
        className.trim().replace(/\s+/g, '.'),
        `${round(rect.x)},${round(rect.y)},${round(rect.width)},${round(rect.height)}`,
        style.fontSize,
        style.color,
        style.backgroundColor,
        style.zIndex,
      ].join('|')
    })
    .join('\n')

  const response = await fetch(`/__digest?name=${encodeURIComponent(name)}`, { method: 'POST', body: digest })
  return `${await response.text()} (${digest.split('\n').length} elements)`
}
