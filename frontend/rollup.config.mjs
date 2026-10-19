import { nodeResolve } from '@rollup/plugin-node-resolve'
import commonjs from '@rollup/plugin-commonjs'
import replace from '@rollup/plugin-replace'
import vue from 'rollup-plugin-vue'

// All CSS (SFC <style> blocks + plain .css imports) is turned into a runtime
// <style> injection so the build needs no CSS bundler plugin.
const cssInject = () => ({
  name: 'css-inject',
  transform(code, id) {
    if (!id.includes('lang.css') && !id.endsWith('.css')) return null
    return {
      code: `const css=${JSON.stringify(code)};if(typeof document!=='undefined'&&document.head){const s=document.createElement('style');s.setAttribute('data-src',${JSON.stringify(id.split(/[\\\\/]/).pop())});s.textContent=css;document.head.appendChild(s)}export default css;`,
      map: null
    }
  }
})

export default {
  input: 'src/main.js',
  output: { file: '../backend/app/static/assets/app.js', format: 'esm' },
  plugins: [
    replace({ preventAssignment: true, 'process.env.NODE_ENV': JSON.stringify('production') }),
    vue({ css: true }),
    cssInject(),
    nodeResolve({ browser: true, preferBuiltins: false }),
    commonjs({ include: /node_modules/ })
  ],
  onwarn(w, warn) { if (['CIRCULAR_DEPENDENCY','THIS_IS_UNDEFINED','SOURCEMAP_ERROR','EMPTY_BUNDLE'].includes(w.code)) return; warn(w) }
}
