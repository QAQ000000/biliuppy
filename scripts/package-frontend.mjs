import { cpSync, existsSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'

const source = resolve('out')
const target = resolve('biliup/web/public')

if (!existsSync(source)) {
  throw new Error(`Next.js export not found: ${source}`)
}

rmSync(target, { recursive: true, force: true })
cpSync(source, target, { recursive: true })
console.log(`Packaged frontend in ${target}`)
