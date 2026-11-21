/**
 * 用 Chrome DevTools Protocol 模拟真机视口截图。
 *
 * 为什么需要：chrome --headless --window-size=390 在 Windows 上有约 485px 的最小窗口宽度，
 * 用 390 截图会得到「页面右侧被裁切」的假象，容易误判为布局溢出。
 * 走 CDP 的 Emulation.setDeviceMetricsOverride 才能真正得到 390 宽的视口。
 *
 * 用法： node scripts/screenshot-mobile.js <url> <输出路径> [宽] [高]
 */
const { spawn } = require('child_process')
const fs = require('fs')
const path = require('path')

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const url = process.argv[2] || 'https://invest.mengrengjiu.com/'
const out = process.argv[3] || 'docs/screenshots/mobile.png'
const width = Number(process.argv[4] || 390)
const height = Number(process.argv[5] || 844)
const waitMs = Number(process.argv[6] || 9000)

const profile = path.join(process.cwd(), '.cdp-profile')
fs.rmSync(profile, { recursive: true, force: true })

const chrome = spawn(CHROME, [
  '--headless=new',
  '--disable-gpu',
  '--no-sandbox',
  '--hide-scrollbars',
  '--remote-debugging-port=9333',
  `--user-data-dir=${profile}`,
  'about:blank',
], { stdio: 'ignore' })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  await sleep(2500)
  const ver = await fetch('http://127.0.0.1:9333/json/version').then((r) => r.json())
  const ws = new WebSocket(ver.webSocketDebuggerUrl)
  let seq = 0
  const pending = new Map()

  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg)
      pending.delete(msg.id)
    }
  })
  await new Promise((r) => ws.addEventListener('open', r, { once: true }))

  const send = (method, params = {}, sessionId) =>
    new Promise((resolve) => {
      const id = ++seq
      pending.set(id, resolve)
      ws.send(JSON.stringify({ id, method, params, sessionId }))
    })

  const created = await send('Target.createTarget', { url: 'about:blank' })
  const targetId = created.result.targetId
  const attached = await send('Target.attachToTarget', { targetId, flatten: true })
  const sessionId = attached.result.sessionId

  await send('Page.enable', {}, sessionId)
  await send('Emulation.setDeviceMetricsOverride', {
    width, height, deviceScaleFactor: 2, mobile: true,
  }, sessionId)
  await send('Emulation.setUserAgentOverride', {
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  }, sessionId)

  await send('Page.navigate', { url }, sessionId)
  await sleep(waitMs)

  // 顺便量一下有没有横向溢出，比肉眼看截图可靠
  const metrics = await send('Runtime.evaluate', {
    expression: 'JSON.stringify({vw: innerWidth, sw: document.documentElement.scrollWidth, dpr: devicePixelRatio})',
    returnByValue: true,
  }, sessionId)
  console.log('视口指标:', metrics.result?.result?.value || '未知')

  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, sessionId)
  fs.mkdirSync(path.dirname(out), { recursive: true })
  fs.writeFileSync(out, Buffer.from(shot.result.data, 'base64'))
  console.log('已保存:', out)
  ws.close()
}

main()
  .catch((err) => { console.error('截图失败:', err.message); process.exitCode = 1 })
  .finally(() => {
    try { chrome.kill() } catch (_) {}
    setTimeout(() => { fs.rmSync(profile, { recursive: true, force: true }) }, 500)
  })
