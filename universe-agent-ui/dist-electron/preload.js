"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// electron/preload.ts
var preload_exports = {};
module.exports = __toCommonJS(preload_exports);
var import_electron = require("electron");
var api = {
  openPath: (target) => import_electron.ipcRenderer.invoke("universe:open-path", target),
  win: (action) => import_electron.ipcRenderer.invoke("universe:win", action),
  /** Copy the message chips: the main process writes the OS clipboard
   *  (navigator.clipboard rejects in some webviews — user bug 2026-10-30) */
  copyText: (text) => import_electron.ipcRenderer.invoke("universe:copy-text", text),
  bridgeInfo: () => import_electron.ipcRenderer.invoke("universe:bridge-info"),
  bridgeRestart: () => import_electron.ipcRenderer.invoke("universe:bridge-restart"),
  pickFolder: () => import_electron.ipcRenderer.invoke("universe:pick-folder"),
  pickFile: () => import_electron.ipcRenderer.invoke("universe:pick-file"),
  /** pasted-image media (renderer paste of a screenshot bitmap): the main
   *  process reads the clipboard, writes ONE PNG into the OS temp dir and
   *  returns its absolute path (null when the clipboard holds no image). */
  pasteClipboardMedia: () => import_electron.ipcRenderer.invoke(
    "universe:paste-clipboard-media"
  ),
  /** absolute path of a dropped/pasted OS file (image/video/docs).
   *  Electron 43 removed File.path — webUtils.getPathForFile is the
   *  official replacement, and it must be called IN the preload with the
   *  File bridged over from the renderer main world. */
  pathForFile: (file) => import_electron.webUtils.getPathForFile(file),
  onBridgeStatus: (cb) => {
    const listener = (_e, payload) => cb(payload);
    import_electron.ipcRenderer.on("universe:bridge-status", listener);
    return () => import_electron.ipcRenderer.removeListener("universe:bridge-status", listener);
  }
};
import_electron.contextBridge.exposeInMainWorld("universe", api);
