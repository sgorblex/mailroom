/// <reference types="node" />
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

import { describe, expect, it } from 'vitest'

import { BACKGROUNDS } from '@/theme/look.ts'

const web = (file: string): string => path.resolve(import.meta.dirname, '..', file)
const read = (file: string): string => readFileSync(web(file), 'utf8')

interface Icon {
  src: string
  sizes: string
  type: string
  purpose?: string
}

interface Manifest {
  name: string
  short_name: string
  start_url: string
  scope: string
  display: string
  theme_color: string
  background_color: string
  icons: Icon[]
}

const readManifest = (file: string): Manifest => JSON.parse(read(`public/${file}`)) as Manifest
const manifest = readManifest('manifest.webmanifest')
const staging = readManifest('manifest-staging.webmanifest')

/** A PNG's width and height, from its IHDR chunk. */
function pngSize(file: string): string {
  const bytes = readFileSync(file)
  return `${String(bytes.readUInt32BE(16))}x${String(bytes.readUInt32BE(20))}`
}

describe('the web app manifest', () => {
  it('installs the app at its root, standalone, on the light background', () => {
    expect(manifest).toMatchObject({ start_url: '/', scope: '/', display: 'standalone' })
    expect(manifest.theme_color).toBe(BACKGROUNDS.light)
    expect(manifest.background_color).toBe(BACKGROUNDS.light)
  })

  it.each([
    ['production', manifest, 'apple-touch-icon.png'],
    ['staging', staging, 'apple-touch-icon-staging.png'],
  ])('names, for %s, icons that exist at their stated sizes, a maskable one among them', (_, { icons }, appleTouch) => {
    expect(icons.filter((icon) => !existsSync(web(`public${icon.src}`)))).toEqual([])
    const pngs = icons.filter((icon) => icon.type === 'image/png')
    expect(pngs.map((icon) => pngSize(web(`public${icon.src}`)))).toEqual(pngs.map((icon) => icon.sizes))
    expect(icons.map((icon) => icon.sizes)).toEqual(expect.arrayContaining(['192x192', '512x512']))
    expect(icons.some((icon) => icon.purpose === 'maskable')).toBe(true)
    expect(pngSize(web(`public/${appleTouch}`))).toBe('180x180')
  })

  it('has a staging twin named Mailroom staging, with icons of its own and production\'s fields', () => {
    expect(manifest).toMatchObject({ name: 'Mailroom', short_name: 'Mailroom' })
    expect(staging).toMatchObject({ name: 'Mailroom staging', short_name: 'Mailroom staging' })
    // Each icon is production's with -staging in its name, so the two sets stay in step.
    expect(staging.icons.map((icon) => icon.src).filter((src) => !src.includes('-staging'))).toEqual([])
    const unstaged = staging.icons.map((icon) => ({ ...icon, src: icon.src.replace('-staging', '') }))
    expect({ ...staging, name: manifest.name, short_name: manifest.short_name, icons: unstaged }).toEqual(manifest)
  })

  it('is linked from index.html with the icons', () => {
    const html = read('index.html')
    expect(html).toContain('<link rel="manifest" href="/manifest.webmanifest" />')
    expect(html).toContain('<link rel="apple-touch-icon" href="/apple-touch-icon.png" />')
    expect(html).toContain('<link rel="icon" href="/icon.svg" type="image/svg+xml" />')
  })

  it('is served, staging\'s too, as application/manifest+json, under a CSP that lets the page load it', () => {
    const conf = read('nginx-default.conf')
    const location = /location ~ (\S+) \{\s*types \{ \}\s*default_type application\/manifest\+json;\s*\}/.exec(conf)
    const pattern = new RegExp(location?.[1] ?? '^$')
    expect(['/manifest.webmanifest', '/manifest-staging.webmanifest'].filter((uri) => !pattern.test(uri))).toEqual([])
    expect(pattern.test('/manifest-production.webmanifest')).toBe(false)
    // No manifest-src: default-src 'self' covers it.
    const policies = [...conf.matchAll(/Content-Security-Policy "([^"]+)"/g)].map(([, policy = '']) => policy)
    expect(policies.length).toBeGreaterThan(0)
    for (const policy of policies) {
      expect(policy).toMatch(/^default-src 'self';/)
      expect(policy).not.toContain('manifest-src')
    }
  })
})
