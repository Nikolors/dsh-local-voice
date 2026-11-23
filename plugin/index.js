import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { readFileSync, unlinkSync } from 'node:fs'
import net from 'node:net'

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const PID_FILE = 'C:\\work\\workspace\\.tmp\\qwen-server.pid'

function isPortOpen(port, host = '127.0.0.1', timeout = 400) {
  return new Promise((resolve) => {
    const socket = net.connect({ port, host })
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      socket.destroy()
      resolve(value)
    }
    socket.setTimeout(timeout)
    socket.once('connect', () => finish(true))
    socket.once('timeout', () => finish(false))
    socket.once('error', () => finish(false))
  })
}

/**
 * Голосовой сервер запускаем через проводник: из процесса приложения прямой
 * запуск pythonw падает при инициализации CUDA (ограничения задания хост-процесса),
 * а explorer.exe стартует уже вне их. Сервер сам пишет свой PID в файл, поэтому
 * на выходе приложения его можно погасить по номеру.
 */
export function apply(ctx, config = {}) {
  const port = Number(config.port ?? 8895)
  const explorer = 'C:\\Windows\\explorer.exe'
  const launcher = config.launcher ?? 'C:\\work\\workspace\\tools\\qwen-tts-server-hidden.cmd'
  const stopper = config.stopper ?? 'C:\\work\\workspace\\tools\\qwen-tts-stop.cmd'
  const watchFile = config.watchFile ?? 'C:\\work\\workspace\\.tmp\\qwen-watch.pid'

  const stop = () => {
    try {
      spawn(explorer, [stopper], { detached: true, stdio: 'ignore', windowsHide: true }).unref()
      ctx.logger?.info?.(`voice server stop requested via explorer (${stopper})`)
    } catch (error) {
      ctx.logger?.warn?.(`voice server stop failed: ${error?.message ?? error}`)
    }
  }

  const start = async () => {
    // Пишем номер процесса приложения: сервер сам выключится, когда приложение закроется.
    try {
      writeFileSync(watchFile, String(process.pid), 'ascii')
    } catch (error) {
      ctx.logger?.warn?.(`voice server watch file failed: ${error?.message ?? error}`)
    }
    if (await isPortOpen(port)) {
      ctx.logger?.info?.(`voice server already listening on ${port}`)
      return
    }
    try {
      spawn(explorer, [launcher], { detached: true, stdio: 'ignore', windowsHide: true }).unref()
      ctx.logger?.info?.(`voice server launch requested via explorer (${launcher})`)
    } catch (error) {
      ctx.logger?.warn?.(`voice server launch failed: ${error?.message ?? error}`)
      return
    }

    for (let attempt = 0; attempt < 90; attempt += 1) {
      await sleep(1000)
      if (await isPortOpen(port)) {
        ctx.logger?.info?.(`voice server ready on ${port}`)
        return
      }
    }
    ctx.logger?.warn?.(`voice server did not open port ${port} in time`)
  }

  start()
  ctx.on('dispose', stop)
}
