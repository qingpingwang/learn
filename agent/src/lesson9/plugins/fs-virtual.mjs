// dsh 虚拟文件系统插件：提供 ctx.fs
// 方法签名照 0.1.5rc1 的 @deepseek-ai/dsh-fs 抽象类 FileSystem 逐个对齐
// （resolve / processPath / processPathFromHostPath / fileUrl / contains / stat / lstat /
//  readText / streamText / readBytes / readByteRange / listDir / writeText / editText）。
// 单文件运行时里本地插件 import 不到 @deepseek-ai/dsh-fs，所以不继承那个类，
// 而是写一个同形状的普通对象，用 ctx.provide('fs', ...) 挂上去。
//
// 真正的读写交给 Storage 适配器：
//   LocalDiskStorage —— 落在本机磁盘；root 只是相对路径的解析起点，
//                       不是围栏（和官方 fs-local 一样）。技能目录在会话
//                       cwd 之外，skill-filesystem 会通过 ctx.fs 读绝对路径，
//                       所以这里不能把越出 root 的路径一律拒绝。
//   RemoteApiStorage —— 远程后端占位，未实现、未测试。

import { promises as fsp } from 'node:fs'
import { appendFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve as resolvePath, sep } from 'node:path'
import { pathToFileURL } from 'node:url'
import { randomBytes } from 'node:crypto'

export const name = 'fs-virtual'

class FsError extends Error {
  constructor(message, code) {
    super(message)
    this.name = 'FsError'
    this.code = code
  }
}

function aborted(signal, op) {
  if (signal?.aborted) throw new FsError(`${op} aborted`, 'FS_ABORTED')
}

function mapIo(e, where) {
  if (e instanceof FsError) return e
  if (e.code === 'ENOENT') return new FsError(`not found: ${where}`, 'FS_NOT_FOUND')
  if (e.code === 'ENOTDIR') return new FsError(`not a directory: ${where}`, 'FS_NOT_DIRECTORY')
  if (e.code === 'EISDIR') return new FsError(`not a regular file: ${where}`, 'FS_NOT_REGULAR_FILE')
  if (e.code === 'EACCES' || e.code === 'EPERM') return new FsError(`permission denied: ${where}`, 'FS_PERMISSION_DENIED')
  return new FsError(`io error on ${where}: ${e.message}`, 'FS_IO_ERROR')
}

const version = (st) => `${Math.trunc(st.mtimeMs)}:${st.size}`
const lf = (s) => s.replace(/\r\n/g, '\n')

function decodeText(buf, displayPath) {
  if (buf.includes(0)) throw new FsError(`not a text file: ${displayPath}`, 'FS_NOT_TEXT')
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buf)
  } catch {
    throw new FsError(`not a text file: ${displayPath}`, 'FS_NOT_TEXT')
  }
}

// ---------- 存储适配器 ----------
class LocalDiskStorage {
  constructor(root) {
    this.root = resolvePath(root)
    this.kind = 'local-disk'
  }

  static typeOf(st) {
    if (st.isFile()) return 'file'
    if (st.isDirectory()) return 'directory'
    if (st.isSymbolicLink()) return 'symlink'
    return 'other'
  }

  // key 就是本机绝对路径（和 fs-local 的 targetKey 一样）
  async stat(abs, follow = true) {
    try {
      const st = follow ? await fsp.stat(abs) : await fsp.lstat(abs)
      return { type: LocalDiskStorage.typeOf(st), size: st.size, mtimeMs: st.mtimeMs }
    } catch (e) {
      if (e.code === 'ENOENT') return null
      throw mapIo(e, abs)
    }
  }

  async list(abs) {
    try {
      const entries = await fsp.readdir(abs, { withFileTypes: true })
      const out = []
      for (const ent of entries) {
        const child = join(abs, ent.name)
        const st = await this.stat(child)
        out.push({
          name: ent.name,
          abs: child,
          type: st ? (st.type === 'symlink' ? 'other' : st.type) : 'other',
          size: st?.size,
          mtimeMs: st?.mtimeMs,
        })
      }
      return out
    } catch (e) {
      throw mapIo(e, abs)
    }
  }

  async read(abs) {
    try { return await fsp.readFile(abs) }
    catch (e) { throw mapIo(e, abs) }
  }

  async write(abs, buffer) {
    try {
      await fsp.mkdir(dirname(abs), { recursive: true })
      const tmp = `${abs}.${randomBytes(4).toString('hex')}.tmp`
      await fsp.writeFile(tmp, buffer)
      await fsp.rename(tmp, abs)
    } catch (e) {
      throw mapIo(e, abs)
    }
  }
}

