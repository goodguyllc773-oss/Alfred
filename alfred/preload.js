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
});
