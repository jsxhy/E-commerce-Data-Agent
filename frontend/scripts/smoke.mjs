/**
 * jsdom 冒烟：加载构建产物 app.js，确认应用挂载且首页/登录页能渲染。
 *
 * 用法（在 frontend 目录）：
 *   node scripts/smoke.mjs
 *
 * jsdom 是 devDependency，按以下顺序解析：
 *   1. 环境变量 JSDOM_PATH（指向 jsdom 包目录）
 *   2. frontend/node_modules/jsdom
 * 找不到时脚本以退出码 2 结束并打印安装提示，不算构建失败。
 */
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const bundle = resolve(root, '../backend/app/static/assets/app.js')

const jsdomCandidates = [
  process.env.JSDOM_PATH,
  resolve(root, 'node_modules/jsdom')
].filter(Boolean)

function findJsdom() {
  for (const dir of jsdomCandidates) {
    // jsdom 的 main 指向 ./lib/api.js（CJS）；老版本是 ./index.js，两者都试。
    for (const entry of ['lib/api.js', 'index.js']) {
      const file = resolve(dir, entry)
      if (existsSync(file)) return pathToFileURL(file).href
    }
  }
  return null
}

if (!existsSync(bundle)) {
  console.error(`[smoke] 找不到构建产物：${bundle}`)
  console.error('[smoke] 请先在 frontend 目录执行 node node_modules/rollup/dist/bin/rollup -c rollup.config.mjs')
  process.exit(1)
}

const jsdomHref = findJsdom()
if (!jsdomHref) {
  console.warn('[smoke] 未找到 jsdom，跳过冒烟测试。')
  console.warn('[smoke] 可执行：npm i -D jsdom  或 设置 JSDOM_PATH 环境变量')
  process.exit(2)
}

const { JSDOM } = await import(jsdomHref)

const dom = new JSDOM('<!doctype html><html><head></head><body><div id="app"></div></body></html>', {
  url: 'http://127.0.0.1:8000/',
  pretendToBeVisual: true,
  runScripts: 'outside-only'
})

const { window } = dom
const expose = [
  'window',
  'document',
  'navigator',
  'location',
  'history',
  'localStorage',
  'sessionStorage',
  'SVGElement',
  'Element',
  'Node',
  'HTMLElement',
  'HTMLUnknownElement',
  'DocumentFragment',
  'Text',
  'Comment',
  'DOMParser',
  'Event',
  'CustomEvent',
  'MouseEvent',
  'MutationObserver',
  'getComputedStyle',
  'requestAnimationFrame',
  'cancelAnimationFrame',
  'matchMedia',
  'ResizeObserver',
  'IntersectionObserver'
]
// Node 22+ 的 globalThis 上 navigator 等是只读 getter，必须用 defineProperty 覆盖。
function defineGlobal(key, value) {
  if (value === undefined) return
  try {
    Object.defineProperty(globalThis, key, {
      value,
      writable: true,
      configurable: true,
      enumerable: false
    })
  } catch (e) {
    /* 少数不可覆盖的内建值直接跳过 */
  }
}

for (const key of expose) {
  defineGlobal(key, window[key])
}
defineGlobal('window', window)
defineGlobal('document', window.document)
defineGlobal('navigator', window.navigator)
defineGlobal('location', window.location)
defineGlobal('history', window.history)
defineGlobal('localStorage', window.localStorage)

if (!window.matchMedia) {
  window.matchMedia = () => ({
    matches: false,
    media: '',
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return false
    }
  })
  globalThis.matchMedia = window.matchMedia
}

if (!window.ResizeObserver) {
  class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  window.ResizeObserver = ResizeObserver
  globalThis.ResizeObserver = ResizeObserver
}

if (!window.requestAnimationFrame) {
  window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16)
  window.cancelAnimationFrame = (id) => clearTimeout(id)
  globalThis.requestAnimationFrame = window.requestAnimationFrame
  globalThis.cancelAnimationFrame = window.cancelAnimationFrame
}