// ---------- 远程后端占位（未实现、未测试） ----------
// TODO(remote): 以后接对象存储或自建文件服务时实现这几个方法，
// 比如 stat -> HEAD /files/{key}，list -> GET /files/{key}?list=1，
// read -> GET /files/{key}，write -> PUT /files/{key}。
class RemoteApiStorage {
  constructor(endpoint) {
    this.endpoint = endpoint
    this.root = undefined
    this.kind = 'remote-api(stub)'
  }
  async stat() { throw new FsError('RemoteApiStorage 还没实现', 'FS_IO_ERROR') }
  async list() { throw new FsError('RemoteApiStorage 还没实现', 'FS_IO_ERROR') }
  async read() { throw new FsError('RemoteApiStorage 还没实现', 'FS_IO_ERROR') }
  async write() { throw new FsError('RemoteApiStorage 还没实现', 'FS_IO_ERROR') }
}

// ---------- ctx.fs 实现 ----------
class VirtualFileSystem {
  constructor(storage, log) {
    this.storage = storage
    this.log = log
    this.locks = new Map()
  }

  get sandboxMode() { return undefined }

  // 相对路径相对 root（会话 cwd）解析；绝对路径原样用。和 fs-local 一样不做围栏。
  absOf(path, cwd) {
    const base = cwd ?? this.storage.root ?? process.cwd()
    return resolvePath(base, path)
  }

  targetOf(abs) {
    // displayPath 也用绝对路径：skill-filesystem 会拿它去 join('SKILL.md')
    return { targetKey: abs, displayPath: abs }
  }

  absFromTarget(target) {
    return String(target.targetKey)
  }

  async resolve(path, opts) {
    aborted(opts?.signal, 'resolve')
    if (typeof path !== 'string' || path.trim() === '') throw new FsError('file_path must be a non-empty string', 'FS_NOT_FOUND')
    const abs = this.absOf(path, opts?.cwd)
    this.log('resolve', abs)
    return this.targetOf(abs)
  }

  processPath(target) {
    return this.absFromTarget(target)
  }

  processPathFromHostPath(hostPath) {
    return isAbsolute(hostPath) ? resolvePath(hostPath) : undefined
  }

  fileUrl(target) {
    return pathToFileURL(this.absFromTarget(target)).href
  }

  contains(parent, child) {
    const p = this.absFromTarget(parent)
    const c = this.absFromTarget(child)
    const rel = relative(p, c)
    return rel === '' || (rel !== '..' && !rel.startsWith(`..${sep}`) && !isAbsolute(rel))
  }

  async stat(target, signal) {
    aborted(signal, 'stat')
    const st = await this.storage.stat(this.absFromTarget(target))
    if (!st) return undefined
    return { version: version(st), type: st.type === 'symlink' ? 'other' : st.type, size: st.type === 'file' ? st.size : undefined }
  }

  async lstat(path, opts, signal) {
    aborted(signal, 'lstat')
    if (typeof path !== 'string' || path.trim() === '') throw new FsError('file_path must be a non-empty string', 'FS_NOT_FOUND')
    const st = await this.storage.stat(this.absOf(path, opts?.cwd), false)
    if (!st) return undefined
    return { version: version(st), type: st.type, size: st.type === 'file' ? st.size : undefined }
  }

  async requireFile(target) {
    const abs = this.absFromTarget(target)
    const st = await this.storage.stat(abs)
    if (!st) throw new FsError(`not found: ${target.displayPath}`, 'FS_NOT_FOUND')
    if (st.type !== 'file') throw new FsError(`not a regular file: ${target.displayPath}`, 'FS_NOT_REGULAR_FILE')
    return st
  }

  async readText(target, signal) {
    aborted(signal, 'read')
    await this.requireFile(target)
    const text = decodeText(await this.storage.read(this.absFromTarget(target)), target.displayPath)
    this.log('readText', target.displayPath, `${text.length} chars`)
    return text
  }

  async streamText(target, signal) {
    const text = await this.readText(target, signal)
    return (async function* () { yield text })()
  }

  async readBytes(target, signal, maxBytes) {
    aborted(signal, 'read')
    const st = await this.requireFile(target)
    if (st.size > maxBytes) throw new FsError(`file too large: ${target.displayPath}`, 'FS_TOO_LARGE')
    const buf = await this.storage.read(this.absFromTarget(target))
    if (buf.length > maxBytes) throw new FsError(`file too large: ${target.displayPath}`, 'FS_TOO_LARGE')
    this.log('readBytes', target.displayPath, `${buf.length} bytes`)
    return new Uint8Array(buf)
  }

  async readByteRange(target, range, signal) {
    aborted(signal, 'read')
    await this.requireFile(target)
    const buf = await this.storage.read(this.absFromTarget(target))
    this.log('readByteRange', target.displayPath, `${range.offset}+${range.length}`)
    return new Uint8Array(buf.subarray(range.offset, range.offset + range.length))
  }

