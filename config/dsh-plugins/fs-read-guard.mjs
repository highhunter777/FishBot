/**
 * 只读实例（DSH_PERMISSION_MODE=read-only）的读取围栏。
 *
 * DSH 自带的沙盒只约束“写”，读是放行的；这个插件补上读侧：
 * - read / read_image：只允许 cwd（即 FishBot 工作目录）内的路径
 * - glob / grep：只读实例直接禁用，避免用模式扫全盘
 * - web_fetch：拒绝 file://
 *
 * 通过 dsh-acp.patch.yml 的 insert 挂载，worker（danger-full-access）不受影响。
 */
import { realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";

export const name = "fs-read-guard";

const norm = (p) => (process.platform === "win32" ? p.toLowerCase() : p);

function canon(p) {
  try {
    return norm(realpathSync.native(p));
  } catch {
    return norm(resolve(p));
  }
}

function inside(root, target) {
  const rel = relative(root, target);
  return rel === "" || (rel !== ".." && !rel.startsWith(".." + sep) && !isAbsolute(rel));
}

function deny(message) {
  return {
    content: [{ type: "text", text: `Error: ${message}` }],
    isError: true,
    error: {
      message,
      info: { name: "FsReadGuardError", code: "FS_READ_OUTSIDE" },
    },
  };
}

export function apply(ctx) {
  if (process.env.DSH_PERMISSION_MODE !== "read-only") return;

  const root = canon(process.cwd());
  const pathTools = new Set(["read", "read_image"]);

  ctx.on("tools/execute", async (exec, next) => {
    const tool = exec.name;
    const args = exec.arguments || {};

    if (tool === "glob" || tool === "grep") {
      return deny("只读实例不提供目录搜索，只能在聊天里回答。");
    }

    if (tool === "web_fetch" && typeof args.url === "string" && /^file:/i.test(args.url)) {
      return deny("只读实例不能读取本地文件 URL。");
    }

    if (!pathTools.has(tool) || typeof (args.file_path ?? args.path) !== "string") {
      return next();
    }

    const raw = String(args.file_path ?? args.path).trim();
    if (!raw) return next();

    const target = canon(resolve(root, raw));
    if (!inside(root, target)) {
      return deny("只读实例只能读取工作目录内的文件。");
    }
    return next();
  });
}
