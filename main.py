import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Set

import aiohttp
from aiohttp import web, WSMsgType
import websockets

# Import configuration
try:
    from config import *
except ImportError:
    # Fallback configuration if config.py is not available
    DEVTOOLS_HOST = "127.0.0.1"
    DEVTOOLS_PORT = 9222
    HTTP_HOST = "127.0.0.1"
    HTTP_PORT = 8080
    DEFAULT_BOT_CONFIG = {
        "enabled": False,
        "delay_seconds": 0.75,
        "accuracy_mode": "perfect"
    }
    MIN_DELAY_SECONDS = 0.1
    MAX_DELAY_SECONDS = 30.0
    VALID_ACCURACY_MODES = ["perfect", "nearby", "region"]
    LOG_FORMAT = '[%(asctime)s] [%(levelname)s] %(message)s'
    LOG_DATE_FORMAT = '%H:%M:%S'
    WS_RECONNECT_DELAY = 5
    INDEX_HTML_PATH = "index.html"

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT
)

# This will now store aiohttp WebSocketResponse objects
clients: Set[web.WebSocketResponse] = set()

# Global bot configuration
bot_config = DEFAULT_BOT_CONFIG.copy()

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
    """Sends a JSON object to all connected map clients using aiohttp."""
    if not clients:
        return

    # Remove closed connections
    closed_clients = {client for client in clients if client.closed}
    clients.difference_update(closed_clients)

    if not clients:
        return

    # Create a list of awaitable tasks
    tasks = [client.send_json(obj) for client in clients]
    if tasks:
        # Use asyncio.gather to send messages concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failed_clients = set()

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logging.warning(f"Failed to send to a client: {result}")
                # Mark client for removal
                failed_clients.add(list(clients)[i])

        # Remove failed clients
        clients.difference_update(failed_clients)


