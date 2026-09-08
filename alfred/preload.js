"use strict";
/* Bridge between the Electron main process and the existing renderer UI.
   The renderer sees `window.alfredNative`; when it's absent the page runs as a
   plain static tool (no Gmail). */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("alfredNative", {
  platform: "electron",
  gmail: {
    status:      () => ipcRenderer.invoke("gmail:status"),
    setCreds:    (c) => ipcRenderer.invoke("gmail:setCreds", c),
    connect:     () => ipcRenderer.invoke("gmail:connect"),
    imapConnect: (c) => ipcRenderer.invoke("gmail:imapConnect", c),
    disconnect:  () => ipcRenderer.invoke("gmail:disconnect"),
    list:       (a) => ipcRenderer.invoke("gmail:list", a),
    get:        (id) => ipcRenderer.invoke("gmail:get", id),
    send:       (a) => ipcRenderer.invoke("gmail:send", a),
    draft:      (a) => ipcRenderer.invoke("gmail:draft", a),
    modify:     (a) => ipcRenderer.invoke("gmail:modify", a),
    trash:      (a) => ipcRenderer.invoke("gmail:trash", a),
  },
  updates: {
    onStatus:     (cb) => ipcRenderer.on("update:status", (_e, s) => cb(s)),
    check:        () => ipcRenderer.invoke("update:check"),
    download:     () => ipcRenderer.invoke("update:download"),
    install:      () => ipcRenderer.invoke("update:install"),
    version:      () => ipcRenderer.invoke("update:version"),
    openReleases: () => ipcRenderer.invoke("update:openReleases"),
  },
  brain: {
    status:     () => ipcRenderer.invoke("brain:status"),
    setup:      () => ipcRenderer.invoke("brain:setup"),
    chat:       (a) => ipcRenderer.invoke("brain:chat", a),
    onProgress: (cb) => ipcRenderer.on("brain:progress", (_e, p) => cb(p)),
  },
  license: {
    status:       () => ipcRenderer.invoke("license:status"),
    activate:     (a) => ipcRenderer.invoke("license:activate", a),
    setAdmin:     (a) => ipcRenderer.invoke("license:setAdmin", a),
    checkMaster:  (a) => ipcRenderer.invoke("license:checkMaster", a),
    adminCall:    (a) => ipcRenderer.invoke("license:adminCall", a),
    setServer:    (a) => ipcRenderer.invoke("license:setServer", a),
    deactivate:   () => ipcRenderer.invoke("license:deactivate"),
    openCodesFile:() => ipcRenderer.invoke("license:openCodesFile"),
  },
  app: {
    openDataFolder: () => ipcRenderer.invoke("app:openDataFolder"),
  },
  config: {
    load: () => ipcRenderer.invoke("config:load"),
    save: (d) => ipcRenderer.invoke("config:save", d),
  },
});
