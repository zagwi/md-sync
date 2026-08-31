"""Unix socket RPC server — 桌面版与打包后端的进程内通信层。

替代 web 版的 HTTP/FastAPI 传输：桌面 app 把后端作为子进程拉起，通过本机
Unix socket（Linux/macOS）交换 JSON，**不监听任何网络端口**。

协议（短连接，每消息一行 JSON）：
    请求: {"method": "...", "params": {...}}
    响应: {"ok": true, "data": ...} | {"ok": false, "error": "..."}
"""

from __future__ import annotations

import base64
import json
import logging
import socket
import sys
import threading
from pathlib import Path

from md_sync.web.app import _UPLOAD_DIR, WebSession

logger = logging.getLogger(__name__)

SOCK_NAME = "md-sync.sock"


def socket_path() -> Path:
    """与 Rust 侧 api::ipc_socket_path 保持同一算法。"""
    base = (
        Path.home() / "Library" / "Caches" / "md-sync"
        if sys.platform == "darwin"
        else Path.home() / ".cache" / "md-sync"
    )
    return base / SOCK_NAME


def _reply(conn: socket.socket, ok: bool, payload) -> None:
    body = json.dumps(
        {"ok": ok, **({"data": payload} if ok else {"error": payload})},
        ensure_ascii=False,
    )
    try:
        conn.sendall((body + "\n").encode("utf-8"))
    except OSError:
        pass


class IpcServer:
    def __init__(self, session: WebSession | None = None) -> None:
        self.session = session or WebSession()

    def handle(self, method: str, params: dict):
        s = self.session
        if method == "meta":
            return s.meta_payload()
        if method == "styles":
            return {"styles": s.styles(params.get("schema"))}
        if method == "state":
            return s.state_payload()
        if method == "config":
            return s.apply_config(params)
        if method == "sync":
            ok, problems = s.run_sync()
            if not ok:
                return {"ok": False, "errors": problems}
            return {"ok": True, "started": True}
        if method == "watch":
            if params.get("enabled"):
                return {"ok": s.watch(), "watching": s.watching}
            s.stop_watch()
            return {"ok": True, "watching": s.watching}
        if method == "clear":
            return {"ok": True, "removed": s.clear_outputs()}
        if method == "open-dir":
            shown = s.open_dir()
            return {"ok": shown is None, "path": str(s.output_root())}
        if method == "logs":
            after = int(params.get("after", 0))
            lines = s.log.tail(after)
            return {"lines": lines, "max_id": s.log._last_id}
        if method == "refresh":
            s.build_config()
            return {"ok": True, "output_files": s.output_files()}
        if method == "normalize":
            return s.normalize_source()
        if method == "upload":
            return self._upload(params)
        raise KeyError(f"未知方法: {method}")

    def _upload(self, params: dict) -> dict:
        """桌面版实际不走上传（rfd 直接选本地路径）；保留以对齐 web 语义。"""
        name = (params.get("filename") or "upload.md").strip()
        content_b64 = params.get("content") or ""
        data = base64.b64decode(content_b64) if content_b64 else b""
        if not data:
            return {"ok": False, "errors": ["上传内容为空"]}
        safe = Path(name).name or "upload.md"
        if not safe.lower().endswith((".md", ".markdown", ".txt", ".text")):
            safe = Path(safe).stem + ".md"
        _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = _UPLOAD_DIR / safe
        target.write_bytes(data)
        s = self.session
        s.state.source = str(target)
        s.build_config()
        s.log.append(f"⬆ 已上传源文件: {target.name}（{len(data)} 字节）")
        return s.state_payload()

    def _serve_conn(self, conn: socket.socket) -> None:
        with conn:
            try:
                data = conn.recv(65536)
            except OSError:
                return
            if not data:
                return
            try:
                req = json.loads(data.decode("utf-8"))
                method = req.get("method", "")
                params = req.get("params") or {}
                _reply(conn, True, self.handle(method, params))
            except KeyError as e:
                _reply(conn, False, str(e))
            except Exception as e:  # noqa: BLE001
                logger.exception("ipc handler failed")
                _reply(conn, False, f"后端错误: {e}")

    def serve_forever(self) -> None:
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # 清理可能残留的陈旧 socket 文件
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        sock.listen(8)
        print(f"[ipc] md-sync backend listening on {path}")
        try:
            while True:
                conn, _ = sock.accept()
                threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()
        except KeyboardInterrupt:
            pass
        finally:
            sock.close()
            try:
                path.unlink()
            except OSError:
                pass


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    IpcServer().serve_forever()


if __name__ == "__main__":
    main()
