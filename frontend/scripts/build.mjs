/**
 * 构建脚本：Rollup 打包 + 拷贝 index.html 到后端静态目录。
 *
 * 为什么不用 `rollup -c` 加一个 copy 插件：本机沙箱禁止 Node 的
 * `child_process.spawn`（EPERM），所以 package.json 里不能再串第二个命令。
 * 这里直接调用 Rollup 的 JS API，在同一个进程里完成打包与拷贝。
 */
import { copyFile, mkdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { rollup } from 'rollup'

import config from '../rollup.config.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const staticDir = resolve(root, '../backend/app/static')

const startedAt = Date.now()

const bundle = await rollup(config)
const outputs = Array.isArray(config.output) ? config.output : [config.output]
for (const output of outputs) {
  await bundle.write(output)
  console.log(`✓ ${output.file}`)
}
await bundle.close()

await mkdir(staticDir, { recursive: true })
await copyFile(resolve(root, 'index.html'), resolve(staticDir, 'index.html'))
console.log(`✓ ${resolve(staticDir, 'index.html')}`)

console.log(`构建完成，耗时 ${((Date.now() - startedAt) / 1000).toFixed(1)}s`)
