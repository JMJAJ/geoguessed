import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Set

import aiohttp
from aiohttp import web
import websockets
from websockets.server import serve

DEVTOOLS_HOST = "127.0.0.1"
DEVTOOLS_PORT = 9222
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8765  
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8080    

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

clients: Set[websockets.WebSocketServerProtocol] = set()

async def get_browser_ws_url() -> Optional[str]:
    """Queries the DevTools JSON endpoint to find the browser's debugger WebSocket URL."""
    url = f"http://{DEVTOOLS_HOST}:{DEVTOOLS_PORT}/json/version"
    logging.info(f"Attempting to find browser WebSocket at {url}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    ws_url = data.get("webSocketDebuggerUrl")
                    if ws_url:
                        logging.info(f"Successfully found browser WebSocket: {ws_url}")
                        return ws_url
                    else:
                        logging.error("WebSocket URL not found in browser response.")
                        return None
    except aiohttp.ClientConnectorError:
        logging.error("-" * 60)
        logging.error(f"Could not connect to http://{DEVTOOLS_HOST}:{DEVTOOLS_PORT}.")
        logging.error("Please ensure your browser is running with the remote debugging flag:")
        logging.error("chrome.exe --remote-debugging-port=9222")
        logging.error("-" * 60)
        return None
    return None

async def broadcast(obj: Dict[str, Any]):
    """Sends a JSON object to all connected map clients."""
    if not clients:
        return
    data = json.dumps(obj)

    await asyncio.gather(*[c.send(data) for c in clients if not c.closed], return_exceptions=True)

async def client_handler(websocket: websockets.WebSocketServerProtocol):
    """Handles incoming connections from the map page."""
    logging.info("Map client connected.")
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        logging.info("Map client disconnected.")
        clients.discard(websocket)

def extract_location(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        duel = payload.get("duel")
        if not isinstance(duel, dict): return None
        state = duel.get("state") or {}
        rounds = state.get("rounds") or []
        current = state.get("currentRoundNumber")
        idx = (current - 1) if isinstance(current, int) and current >= 1 else None
        candidate = None
        if isinstance(idx, int) and 0 <= idx < len(rounds):
            candidate = rounds[idx]
        elif rounds:
            candidate = next((r for r in rounds if isinstance(r, dict) and r.get("panorama")), rounds[0])

        if candidate and isinstance(candidate, dict):
            pano = candidate.get("panorama") or {}
            lat, lng = pano.get("lat"), pano.get("lng")
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                return {
                    "lat": float(lat), "lng": float(lng),
                    "round": candidate.get("roundNumber"),
                    "countryCode": pano.get("countryCode")
                }
    except Exception: pass
    try:
        if isinstance(payload.get("pin"), dict):
            p = payload["pin"]
            lat, lng = p.get("lat"), p.get("lng")
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
                return {"lat": float(lat), "lng": float(lng), "round": None, "countryCode": None}
    except Exception: pass
    return None

async def devtools_sniffer(browser_ws: str):
    """Connects to the browser, finds the GeoGuessr tab, and sniffs WebSocket traffic."""
    logging.info("Starting DevTools sniffer...")
    async with websockets.connect(browser_ws) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
        attached_session = None

        while True:
            try:
                raw = await ws.recv()
                data = json.loads(raw)
            except (websockets.ConnectionClosed, json.JSONDecodeError) as e:
                logging.error(f"Sniffer connection lost: {e}. Reconnecting...")

                break

            method = data.get("method")
            msg_id = data.get("id")

            if msg_id == 1 and "result" in data:
                for target in data["result"].get("targetInfos", []):
                    if target.get("url", "").startswith("https://www.geoguessr.com"):
                        logging.info(f"Found GeoGuessr target: {target['url']}")
                        await ws.send(json.dumps({
                            "id": 2, "method": "Target.attachToTarget",
                            "params": {"targetId": target["targetId"], "flatten": True}
                        }))

            elif msg_id == 2 and "result" in data:
                attached_session = data["result"]["sessionId"]
                logging.info(f"Attached to GeoGuessr session: {attached_session}")
                await ws.send(json.dumps({"id": 3, "method": "Network.enable", "sessionId": attached_session}))

            elif method == "Network.webSocketFrameReceived":
                params = data.get("params", {})
                if attached_session and params.get("response", {}).get("payloadData"):
                    try:
                        payload = json.loads(params["response"]["payloadData"])
                        loc = extract_location(payload)
                        if loc:
                            logging.info(f"Location found: {loc}")
                            await broadcast(loc)
                    except json.JSONDecodeError:
                        pass 

            elif method == "Target.targetCreated":
                info = data.get("params", {}).get("targetInfo", {})
                if info.get("url", "").startswith("https://www.geoguessr.com"):
                    logging.info("GeoGuessr target (re)created. Attaching...")
                    await ws.send(json.dumps({
                        "id": 2, "method": "Target.attachToTarget",
                        "params": {"targetId": info["targetId"], "flatten": True}
                    }))

async def http_handler(request):
    """Handles HTTP requests to serve the main HTML file."""
    filepath = Path(__file__).parent / "index.html"
    if not filepath.exists():
        return web.Response(status=404, text="Error: index.html not found.")
    return web.FileResponse(filepath)

async def start_http_server():
    """Initializes and starts the aiohttp web server."""
    app = web.Application()
    app.router.add_get('/', http_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    await site.start()
    logging.info(f"Web server started. Open http://{HTTP_HOST}:{HTTP_PORT} in your browser.")

    while True:
        await asyncio.sleep(3600)

async def main():
    browser_ws_url = await get_browser_ws_url()
    if not browser_ws_url:
        return

    bridge_server = await serve(client_handler, BRIDGE_HOST, BRIDGE_PORT)
    logging.info(f"Bridge server listening on ws://{BRIDGE_HOST}:{BRIDGE_PORT}")

    http_task = asyncio.create_task(start_http_server())
    sniffer_task = asyncio.create_task(devtools_sniffer(browser_ws_url))

    await asyncio.gather(http_task, sniffer_task)

    bridge_server.close()
    await bridge_server.wait_closed()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server shutting down.")