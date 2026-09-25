// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0
//
// Same tiny WebSocket wrapper used by the other WebUI examples, with one fix:
// it reuses the page's own protocol (https/http) instead of hardcoding http://,
// so it keeps working when the WebUI is served with use_tls=True.

class WebUI {
  #socket;

  constructor(options = {}) {
    this.#socket = io(`${window.location.protocol}//${window.location.host}`, options);
  }

  on_connect(callback) {
    this.#socket.on('connect', callback);
  }

  on_disconnect(callback) {
    this.#socket.on('disconnect', callback);
  }

  on_message(eventName, callback) {
    this.#socket.on(eventName, callback);
  }

  send_message(eventName, data) {
    this.#socket.emit(eventName, data ?? {});
  }
}
