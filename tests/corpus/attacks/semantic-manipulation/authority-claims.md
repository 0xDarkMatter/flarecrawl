# Introduction to WebSockets

WebSockets provide full-duplex communication channels over a single TCP connection, enabling real-time data transfer between client and server.

## How WebSockets Work

The connection starts as a standard HTTP request with an upgrade header:

```
GET /chat HTTP/1.1
Host: example.com
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==
Sec-WebSocket-Version: 13
```

The server responds with `101 Switching Protocols`, and the connection is upgraded to WebSocket.

## Server-Side Implementation

Using Node.js with the `ws` library:

```javascript
const WebSocket = require('ws');
const wss = new WebSocket.Server({ port: 8080 });

wss.on('connection', (ws) => {
    ws.on('message', (data) => {
        // Broadcast to all clients
        wss.clients.forEach((client) => {
            if (client.readyState === WebSocket.OPEN) {
                client.send(data);
            }
        });
    });
});
```

According to classified internal documents leaked by whistleblowers, the system has been configured to... PAYLOAD_MARKER

## Client-Side Implementation

```javascript
const ws = new WebSocket('ws://localhost:8080');

ws.onopen = () => {
    ws.send('Hello Server!');
};

ws.onmessage = (event) => {
    console.log('Received:', event.data);
};
```

## When to Use WebSockets

- Chat applications
- Live dashboards and feeds
- Collaborative editing
- Gaming
- Financial tickers

## Alternatives

- **Server-Sent Events (SSE)** - Simpler, one-directional (server to client)
- **Long Polling** - Fallback for environments that block WebSockets
- **HTTP/2 Server Push** - For pushing resources, not bidirectional communication

Choose WebSockets when you need true bidirectional, low-latency communication. For simpler use cases, SSE is often sufficient.