// jsdom 没有 canvas 实现，ECharts 会抛 "getContext() not implemented" 并中断渲染。
// 这里提供一个只够 ECharts 跑完 setOption 的 2D 上下文桩，让图表组件能被真正渲染。
function make2dContext() {
  const gradient = { addColorStop() {} }
  const base = {
    canvas: { width: 800, height: 300 },
    dpr: 1,
    measureText: () => ({ width: 0 }),
    createLinearGradient: () => gradient,
    createRadialGradient: () => gradient,
    createPattern: () => null,
    getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    putImageData() {},
    save() {},
    restore() {}
  }
  return new Proxy(base, {
    get(target, prop) {
      if (prop in target) return target[prop]
      return () => undefined
    },
    set(target, prop, value) {
      target[prop] = value
      return true
    }
  })
}

if (window.HTMLCanvasElement) {
  window.HTMLCanvasElement.prototype.getContext = function () {
    return make2dContext()
  }
  Object.defineProperty(window.HTMLCanvasElement.prototype, 'toDataURL', {
    value: () => 'data:image/png;base64,',
    writable: true,
    configurable: true
  })
}

// ---------------------------------------------------------------- 假后端
// 沙箱里没有后端也没有真实网络，这里给 axios 的 XHR 适配器一个桩，
// 按契约 §2/§7/§8 返回最小可用数据，用来验证三个页面真的能渲染出内容。
const state = { semanticMode: 'normal' }

function json(data) {
  return { code: 0, message: 'ok', data }
}

function fakeResponse(method, url) {
  const path = url.split('?')[0]
  if (path.endsWith('/auth/login')) {
    return json({
      token: 'smoke-token',
      user: { id: 1, username: 'admin', display_name: '管理员', role: 'admin' }
    })
  }
  if (path.endsWith('/reports/notifications')) {
    return json({ items: [{ id: 1 }], unread_count: 2 })
  }
  if (path.endsWith('/dashboard/overview')) {
    return json({
      updated_at: '2026-10-19 11:38:26',
      greeting: '你好，管理员',
      subtitle: '这是你的电商数据与 Agent 运行概览。',
      kpis: {
        product_count: { value: 20, label: '全部商品' },
        order_count: { value: 49, label: '累计订单' },
        review_count: { value: 46, label: '累计评价' },
        avg_rating: { value: 3.02, label: '满分 5 分' },
        paid_amount: { value: 7081.8, label: '订单实付合计（不含已取消）' },
        high_risk_reports: { value: 4, label: 'Agent 判定需人工确认' },
        unread_alerts: { value: 2, label: '站内通知' },
        pending_review: { value: 1, label: '人工复核状态' }
      },
      order_trend: {
        total_orders: 49,
        total_amount: 7170.8,
        points: [
          { date: '2026-09-20', count: 1, amount: 128 },
          { date: '2026-09-21', count: 3, amount: 320 }
        ]
      },
      rating_dist: {
        avg: 3.02,
        buckets: [1, 2, 3, 4, 5].map((star) => ({ star, count: star + 5 }))
      },
      attention_products: [
        { product_id: 3, name: '静音蓝牙耳机', category: '数码配件', bad_review_count: 3, avg_rating: 2.1 }
      ]
    })
  }
  if (path.endsWith('/dashboard/inspect')) {
    return json({ run_id: 21, report_count: 2, alert_count: 1, message: '巡检完成' })
  }
  if (path.endsWith('/observability/overview')) {
    return json({
      updated_at: '2026-10-19 11:38:26',
      kpis: {
        total_runs: { value: 20, label: '含进行中与已取消' },
        success_runs: { value: 12, label: '正常跑完' },
        failed_runs: { value: 5, label: '异常终止' },
        cancelled_runs: { value: 0, label: '用户放弃或中断' },
        unfinished_runs: { value: 3, label: '进行中/待确认' },
        success_rate: { value: 70.6, label: '已结束的运行里成功的比例', unit: '%' },
        avg_duration: { value: 21.8, label: '仅统计成功运行', unit: 's' },
        like_rate: { value: 100, label: '赞1 / 踩0', unit: '%' }
      },
      route_dist: [
        { route: 'structured', name: '结构化', count: 9 },
        { route: 'semantic', name: '语义检索', count: 6 },
        { route: 'undetermined', name: '未判定', count: 5 }
      ],
      stage_avg: [
        { stage: '数据查询', avg_ms: 11400 },
        { stage: '多角色分析', avg_ms: 2800 },
        { stage: '意图识别', avg_ms: 469 }
      ],
      daily_runs: {
        total: 20,
        points: [{ date: '2026-10-06', count: 1 }, { date: '2026-10-07', count: 2 }]
      }
    })
  }
  if (path.endsWith('/semantic/search')) {
    if (state.semanticMode === 'unavailable') {
      return json({
        query: '跑鞋脱胶',
        top_k: 5,
        hit_count: 0,
        collection_size: 0,
        embedding_mode: 'unavailable',
        threshold: 0.5,
        items: []
      })
    }
    return json({
      query: '跑鞋脱胶',
      top_k: 5,
      hit_count: 2,
      collection_size: 66,
      embedding_mode: 'bge-small-zh-onnx',
      threshold: 0.5,
      items: [
        {
          rank: 1,
          source: 'review',
          ref_id: 7,
          product_id: 3,
          product_name: '轻量缓震跑鞋',
          rating: 1,
          snippet: '跑了两次鞋底就开胶了，质量不行。',
          distance: 0.213,
          relevant: true
        },
        {
          rank: 2,
          source: 'product',
          ref_id: 3,
          product_id: 3,
          product_name: '轻量缓震跑鞋',
          rating: null,
          snippet: '轻量缓震跑鞋，适合日常慢跑。',
          distance: 0.512,
          relevant: false
        }
      ]
    })
  }
  // 其它接口（runs/reports/products/orders/reviews/users/chat）返回空壳
  return json({ items: [], total: 0 })
}

