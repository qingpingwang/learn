"""虚拟文件系统。agent 看见的是 /skills、/workspace、/memories，文件在本地目录里。

/skills、/memories 各自挂一个本地目录，所有会话共用。
/workspace 和压缩掉的原文在当前会话目录里，换一条会话就换一份。
"""

import os
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from langchain_core.runnables.config import var_child_runnable_config

from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

def _normalize(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("路径不合法")
    if not path.startswith("/"):
        path = "/" + path
    pure = PurePosixPath(path)
    if any(part in ("..", "~") for part in pure.parts):
        raise ValueError("路径不合法")
    text = pure.as_posix()
    return "/" if text == "/" else text.rstrip("/")


def _readonly(path: str, prefixes: tuple[str, ...]) -> bool:
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _under(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    target.relative_to(base)
    return target


class VirtualFS(BackendProtocol):
    """deepagents 的文件工具不碰磁盘，只调这个类上的方法。

    模型看见的工具和实际调用：

    - ls(path) → ls
    - read(file_path, offset=0, limit=2000) → read_file
    - write(file_path, content) → write_file。超长消息被挪走时也会调 write
    - edit(file_path, old_string, new_string, replace_all=False) → edit_file
    - delete(file_path) → delete
    - glob(pattern, path=None) → glob
    - grep(pattern, path=None, glob=None, max_count=None) → grep

    skills 和长期记忆不走工具，中间件直接调 download_files 把文件读出来。
    异步跑的时候调同名加 a 的版本。协议里的默认实现是把同步方法丢进线程。

    execute 只有带 shell 的后端才有，这个类不实现。
    local() 不在协议里，deepagents 不会调。服务端用它拿本地目录。
    """

    def __init__(self, root: str | Path, *, skills: str | Path | None = None, memories: str | Path | None = None):
        self.sessions = Path(root).resolve()
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.skills = Path(skills).resolve() if skills is not None else self.sessions / "skills"
        self.memories = Path(memories).resolve() if memories is not None else self.sessions / "memories"
        self.skills.mkdir(parents=True, exist_ok=True)
        self.memories.mkdir(parents=True, exist_ok=True)
        self.readonly = ("/skills",)
        self._dirs: dict[str, Path] = {}

    def bind(self, session_id: str) -> None:
        """记下这个会话的本地目录。/skills 和 /memories 不受影响。

        目录存在实例上，用 thread_id 取。流式输出每次 yield 都会换一份上下文，
        写在 contextvar 里的话，后面的工具调用就找不到了。
        """
        if not session_id or "/" in session_id or session_id in (".", ".."):
            raise ValueError("会话编号不合法")
        directory = _under(self.sessions, session_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "workspace").mkdir(exist_ok=True)
        self._dirs[session_id] = directory

    def _session_dir(self) -> Path:
        config = var_child_runnable_config.get()
        thread_id = None
        if isinstance(config, dict):
            thread_id = (config.get("configurable") or {}).get("thread_id")
        directory = self._dirs.get(thread_id) if isinstance(thread_id, str) else None
        if directory is not None:
            return directory
        if len(self._dirs) == 1:
            return next(iter(self._dirs.values()))
        raise ValueError("没有绑定会话")

    def local(self, path: str = "/") -> Path:
        """虚拟路径对应的本地路径。deepagents 不调用，服务端用它下发 skills。"""
        virtual = _normalize(path)
        if virtual == "/skills" or virtual.startswith("/skills/"):
            return _under(self.skills, virtual.removeprefix("/skills").lstrip("/"))
        if virtual == "/memories" or virtual.startswith("/memories/"):
            return _under(self.memories, virtual.removeprefix("/memories").lstrip("/"))
        return _under(self._session_dir(), virtual.lstrip("/"))

    def _virtual(self, path: Path) -> str:
        resolved = path.resolve()
        for prefix, base in (("/skills", self.skills), ("/memories", self.memories)):
            try:
                relative = resolved.relative_to(base)
            except ValueError:
                continue
            text = relative.as_posix()
            return prefix if text == "." else f"{prefix}/{text}"
        relative = resolved.relative_to(self._session_dir())
        text = relative.as_posix()
        return "/" if text == "." else "/" + text

    def _info(self, path: Path, *, is_dir: bool) -> dict:
        shown = self._virtual(path)
        if is_dir and shown != "/":
            shown += "/"
        try:
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).isoformat()
            size = 0 if is_dir else int(stat.st_size)
        except OSError:
            return {"path": shown, "is_dir": is_dir}
        return {"path": shown, "is_dir": is_dir, "size": size, "modified_at": modified}

    def _contained(self, path: Path, bound: Path) -> bool:
        try:
            path.resolve().relative_to(bound.resolve())
        except ValueError:
            return False
        return True

    # 工具 ls。skills 加载时也会用它列出 /skills 下的目录
    def ls(self, path: str) -> LsResult:
        try:
            virtual = _normalize(path)
            directory = self.local(virtual)
        except ValueError as exc:
            return LsResult(error=str(exc))
        if not directory.exists():
            return LsResult(error="没有这个路径")
        if not directory.is_dir():
            return LsResult(error="不是目录")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if not self._contained(child, directory):
                continue
            if child.is_dir():
                entries.append(self._info(child, is_dir=True))
            elif child.is_file():
                entries.append(self._info(child, is_dir=False))
        if virtual == "/":
            seen = {item["path"].rstrip("/") for item in entries}
            for base in (self.skills, self.memories):
                info = self._info(base, is_dir=True)
                if info["path"].rstrip("/") in seen:
                    continue
                entries.append(info)
            entries.sort(key=lambda item: item["path"])
        return LsResult(entries=entries)

    # 工具 read_file
    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if limit <= 0:
            return ReadResult(no_lines_requested=True)
        try:
            path = self.local(file_path)
        except ValueError as exc:
            return ReadResult(error=str(exc))
        if not path.exists():
            return ReadResult(error="没有这个文件")
        if path.is_dir():
            return ReadResult(error="是目录")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return ReadResult(error="读不了这个文件")
        stat = path.stat()
        file_data = {
            "content": "",
            "encoding": "utf-8",
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
        if not lines:
            return ReadResult(file_data=file_data)
        offset = max(offset, 0)
        if offset >= len(lines):
            return ReadResult(error="偏移超出文件")
        window = lines[offset:offset + limit]
        file_data["content"] = "\n".join(window)
        end_line = offset + len(window)
        return ReadResult(
            file_data=file_data,
            total_lines=len(lines),
            start_line=offset + 1,
            end_line=end_line,
            next_offset=end_line,
        )

    # 工具 write_file。上下文把超长消息挪走时也走这里
    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            virtual = _normalize(file_path)
        except ValueError as exc:
            return WriteResult(error=str(exc))
        if virtual == "/":
            return WriteResult(error="不能写根目录")
        if _readonly(virtual, self.readonly):
            return WriteResult(error="只读")
        try:
            path = self.local(virtual)
        except ValueError as exc:
            return WriteResult(error=str(exc))
        if path.is_dir():
            return WriteResult(error="是目录")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return WriteResult(path=virtual)

    # 工具 edit_file
    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        try:
            virtual = _normalize(file_path)
        except ValueError as exc:
            return EditResult(error=str(exc))
        if _readonly(virtual, self.readonly):
            return EditResult(error="只读")
        if not old_string:
            return EditResult(error="要替换的原文是空的")
        if old_string == new_string:
            return EditResult(error="原文和替换内容一样")
        try:
            path = self.local(virtual)
        except ValueError as exc:
            return EditResult(error=str(exc))
        if not path.is_file():
            return EditResult(error="没有这个文件")
        text = path.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            return EditResult(error="找不到这段原文")
        if not replace_all and count != 1:
            return EditResult(error="原文出现了多次")
        updated = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        path.write_text(updated, encoding="utf-8")
        return EditResult(path=virtual, occurrences=count if replace_all else 1)

    # 工具 delete
    def delete(self, file_path: str) -> DeleteResult:
        try:
            virtual = _normalize(file_path)
        except ValueError as exc:
            return DeleteResult(error=str(exc))
        if virtual == "/":
            return DeleteResult(error="不能删根目录")
        if _readonly(virtual, self.readonly):
            return DeleteResult(error="只读")
        try:
            path = self.local(virtual)
        except ValueError as exc:
            return DeleteResult(error=str(exc))
        if not path.exists():
            return DeleteResult(error="没有这个路径")
        _remove(path)
        return DeleteResult(path=virtual)

    def _files(self, path: str | None) -> list[tuple[str, Path]]:
        virtual = _normalize(path or "/")
        roots = [self._session_dir(), self.skills, self.memories] if virtual == "/" else [self.local(virtual)]
        found = []
        for root in roots:
            if not root.exists():
                if virtual != "/":
                    raise FileNotFoundError(virtual)
                continue
            if root.is_file():
                found.append((self._virtual(root), root))
                continue
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                current = Path(dirpath)
                dirnames[:] = [name for name in dirnames if self._contained(current / name, root)]
                for name in filenames:
                    file = current / name
                    if self._contained(file, root) and file.is_file():
                        found.append((self._virtual(file), file))
        return found

    # 工具 grep。pattern 是字面量，不是正则
    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, *, max_count: int | None = None) -> GrepResult:
        if not pattern:
            return GrepResult(error="搜索内容是空的")
        try:
            files = self._files(path)
        except ValueError as exc:
            return GrepResult(error=str(exc))
        except FileNotFoundError:
            return GrepResult(error="没有这个路径")
        matches = []
        truncated = False
        for virtual, file in files:
            relative = virtual.lstrip("/")
            if glob and not _match(glob, relative):
                continue
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            for number, line in enumerate(lines, start=1):
                if pattern not in line:
                    continue
                matches.append({"path": virtual, "line": number, "text": line})
                if max_count is not None and len(matches) >= max_count:
                    truncated = True
                    break
            if truncated:
                break
        return GrepResult(matches=matches, truncated=truncated)

    # 工具 glob
    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        if not pattern or ".." in PurePosixPath(pattern).parts:
            return GlobResult(error="路径不合法")
        try:
            virtual = _normalize(path or "/")
            files = self._files(path)
            bases = [self._session_dir(), self.skills, self.memories] if virtual == "/" else [self.local(virtual)]
        except ValueError as exc:
            return GlobResult(error=str(exc))
        except FileNotFoundError:
            return GlobResult(error="没有这个路径")
        matches = []
        for virtual_path, file in files:
            relative = _relative_to_bases(file, bases)
            if relative is not None and _match(pattern, relative):
                matches.append(self._info(file, is_dir=False))
        return GlobResult(matches=matches)

    # skills 和 memory 中间件用来读文件，模型没有这个工具
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results = []
        for path in paths:
            try:
                local = self.local(path)
            except ValueError:
                results.append(FileDownloadResponse(path=path, content=None, error=INVALID_PATH))
                continue
            if not local.exists():
                results.append(FileDownloadResponse(path=path, content=None, error=FILE_NOT_FOUND))
            elif local.is_dir():
                results.append(FileDownloadResponse(path=path, content=None, error=IS_DIRECTORY))
            else:
                try:
                    results.append(FileDownloadResponse(path=path, content=local.read_bytes(), error=None))
                except OSError:
                    results.append(FileDownloadResponse(path=path, content=None, error="permission_denied"))
        return results

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        results = []
        for path, content in files:
            try:
                virtual = _normalize(path)
            except ValueError:
                results.append(FileUploadResponse(path=path, error=INVALID_PATH))
                continue
            if _readonly(virtual, self.readonly):
                results.append(FileUploadResponse(path=path, error="permission_denied"))
                continue
            try:
                local = self.local(virtual)
            except ValueError:
                results.append(FileUploadResponse(path=path, error=INVALID_PATH))
                continue
            if local.is_dir():
                results.append(FileUploadResponse(path=path, error=IS_DIRECTORY))
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(content)
            results.append(FileUploadResponse(path=path, error=None))
        return results


def _relative_to_bases(path: Path, bases: list[Path]) -> str | None:
    resolved = path.resolve()
    for base in bases:
        try:
            return resolved.relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return None


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    for child in path.iterdir():
        _remove(child)
    path.rmdir()


def _match(pattern: str, relative: str) -> bool:
    """无斜杠的模式匹配任意深度的文件名，有斜杠的模式相对搜索根。"""
    name = relative.rsplit("/", 1)[-1]
    body = pattern[1:] if pattern.startswith("/") else pattern
    if "/" not in body:
        if name.startswith(".") and not body.startswith("."):
            return False
        return fnmatchcase(name, body)
    return PurePosixPath(relative).full_match(body.lstrip("/"))
