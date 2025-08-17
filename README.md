# GeoGuessed

A simple, real-time companion map for GeoGuessr. This tool sniffs location data directly from your steam version of geoguessr while you play and displays it on a live, interactive map interface.

It runs locally on your machine and automates the connection process, so you can just run the script, open the map, and start playing.



## Features

-   **Real-time Location Tracking**: The map marker moves to your new location the instant a new round starts.
-   **Live Game Statistics**: Tracks total rounds, unique countries visited, total distance traveled, and session time.
-   **Location History**: See a list of your previous locations and click to jump back to them on the map.
-   **Interactive Controls**:
    -   Toggle a polyline trail to see your path.
    -   Switch between **Satellite**, **Dark**, and **Street** map styles.
    -   Enable/disable auto-zoom.
    -   Clear history and stats.
-   **Automatic Setup**: The script automatically finds your game's debugging information—no need to manually copy and paste WebSocket URLs.
-   **Keyboard Shortcuts**: Control the map with keys (`T` for trail, `Z` for zoom, `C` for clear, `1-3` for map styles).

## Requirements

-   Steam GeoGuessr.
-   [Python 3.7+](https://www.python.org/downloads/)
-   The following Python libraries: `websockets` and `aiohttp`.

## How to Run

Follow these steps to get the live map running.

#### 1. Download Files

Download the following two files and place them in the same directory:

-   `main.py` (the Python backend)
-   `index.html` (the frontend map page)

#### 2. Install Python Dependencies

Open your terminal or command prompt and install the required libraries using pip:

```bash
pip install websockets aiohttp
```

#### 3. Launch Your Game with Remote Debugging

You must start your game from the command line with the remote debugging flag enabled.

-   **Windows (in Command Prompt):**
    ```cmd
    "path\to\GeoGuessr.exe" --remote-debugging-port=9222
    ```

#### 4. Run the Python Server

In the same directory where you saved the files, run the Python script from your terminal:

```bash
python main.py
```

You should see output confirming that the web server and bridge server are running:

```
[INFO] Web server started. Open http://127.0.0.1:8080 in your browser.
[INFO] Bridge server listening on ws://127.0.0.1:8765
```

#### 5. Open the Live Map and Play!

1.  Open a new browser tab and navigate to **`http://localhost:8080`**.
2.  And wait till **`connecting`** changes to **`connected`** (it will show **`Map client connected`** in terminal when its successfully connected).