class FakeXHR {
  constructor() {
    this.readyState = 0
    this.status = 0
    this.statusText = ''
    this.responseText = ''
    this.response = ''
    this.responseURL = ''
    this.timeout = 0
    this.withCredentials = false
    this.upload = { addEventListener() {}, removeEventListener() {} }
    this.onloadend = null
    this.onerror = null
    this.ontimeout = null
    this.onabort = null
    this.onreadystatechange = null
    this._headers = {}
    this._url = ''
    this._method = 'GET'
  }

  open(method, url) {
    this._method = method
    this._url = url
    this.readyState = 1
  }

  setRequestHeader(key, value) {
    this._headers[key] = value
  }

  getAllResponseHeaders() {
    return 'content-type: application/json\r\n'
  }

  addEventListener() {}
  removeEventListener() {}
  abort() {}
  overrideMimeType() {}

  send() {
    setTimeout(() => {
      const body = fakeResponse(this._method, String(this._url || ''))
      this.readyState = 4
      this.status = 200
      this.statusText = 'OK'
      this.responseText = JSON.stringify(body)
      this.response = this.responseText
      this.responseURL = String(this._url || '')
      if (typeof this.onloadend === 'function') {
        this.onloadend({ type: 'loadend', target: this })
      } else if (typeof this.onreadystatechange === 'function') {
        this.onreadystatechange()
      }
    }, 4)
  }
}

defineGlobal('XMLHttpRequest', FakeXHR)

const failures = []

process.on('unhandledRejection', (err) => {
  const msg = String((err && err.message) || err)
  if (!/Network Error|ECONNREFUSED|Failed to fetch|status code 401|timeout/i.test(msg)) {
    failures.push(msg)
  }
})

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function go(path) {
  window.history.pushState({}, '', path)
  window.dispatchEvent(new window.PopStateEvent('popstate'))
}

function expect(cond, message) {
  if (!cond) failures.push(message)
}