async def websocket_handler(request: web.Request):
    """Handles WebSocket connections using aiohttp."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    logging.info("🌐 Map client connected from frontend")
    clients.add(ws)
    
    try:
        # Send current bot status to the new client
        await ws.send_json({
            "type": "bot_status",
            "data": bot_config
        })
        logging.info("✅ Sent initial bot status to client")

        # Main loop to receive messages from the client
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    logging.info(f"📨 Received message from frontend: {data}")
                    
                    if data.get("type") == "bot_control":
                        if "enabled" in data:
                            if isinstance(data["enabled"], bool):
                                bot_config["enabled"] = data["enabled"]
                                logging.info(f"🤖 Bot {'enabled' if bot_config['enabled'] else 'disabled'} via web interface")
                            else:
                                logging.warning("Invalid bot enabled value - must be boolean")

                        if "delay_seconds" in data:
                            try:
                                delay = float(data["delay_seconds"])
                                if MIN_DELAY_SECONDS <= delay <= MAX_DELAY_SECONDS:
                                    bot_config["delay_seconds"] = delay
                                else:
                                    logging.warning(f"Invalid delay value - must be between {MIN_DELAY_SECONDS} and {MAX_DELAY_SECONDS} seconds")
                            except (ValueError, TypeError):
                                logging.warning("Invalid delay value - must be a number")

                        if "accuracy_mode" in data:
                            if data["accuracy_mode"] in VALID_ACCURACY_MODES:
                                bot_config["accuracy_mode"] = data["accuracy_mode"]
                            else:
                                logging.warning(f"Invalid accuracy mode - must be one of {VALID_ACCURACY_MODES}")

                        # Broadcast updated config to all clients
                        await broadcast({"type": "bot_status", "data": bot_config})
                        
                except json.JSONDecodeError as e:
                    logging.warning(f"Bad JSON from frontend: {e}")
            elif msg.type == WSMsgType.ERROR:
                logging.error(f"WebSocket connection closed with exception {ws.exception()}")

    except Exception as e:
        logging.error(f"❌ Error in WebSocket handler: {e}")
    finally:
        logging.info("🌐 Map client disconnected")
        clients.discard(ws)
        
    return ws


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

def adjust_coordinates_for_accuracy(lat: float, lng: float, mode: str) -> tuple[float, float]:
    """Adjust coordinates based on accuracy mode."""
    import random

    if mode == "perfect":
        return lat, lng

    # Use configuration-based offsets if available
    try:
        lat_offset_max, lng_offset_max = ACCURACY_OFFSETS.get(mode, (0.0, 0.0))
        if lat_offset_max == 0.0 and lng_offset_max == 0.0:
            return lat, lng

        lat_offset = random.uniform(-lat_offset_max, lat_offset_max)
        lng_offset = random.uniform(-lng_offset_max, lng_offset_max)
        return lat + lat_offset, lng + lng_offset
    except NameError:
        # Fallback to hardcoded values if config not available
        if mode == "nearby":
            lat_offset = random.uniform(-0.009, 0.009)
            lng_offset = random.uniform(-0.009, 0.009)
            return lat + lat_offset, lng + lng_offset
        elif mode == "region":
            lat_offset = random.uniform(-0.45, 0.45)
            lng_offset = random.uniform(-0.45, 0.45)
            return lat + lat_offset, lng + lng_offset
        else:
            return lat, lng

async def make_bot_guess(ws, attached_session: str, location: Dict[str, Any]):
    """Makes an automatic guess using the bot."""
    if not bot_config["enabled"]:
        return
    
    latitude = location['lat']
    longitude = location['lng']
    round_number = location.get('round', 1)
    
    guess_lat, guess_lng = adjust_coordinates_for_accuracy(
        latitude, longitude, bot_config["accuracy_mode"]
    )
    
    await asyncio.sleep(bot_config["delay_seconds"])

    js_to_make_guess = f"""
    (async () => {{
        try {{
            const gameId = window.location.pathname.split('/').pop();
            if (!gameId) throw new Error('Could not extract game ID from URL');
            
            const pinUrl = `https://game-server.geoguessr.com/api/duels/${{gameId}}/pin`;
            const guessUrl = `https://game-server.geoguessr.com/api/duels/${{gameId}}/guess`;

            const payload = {{
                lat: {guess_lat},
                lng: {guess_lng},
                roundNumber: {round_number},
                time: new Date().toISOString()
            }};

            const options = {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json', 'x-client': 'steam' }},
                body: JSON.stringify(payload),
                credentials: 'include'
            }};

            const pinResponse = await fetch(pinUrl, options);
            if (!pinResponse.ok) throw new Error(`Pin request failed: ${{pinResponse.status}} ${{pinResponse.statusText}}`);
            await pinResponse.json();

            await new Promise(resolve => setTimeout(resolve, 500)); 
            
            const guessResponse = await fetch(guessUrl, options);
            if (!guessResponse.ok) throw new Error(`Guess request failed: ${{guessResponse.status}} ${{guessResponse.statusText}}`);
            await guessResponse.json();
            
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed; top: 20px; right: 20px; background: #00ff88; color: #000;
                padding: 15px 25px; border-radius: 12px; font-weight: bold; z-index: 999999;
                animation: slideIn 0.3s ease;`;
            notification.textContent = '🤖 Bot guess submitted!';
            document.body.appendChild(notification);
            setTimeout(() => notification.remove(), 3000);
            
            return 'SUCCESS: Bot guess submitted';
        }} catch (error) {{
            console.error('🤖 BOT ERROR:', error);
            const errorNotification = document.createElement('div');
            errorNotification.style.cssText = `
                position: fixed; top: 20px; right: 20px; background: #ff4757; color: #fff;
                padding: 15px 25px; border-radius: 12px; font-weight: bold; z-index: 999999;`;
            errorNotification.textContent = '🤖 Bot error: ' + error.message;
            document.body.appendChild(errorNotification);
            setTimeout(() => errorNotification.remove(), 5000);
            throw error;
        }}
    }})();
    """

    logging.info(f"🤖 BOT: Making guess at ({guess_lat:.6f}, {guess_lng:.6f}) [mode: {bot_config['accuracy_mode']}]")
    
    try:
        await ws.send(json.dumps({
            "id": 100,
            "method": "Runtime.evaluate",
            "params": {"expression": js_to_make_guess, "awaitPromise": True},
            "sessionId": attached_session
        }))
        
        await broadcast({
            "type": "bot_action",
            "data": {
                "action": "guess_made",
                "original_location": {"lat": latitude, "lng": longitude},
                "guess_location": {"lat": guess_lat, "lng": guess_lng},
                "accuracy_mode": bot_config["accuracy_mode"],
                "round": round_number
            }
        })
        
    except Exception as e:
        logging.error(f"🤖 BOT ERROR: Failed to execute guess: {e}")

async def fetch_and_broadcast_profile(cookies: Dict[str, str]):
    """Fetches the user's profile using their cookies and broadcasts it."""
    profile_url = "https://www.geoguessr.com/api/v3/profiles/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) geoguessr-steam-edition/1.0.0 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36",
        "x-client": "steam"
    }

    try:
        async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
            async with session.get(profile_url) as response:
                if response.status == 200:
                    profile_data = await response.json()
                    if profile_data.get("user"):
                        logging.info(f"👤 Profile data fetched for user: {profile_data.get('user', {}).get('nick')}")
                        await broadcast({"type": "profile", "data": profile_data})
                    else:
                        logging.warning("Could not find user data in profile response.")
                else:
                    logging.error(f"Failed to fetch profile. Status: {response.status}, Body: {await response.text()}")
    except aiohttp.ClientError as e:
        logging.error(f"Error fetching profile: {e}")


async def devtools_sniffer(browser_ws: str):
    """Connects to the browser, finds the GeoGuessr tab, and sniffs network traffic."""
    logging.info("Starting DevTools sniffer...")
    last_location = None

    while True:
        try:
            async with websockets.connect(browser_ws) as ws:
                logging.info("Connected to browser DevTools")
                await ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
                attached_session = None
                geoguessr_found = False

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
                        geoguessr_found = False
                        for target in data["result"].get("targetInfos", []):
                            if "geoguessr.com" in target.get("url", "").lower():
                                logging.info(f"🎯 Found GeoGuessr target: {target['url']}")
                                geoguessr_found = True
                                await ws.send(json.dumps({
                                    "id": 2, "method": "Target.attachToTarget",
                                    "params": {"targetId": target["targetId"], "flatten": True}
                                }))
                                break
                        
                        if not geoguessr_found:
                            logging.warning("❌ No GeoGuessr tabs found. Make sure you have a GeoGuessr page open.")
                            await asyncio.sleep(5)
                            await ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))

                    elif msg_id == 2 and "result" in data:
                        attached_session = data["result"]["sessionId"]
                        logging.info(f"✅ Attached to GeoGuessr session: {attached_session}")
                        await ws.send(json.dumps({"id": 3, "method": "Network.enable", "sessionId": attached_session}))
                        await ws.send(json.dumps({"id": 5, "method": "Network.getAllCookies", "sessionId": attached_session}))
                        await ws.send(json.dumps({"id": 6, "method": "Runtime.enable", "sessionId": attached_session}))

                    elif msg_id == 5 and "result" in data: # Response for getAllCookies
                        cookies = {cookie['name']: cookie['value'] for cookie in data["result"].get("cookies", []) if ".geoguessr.com" in cookie['domain']}
                        if "session" in cookies:
                            logging.info("🍪 Extracted browser cookies. Fetching profile...")
                            # Fetch profile in the background
                            asyncio.create_task(fetch_and_broadcast_profile(cookies))
                        else:
                            logging.warning("Could not find necessary session cookies.")

                    elif msg_id == 100 and "result" in data:
                        result = data.get("result", {})
                        if "result" in result and "SUCCESS" in str(result.get("value")):
                            logging.info("🤖 BOT: Guess executed successfully")
                        elif "exceptionDetails" in result:
                            logging.error(f"🤖 BOT: JavaScript error: {result['exceptionDetails']}")
                        else:
                            logging.warning(f"🤖 BOT: Unexpected result: {result}")

                    elif method == "Network.webSocketFrameReceived":
                        params = data.get("params", {})
                        if attached_session and params.get("response", {}).get("payloadData"):
                            try:
                                payload = json.loads(params["response"]["payloadData"])
                                loc = extract_location(payload)
                                if loc and loc != last_location:
                                    logging.info(f"📍 Location found: {loc}")
                                    await broadcast({"type": "location", "data": loc})
                                    last_location = loc

                                    if bot_config["enabled"] and attached_session:
                                        asyncio.create_task(make_bot_guess(ws, attached_session, loc))
                            except json.JSONDecodeError:
                                pass

                    elif method == "Target.targetCreated":
                        info = data.get("params", {}).get("targetInfo", {})
                        if "geoguessr.com" in info.get("url", "").lower():
                            logging.info("🎯 GeoGuessr target (re)created. Attaching...")
                            await ws.send(json.dumps({
                                "id": 2, "method": "Target.attachToTarget",
                                "params": {"targetId": info["targetId"], "flatten": True}
                            }))

        except Exception as e:
            logging.error(f"DevTools sniffer error: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)

async def http_handler(request: web.Request) -> web.Response:
    """Handles HTTP requests to serve the main HTML file."""
    try:
        # Use configuration-based path if available
        try:
            filepath = Path(__file__).parent / INDEX_HTML_PATH
        except NameError:
            filepath = Path(__file__).parent / "index.html"

        if not filepath.exists():
            logging.error(f"HTML file not found: {filepath}")
            return web.Response(status=404, text="Error: HTML file not found.")
        return web.FileResponse(filepath)
    except Exception as e:
        logging.error(f"Error serving HTML file: {e}")
        return web.Response(status=500, text="Internal server error")

async def start_http_server():
    """Initializes and starts the aiohttp web server with WebSocket support."""
    app = web.Application()
    app.router.add_get('/', http_handler)
    app.router.add_get('/ws', websocket_handler)  # Add WebSocket route
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    await site.start()
    logging.info(f"Web server started. Open http://{HTTP_HOST}:{HTTP_PORT} in your browser.")
    
    # Wait indefinitely to keep the server running
    await asyncio.Event().wait()

async def main():
    browser_ws_url = await get_browser_ws_url()
    if not browser_ws_url:
        return

    logging.info("🤖 Bot system initialized and ready")

    http_task = asyncio.create_task(start_http_server())
    sniffer_task = asyncio.create_task(devtools_sniffer(browser_ws_url))

    await asyncio.gather(http_task, sniffer_task)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server shutting down.")