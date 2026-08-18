// Controllable WebSocket for frontend tests. jsdom does not implement a real
// WebSocket, and the hook must exercise the connection state machine, so tests
// stub the global WebSocket with this class.
//
// Behavior:
//   * autoOpen = true  -> every connection opens itself on the next microtask
//     (used by app-level render tests: the dashboard goes LIVE after sync),
//   * autoOpen = false (default) -> every connection simulates a failed
//     handshake (onerror + onclose), matching a backend that is unreachable;
//     the test drives success explicitly with serverOpen()/serverClose().

export class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static autoOpen = false;
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;

  url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code?: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    if (MockWebSocket.autoOpen) {
      queueMicrotask(() => {
        if (this.readyState === MockWebSocket.CONNECTING) this.serverOpen();
      });
    } else {
      queueMicrotask(() => {
        if (this.readyState === MockWebSocket.CONNECTING) {
          this.readyState = MockWebSocket.CLOSED;
          this.onerror?.();
          this.onclose?.({ code: 1006 });
        }
      });
    }
  }

  close() {
    if (this.readyState === MockWebSocket.CLOSED) return;
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code: 1000 });
  }

  serverOpen() {
    if (this.readyState === MockWebSocket.CLOSED) return;
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  serverSend(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  serverClose() {
    if (this.readyState === MockWebSocket.CLOSED) return;
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code: 1006 });
  }

  static reset() {
    MockWebSocket.instances = [];
    MockWebSocket.autoOpen = false;
  }
}