try {
  // 1) 无 token → 路由守卫跳 /login
  await import(pathToFileURL(bundle).href)
  await sleep(1200)

  const app = document.getElementById('app')
  const html = app ? app.innerHTML : ''

  expect(html && html.length > 20, '挂载后 #app 内容为空')
  expect(html.includes('login-page') || html.includes('登录'), '未渲染登录页（无 token 时应跳转 /login）')
  expect(html.includes('默认账号 admin / admin123'), '登录页缺少默认账号提示')

  // 2) 有 token → 外壳 + 各页面
  window.localStorage.setItem('token', 'smoke-token')
  window.localStorage.setItem(
    'user',
    JSON.stringify({ id: 1, username: 'admin', display_name: '管理员', role: 'admin' })
  )

  go('/')
  await sleep(900)
  const homeHtml = app ? app.innerHTML : ''
  expect(homeHtml.includes('app-sidebar'), '有 token 时未渲染侧边栏外壳')
  expect(homeHtml.includes('电商数据分析 Agent'), '侧边栏缺少 logo 文案')
  const menuCount = (homeHtml.match(/class="[^"]*el-menu-item[^"]*"/g) || []).length
  expect(menuCount >= 10, `侧边栏菜单项不足 10 个（实际 ${menuCount}）`)
  expect(homeHtml.includes('你好，管理员'), '首页缺少问候语')
  expect(homeHtml.includes('¥7,081.80'), '首页累计成交未按千分位两位小数格式化')
  expect(homeHtml.includes('3.02'), '首页平均评分未显示两位小数')
  expect(homeHtml.includes('共 49 单'), '首页订单趋势标题未显示总单数')
  expect(homeHtml.includes('静音蓝牙耳机'), '首页重点关注商品未渲染')

  go('/observability')
  await sleep(900)
  const shellHtml = app ? app.innerHTML : ''
  expect(shellHtml.includes('Agent 运行观测'), '/observability 未渲染观测看板标题')
  expect(shellHtml.includes('总运行次数'), '/observability 未渲染 KPI 卡片')
  expect(shellHtml.includes('70.6%'), '/observability 成功率格式不对')
  expect(shellHtml.includes('21.8 s'), '/observability 平均耗时格式不对')
  expect(shellHtml.includes('共 20 次'), '/observability 近 14 天运行量未显示总次数')
  expect(shellHtml.includes('例序取前 8，按耗时降序'), '/observability 阶段耗时副标题缺失')
  // 阶段耗时标签画在 canvas 里，DOM 里看不到，只能对源码做一次格式守卫。
  const observabilitySrc = readFileSync(resolve(root, 'src/views/Observability.vue'), 'utf8')
  expect(/n >= 1000/.test(observabilitySrc) && /toFixed\(1\)\}s/.test(observabilitySrc), '/observability 阶段耗时 ≥1000ms 未转成 s')
  expect(/\}ms`/.test(observabilitySrc), '/observability 阶段耗时 <1000ms 未显示 ms')

  // 3) 语义检索：正常态
  go('/semantic')
  await sleep(900)
  const semanticHtml = app ? app.innerHTML : ''
  expect(semanticHtml.includes('语义检索'), '/semantic 未渲染检索页')
  expect(semanticHtml.includes('跑鞋脱胶'), '/semantic 未渲染快捷词')
  expect(semanticHtml.includes('命中 2 条 · 向量库共 66 条文档'), '/semantic 命中统计不对')
  expect(semanticHtml.includes('距离 0.213'), '/semantic 未渲染距离')
  expect(semanticHtml.includes('不相关'), '/semantic 未标记不相关片段')
  expect(semanticHtml.includes('来源买家评价'), '/semantic 未渲染评价来源说明')
  expect(semanticHtml.includes('来源商品描述'), '/semantic 未渲染商品来源说明')

  // 4) 语义检索：embedding 不可用降级态（重新挂载页面以重新触发检索）
  state.semanticMode = 'unavailable'
  go('/')
  await sleep(400)
  go('/semantic')
  await sleep(900)
  const downHtml = app ? app.innerHTML : ''
  expect(downHtml.includes('向量检索暂不可用'), '/semantic 降级态未显示 el-alert 警告')
  expect(
    downHtml.includes('python backend/scripts/fetch_model.py'),
    '/semantic 降级态未提示运行 fetch_model.py'
  )
  expect(!downHtml.includes('命中 0 条'), '/semantic 降级态不应只显示命中 0 条')

  console.log(`[smoke] bundle ${(readFileSync(bundle).length / 1024).toFixed(0)} KB`)
  console.log(
    `[smoke] login ${html.length} / home ${homeHtml.length} / observability ${shellHtml.length} / semantic ${semanticHtml.length} / degraded ${downHtml.length} chars, menu ${menuCount}`
  )

  if (failures.length) {
    console.error('[smoke] 失败：')
    for (const f of failures) console.error('  - ' + f)
    process.exit(1)
  }
  console.log('[smoke] PASS')
  process.exit(0)
} catch (err) {
  console.error('[smoke] 加载产物失败：', err && (err.stack || err.message || err))
  process.exit(1)
}
