/**
 * Boots the computer-use driver that ships with universe bots. The driver is
 * owned by the desktop host: it grants control to the harness server over the
 * utility process's private message port, exactly as the standalone app does.
 */
import path from "node:path";
import { pathToFileURL } from "node:url";
import type { ChildProcess } from "node:child_process";

type Connection = { mode?: string; reason?: string; [key: string]: unknown };

type CuaModule = {
  startCua: (options?: { attempt?: number }) => Promise<Connection>;
  stopCua: () => Promise<void>;
  grantLocalComputer: (proc: unknown, isCurrent: boolean) => void;
  registerCuaIpc: () => void;
  setCuaStateListener: (listener: (connection: Connection) => void) => void;
  cuaPermissionsStatus: () => Promise<unknown>;
};

let cua: CuaModule | null = null;
let originSetter: ((origin: string) => void) | null = null;

export function cuaConnection(): Connection | null {
  return connection;
}
let connection: Connection | null = null;

export async function startBotsCua(
  repoRoot: string,
  origin: string,
  onConnection?: (connection: Connection) => void,
): Promise<CuaModule | null> {
  try {
    if (!process.env.CUA_DRIVER_PATH) {
      const staged = path.join(
        repoRoot, "universe-bots", "dist-native", "cua-win32-x64",
        process.platform === "win32" ? "cua-driver.exe" : "cua-driver",
      );
      if (require("node:fs").existsSync(staged)) process.env.CUA_DRIVER_PATH = staged;
    }
    const localOrigin = require(
      path.join(repoRoot, "universe-bots", "electron", "local-origin.cjs"),
    ) as { setLocalOrigin: (origin: string) => void };
    localOrigin.setLocalOrigin(origin);
    originSetter = localOrigin.setLocalOrigin;

    const moduleUrl = pathToFileURL(
      path.join(repoRoot, "universe-bots", "electron", "cua.mjs"),
    ).href;
    const loaded = (await import(moduleUrl)) as unknown as CuaModule;
    cua = loaded;
    loaded.setCuaStateListener(next => {
      connection = next;
      onConnection?.(next);
    });
    // The six cua:* channels are registered on the global ipcMain here; they
    // do not collide with Universe's universe:* surface.
    loaded.registerCuaIpc();
    connection = await loaded.startCua();
    onConnection?.(connection);
    console.log(`[cua] ${connection?.mode ?? "unknown"}${connection?.reason ? ": " + connection.reason : ""}`);
    return loaded;
  } catch (error) {
    console.log(`[cua] start failed: ${String(error)}`);
    cua = null;
    return null;
  }
}

export function grantBotsComputer(proc: ChildProcess | null): void {
  if (!cua || !proc) return;
  try {
    cua.grantLocalComputer(proc, true);
  } catch (error) {
    console.log(`[cua] grant failed: ${String(error)}`);
  }
}

export async function cuaPermissions(): Promise<unknown> {
  if (!cua) return { available: false };
  try {
    return await cua.cuaPermissionsStatus();
  } catch (error) {
    console.log(`[cua] permissions query failed: ${String(error)}`);
    return { available: false };
  }
}

export function setBotsCuaOrigin(origin: string): void {
  try {
    originSetter?.(origin);
  } catch {
    /* no driver loaded */
  }
}

export async function stopBotsCua(): Promise<void> {
  const loaded = cua;
  cua = null;
  if (!loaded) return;
  try {
    await loaded.stopCua();
  } catch (error) {
    console.log(`[cua] stop failed: ${String(error)}`);
  }
}