  async listDir(target, signal) {
    aborted(signal, 'listDir')
    const abs = this.absFromTarget(target)
    const st = await this.storage.stat(abs)
    if (!st) throw new FsError(`not found: ${target.displayPath}`, 'FS_NOT_FOUND')
    if (st.type !== 'directory') throw new FsError(`not a directory: ${target.displayPath}`, 'FS_NOT_DIRECTORY')
    const entries = (await this.storage.list(abs)).sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
    this.log('listDir', target.displayPath, `${entries.length} entries`)
    return entries.map((e) => ({
      name: e.name,
      type: e.type,
      target: this.targetOf(e.abs),
      ...(e.mtimeMs !== undefined ? { version: version(e) } : {}),
      ...(e.type === 'file' && e.size !== undefined ? { size: e.size } : {}),
    }))
  }

  async withLock(key, op) {
    const prior = this.locks.get(key) ?? Promise.resolve()
    const run = prior.then(op, op)
    const tail = run.then(() => undefined, () => undefined)
    this.locks.set(key, tail)
    try { return await run } finally { if (this.locks.get(key) === tail) this.locks.delete(key) }
  }

  async writeText(target, content, expected, signal) {
    return this.withLock(target.targetKey, async () => {
      aborted(signal, 'write')
      const abs = this.absFromTarget(target)
      const existing = await this.storage.stat(abs)
      if (existing && existing.type !== 'file') throw new FsError(`cannot write "${target.displayPath}": not a regular file`, 'FS_NOT_REGULAR_FILE')
      if (expected?.kind === 'replaceIfVersion') {
        if (!existing) throw new FsError(`cannot write "${target.displayPath}": file no longer exists`, 'FS_STALE_VERSION')
        if (version(existing) !== expected.version) throw new FsError(`cannot write "${target.displayPath}": file changed since it was read`, 'FS_STALE_VERSION')
      } else if (expected?.kind === 'createIfAbsent' && existing) {
        throw new FsError(`cannot overwrite existing "${target.displayPath}" without reading it first`, 'FS_NOT_OBSERVED')
      }
      let before = null
      if (existing) {
        try { before = lf(decodeText(await this.storage.read(abs), target.displayPath)) } catch { before = null }
      }
      aborted(signal, 'write')
      await this.storage.write(abs, Buffer.from(content, 'utf8'))
      const after = await this.storage.stat(abs)
      this.log('writeText', target.displayPath, `${Buffer.byteLength(content)} bytes`, existing ? 'update' : 'create')
      return {
        operation: existing ? 'update' : 'create',
        version: after ? version(after) : `missing:${abs}`,
        before,
        after: lf(content),
      }
    })
  }

  async editText(target, edit, expected, signal) {
    return this.withLock(target.targetKey, async () => {
      aborted(signal, 'edit')
      const abs = this.absFromTarget(target)
      const existing = await this.storage.stat(abs)
      if (!existing) throw new FsError(`cannot edit "${target.displayPath}": file changed since it was read`, 'FS_STALE_VERSION')
      if (existing.type !== 'file') throw new FsError(`cannot edit "${target.displayPath}": not a regular file`, 'FS_NOT_REGULAR_FILE')
      if (expected && version(existing) !== expected.version) {
        throw new FsError(`cannot edit "${target.displayPath}": file changed since it was read`, 'FS_STALE_VERSION')
      }
      const raw = decodeText(await this.storage.read(abs), target.displayPath)
      const crlf = raw.includes('\r\n')
      const original = lf(raw)
      const oldS = lf(edit.oldString)
      const newS = lf(edit.newString)
      if (!oldS) throw new FsError('oldString must be non-empty', 'FS_EDIT_NOT_FOUND')
      const count = original.split(oldS).length - 1
      if (count === 0) throw new FsError(`oldString not found in ${target.displayPath}`, 'FS_EDIT_NOT_FOUND')
      if (count > 1 && !edit.replaceAll) throw new FsError(`oldString matches ${count} times in ${target.displayPath}`, 'FS_AMBIGUOUS_EDIT')
      const edited = edit.replaceAll ? original.split(oldS).join(newS) : original.replace(oldS, () => newS)
      aborted(signal, 'edit')
      await this.storage.write(abs, Buffer.from(crlf ? edited.replace(/\n/g, '\r\n') : edited, 'utf8'))
      const after = await this.storage.stat(abs)
      this.log('editText', target.displayPath, `${count} match${count > 1 ? 'es' : ''}`)
      return { version: after ? version(after) : `missing:${abs}`, before: original, after: edited }
    })
  }
}

export function apply(ctx, config = {}) {
  const backend = config.backend ?? 'local-disk'
  const storage = backend === 'remote-api'
    ? new RemoteApiStorage(config.endpoint)
    : new LocalDiskStorage(config.root ?? process.cwd())
  const logFile = config.logFile
  const log = (...parts) => {
    const line = `${new Date().toISOString()} [fs-virtual] ${parts.join(' ')}`
    if (logFile) {
      try { appendFileSync(logFile, line + '\n') } catch { /* 日志失败不影响读写 */ }
    }
  }
  log('mounted', storage.kind, storage.root ?? storage.endpoint ?? '')
  ctx.provide('fs', new VirtualFileSystem(storage, log))
}
