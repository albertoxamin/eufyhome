# Eufy Clean App - Assembly-CSharp Analysis

Analysis of the Unity C# panel embedded in the Eufy Clean Android app (v3.18.0).
Extracted from `global-metadata.dat` (IL2CPP metadata) which contains all C# type names,
method names, field names, and string literals from `Assembly-CSharp.dll`.

## Architecture Overview

The Eufy Clean app uses a **Unity 3D panel** for the vacuum map/control interface.
This panel communicates with the native Android layer (ThingClips/Tuya SDK) through
a `NativeBridge` class that passes JSON messages in both directions.

```
┌──────────────────────────────────────────────┐
│              Unity 3D Panel                   │
│  (Assembly-CSharp.dll, IL2CPP compiled)       │
│                                               │
│  State Machine ←→ Draw Classes ←→ Data Models │
│        ↕                                      │
│  NativeBridge (SendToNative / ProcessNative)  │
└──────────────────┬───────────────────────────┘
                   │ JSON messages
┌──────────────────┴───────────────────────────┐
│         Native Android Layer                  │
│  (ThingClips/Tuya SDK, dynamically loaded)    │
│                                               │
│  MQTT Client ←→ DPS Protocol ←→ HTTP API      │
└──────────────────┬───────────────────────────┘
                   │ MQTT / HTTP
┌──────────────────┴───────────────────────────┐
│         Robot Vacuum Device                   │
│  (Firmware, protobuf DPS encoding)            │
└──────────────────────────────────────────────┘
```

## NativeBridge Communication

### Bridge Class
- **`NativeBridge`** — singleton, mediates all communication between Unity and native
- **`SendToNative`** — Unity calls native Android methods
- **`ProcessNativeMessage`** — native sends data/events into Unity
- **`CallNativeMapError`** — error callback for map operations

### Events: Unity → Native (`ec_` prefix)

These are the event names Unity sends to the native layer to request actions:

| Event | Purpose |
|-------|---------|
| `ec_loadMapData` | Request map data from device |
| `ec_initScene` | Initialize the 3D scene |
| `ec_updateStatus` | Request status update |
| `ec_updateTheme` | Change visual theme |
| `ec_updateSceneLayout` | Update scene layout |
| `ec_nextAction` | Advance to next cleaning action |
| `ec_roomEditAction` | Room editing operation |
| `ec_floorEditAction` | Floor type editing |
| `ec_carpetEditAction` | Carpet zone editing |
| `ec_forbiddenAreaEditAction` | No-go zone editing |
| `ec_furnitureEditAction` | Furniture placement editing |
| `ec_zoneCleanAction` | Zone cleaning operation |
| `ec_sceneTaskOperation` | Scene/task operation |
| `ec_rotateOperation` | Map rotation |
| `ec_getSelectedRoomId` | Get currently selected room |
| `ec_setSelectedRoomIds` | Set selected rooms |
| `ec_clearSelectedRoomId` | Clear room selection |
| `ec_getSceneTaskData` | Get scene task data |
| `ec_isCanRotate` | Check if rotation allowed |

### Events: Native → Unity

| Handler | Purpose |
|---------|---------|
| `ProcessNativeMessage` | Main handler for all incoming data |
| `OnMapDataReceived` | New map data available |
| `OnMapDataReceivedEvent` | Event fired after map data processing |
| `OnMapEditStatusReceived` | Map edit operation result |
| `DeserializeMapData` | Parse incoming map JSON into MapData |
| `RefreshByNewMapData` | Refresh all draw components with new data |

## Data Model Classes

### MapData (root object)

The central data structure passed from native to Unity. Confirmed fields from error messages:

```csharp
class MapData {
    MapInfo   mapInfo;            // Map metadata (dimensions, origin, etc.)
    PathInfo[] pathInfo;          // Cleaning path data (array of paths)
    RoomInfo[] roomInfo;          // Room definitions
    ChargeInfo chargeInfo;        // Charging dock position
    ObstacleInfo obstacleInfo;    // Detected obstacles
    FurnitureInfo[] furnitureInfo; // Furniture placements
    float[][] outterWallPoints;   // Outer wall boundary points
    float[][] innerFillPoints;    // Interior fill points
}
```

### MapInfo

```csharp
class MapInfo {
    int    mapId;        // Unique map identifier
    string mapName;      // User-visible map name (e.g., "Map1")
    float  gridSize;     // Grid cell size
    float  gridOrigin;   // Grid origin offset
    int    resolution;   // Map resolution
    int    width;        // Map width in pixels/cells
    int    height;       // Map height in pixels/cells
}
```

### PathInfo

The cleaning path structure — this is what draws the robot's trajectory on the map.

```csharp
class PathInfo {
    float[][] pathPoints;  // Array of (x,y) coordinate pairs
    int       pathType;    // Path type identifier
    Color     pathColor;   // Rendering color
    float     pathWidth;   // Line width for rendering
}
```

Accessed by `PathDraw` which creates Unity `LineRenderer` objects:
- `PathDraw.CreateOrUpdatePathObject(pathIndex, dataIndex)`
- Creates `Line_{index}` GameObjects with LineRenderer components
- Iterates `mapData.pathInfo` array (supports multiple paths)
- Each PathInfo at index `i` can be null (checked: `pathInfo at index {0} is null`)

### RoomInfo

```csharp
class RoomInfo {
    int    roomId;       // Unique room ID
    string roomName;     // Room name
    int    roomType;     // Room type enum
    int    floorType;    // Floor type (hardwood, carpet, tile, etc.)
    float[] roomCenter;  // Center point coordinates
    int    roomSize;     // Room area
}
```

### ChargeInfo

```csharp
class ChargeInfo {
    float chargePoint;    // Dock position
    float chargeTheta;    // Dock orientation angle
}
```

### ObstacleInfo

```csharp
class ObstacleInfo {
    float[][] obstaclePoints;  // Obstacle boundary points
    float     obstaclePoint;   // Single obstacle reference point
}
```

### CarpetInfo

```csharp
class CarpetInfo {
    int       carpetId;      // Carpet zone ID
    int       carpetType;    // Carpet type
    float[][] carpetPoints;  // Carpet boundary points
    int       carpetColorType; // Color for rendering
    int       carpetAction;  // Action when on carpet (boost, avoid, etc.)
}
```

### FurnitureInfo

```csharp
class FurnitureInfo {
    string furnitureId;     // Furniture ID
    string furnitureType;   // Type (chair, table, bed, etc.)
    float  furnitureWidth;  // Width
    float  furnitureHeight; // Height
    float  furnitureLenght; // Length (sic - typo in original code)
}
```

### ZoneInfo

```csharp
class ZoneInfo {
    float[][] zonePoints;  // Zone boundary coordinates
    int       zoneAction;  // Zone cleaning action
    int       zoneHour;    // Scheduled hour
    int       zoneMinute;  // Scheduled minute
}
```

### SceneTask

```csharp
class SceneTask {
    // Referenced via sceneTaskInfo, sceneTaskEdit
    // Managed by State_clean_SceneTask
}
```

### CleanRecord

```csharp
class CleanRecord {
    // Cleaning history entry
    // Managed by State_clean_CleanRecord
}
```

### RoomEditor

```csharp
class RoomEditor {
    string action;         // Edit action type
    object data;           // Action-specific data
    // Used for room merge, divide, rename operations
}
```

## Map Management

### Map List Fields

```csharp
// Found in State_clean_MapManager context:
List<MapOption> mapList;      // List of available maps
int             mapCount;     // Number of maps
int             mapIndex;     // Currently selected map index
int             mapId;        // Currently active map ID
string          mapName;      // Currently active map name
MapOption       mapOption;    // Current map option
MapOption       currentMapOption; // Active map selection
MapData         currentMapData;   // Currently loaded map data
```

### MapOption

```csharp
class MapOption {
    // Map selection option in the map manager UI
    // MapOptionText - display text for the option
    // MapIdx - index identifier
}
```

### Map Data Buffers

```csharp
MapData _currentMapData;    // Active map being displayed
MapData _receivedMapData;   // Newly received map data (pre-swap)
MapData _bufferMapData;     // Buffer for smooth transitions
```

## API Classes

These classes implement the Unity-to-Native bridge calls:

| API Class | Purpose | Bridge Event |
|-----------|---------|-------------|
| `LoadMapDataAPI` | Load/refresh map data | `ec_loadMapData` |
| `MapEditStatusAPI` | Query map edit status | — |
| `InitSceneAPI` | Initialize 3D scene | `ec_initScene` |
| `UpdateStatusAPI` | Update device status | `ec_updateStatus` |
| `UpdateThemeAPI` | Change visual theme | `ec_updateTheme` |
| `UpdateSceneLayoutAPI` | Update layout | `ec_updateSceneLayout` |
| `NextActionAPI` | Trigger next action | `ec_nextAction` |
| `RoomEditAPI` | Room edit operations | `ec_roomEditAction` |
| `FloorEditAPI` | Floor type editing | `ec_floorEditAction` |
| `CarpetEditAPI` | Carpet zone editing | `ec_carpetEditAction` |
| `ForbiddenAreaEditAPI` | No-go zone editing | `ec_forbiddenAreaEditAction` |
| `FurnitureEditAPI` | Furniture placement | `ec_furnitureEditAction` |
| `ZoneCleanAPI` | Zone clean commands | `ec_zoneCleanAction` |
| `SceneTaskAPI` | Scene task control | `ec_sceneTaskOperation` |
| `RotateControlAPI` | Map rotation control | — |
| `RotateOperationAPI` | Rotation operations | `ec_rotateOperation` |
| `GetSelectedRoomIdAPI` | Get selected room | `ec_getSelectedRoomId` |
| `SetSelectedRoomIdsAPI` | Set selected rooms | `ec_setSelectedRoomIds` |
| `ClearSelectedRoomIdAPI` | Clear selection | `ec_clearSelectedRoomId` |
| `ClearOperationAPI` | Clear operations | — |
| `GetSceneTaskDataAPI` | Get task data | `ec_getSceneTaskData` |
| `SendAreaUnitInfoAPI` | Area unit config | — |
| `ConfigurationUnityAPI` | Unity config | — |
| `DisposeUnityAPI` | Cleanup/dispose | — |
| `ResourcesAPI` | Resource management | — |
| `SceneManagerAPI` | Scene management | — |

## Draw (Renderer) Classes

These Unity MonoBehaviour classes render map elements as 3D objects:

| Draw Class | Renders | Data Source |
|------------|---------|-------------|
| `PathDraw` | Cleaning path trajectory | `mapData.pathInfo[]` |
| `RoomInfoDraw` | Room boundaries and labels | `mapData.roomInfo[]` |
| `OutterWallDraw` | Outer wall boundaries | `mapData.outterWallPoints` |
| `InnerFillDraw` | Interior floor fill | `mapData.innerFillPoints` |
| `ForbiddenDraw` | No-go zones | `forbiddenZones` |
| `CarpetInfoDraw` | Carpet zones | `carpetInfos` |
| `FurnitureDraw` | Furniture (2D and 3D) | `mapData.furnitureInfo[]` |
| `ObstacleDraw` | Detected obstacles | `obstacleInfo` |
| `ZoneDraw` | Cleaning zones | `zoneList` |
| `PopUpDraw` | Info popups | popup data |
| `PixelPointDraw` | Pixel-level map points | `pixelPointInfo` |
| `ShadowsDraw` | Shadows | shadow data |
| `PresentAfterDraw` | Post-processing | — |
| `ModelDraw` | 3D models | — |
| `InitRoomDraw` | Initial room setup | — |
| `BaseDraw` | Base class | — |
| `CallDraw` | Generic draw calls | — |

All draw classes implement `OnMapDataReceived` which is called when new `MapData` arrives.

### PathDraw Details

From error messages, the PathDraw rendering flow is:

1. `OnMapDataReceived` called with new `mapData`
2. Validates `mapData` is not null/empty
3. Checks `mapData.mapInfo` is not null (needed for coordinate mapping)
4. Checks `mapData.pathInfo` is not null
5. Iterates each `pathInfo[i]` in the array
6. Calls `CreateOrUpdatePathObject(pathIndex, dataIndex)` for each
7. Creates/updates `Line_{index}` GameObjects with Unity `LineRenderer` components
8. LineRenderer draws the path polyline from `pathInfo.pathPoints`

## State Machine

The vacuum control panel uses a state machine with these states:

| State | Description |
|-------|-------------|
| `State_clean_MapManager` | Map selection/management (multi-floor) |
| `State_clean_Mapping` | Robot is mapping (creating new map) |
| `State_clean_MapEdit` | Editing map properties |
| `State_clean_DisplayOnly` | View-only mode (no controls) |
| `State_HouseClean` | Full house clean preparation |
| `State_clean_HouseCleaning` | House clean in progress |
| `State_clean_RoomClean` | Room clean preparation |
| `State_clean_RoomCleaning` | Room clean in progress |
| `State_clean_RoomEdit` | Room editing (divide, merge, rename) |
| `State_clean_ZoneClean` | Zone clean preparation |
| `State_clean_ZoneCleaning` | Zone clean in progress |
| `State_clean_CarpetClean` | Carpet clean preparation |
| `State_clean_CarpetCleaning` | Carpet clean in progress |
| `State_clean_CarpetEdit` | Carpet zone editing |
| `State_clean_FloorEdit` | Floor type editing |
| `State_clean_ForbiddenEdit` | No-go zone editing |
| `State_clean_FurnitureClean` | Furniture-aware clean |
| `State_clean_FurnitureCleaning` | Furniture clean in progress |
| `State_clean_FurnitureEdit` | Furniture placement editing |
| `State_clean_SceneTask` | Scene/task management |
| `State_clean_TimingMode` | Scheduled cleaning |
| `State_clean_CleanRecord` | Cleaning history view |

### State_clean_MapManager

Manages the map list UI. Key members:
- `mapList` — list of available maps
- `mapCount` — total maps
- `mapIndex` — selected index
- `mapOption` / `currentMapOption` — current selection
- Triggers `ec_loadMapData` to load selected map

### State_clean_RoomEdit

Room editing operations from error messages:
- `DoBeforeEnter` / `DoBeforeExit` — state transition hooks
- `DoDivide` — split a room into two
- `DoMergeCheck` — check if rooms can be merged
- `OnDivideConfirm` — confirm room division
- `OnNotifySelected` / `OnNotifyDisSelected` — room selection in UI
- `ChangeToRename` / `ChangeToSaveRename` — room rename flow
- `DoRenameSelect` — select room for renaming
- `OnRoomEditorReceived` — receive room edit result
- `OnClearOperationReceived` — clear edit operation
- `OnGetSelectedRoomId` — get selected room for editing
- `DivideRefresh` — refresh after room division
- `AreRoomsConnected` — check room connectivity for merge

Uses `DivideScanLine` component for visual room division and `EditManager` singleton.

## Native Libraries

| Library | Purpose |
|---------|---------|
| `libMapBeautyJni.so` | Native map image post-processing (beautification) |
| `libmain.so` | Unity engine main |
| `libunity.so` | Unity runtime |
| `libil2cpp.so` | IL2CPP runtime (C# → native) |

### libMapBeautyJni.so Strings

Contains references to: `pixel`, `map`, `image`, `render`, `beautify`, `filter`, `smooth`,
suggesting it handles map image post-processing (anti-aliasing, smoothing of map edges).

## React Native Device Panel (Eufy_Clean_Rn35)

The actual device control logic lives in a separate React Native bundle
(`RN_ANDROID_Eufy_Clean_Rn35.zip`, 2.2MB). This panel handles settings, map
management, cleaning control, and scene/schedule management. It communicates
with the native ThingClips/Tuya SDK through a `deviceBridge` native module.

### Architecture

```
React Native JS (Eufy_Clean_Rn35)
  ├── propsService   — read-only queries (getters)
  ├── cmdService     — write commands (setters)
  └── pushService    — push notifications / events
         ↕
    deviceBridge (React Native Native Module)
         ↕
    ThingClips/Tuya SDK (Java/Kotlin, dynamically loaded)
         ↕
    MQTT DPS Protocol (protobuf-encoded)
         ↕
    Robot Vacuum Device
```

### Map List Call Chain

```javascript
// JS: getMapList()
getMapList = (successCb, errorCb) => {
    deviceService.propsService.getMultiMapDatas(successCb, errorCb);
}

// propsService.getMultiMapDatas()
getMultiMapDatas(successCb, errorCb) {
    this.deviceBridge.fetchMapDatas(
        (results) => {
            // results is an array of JSON strings
            const maps = Array.isArray(results)
                ? results.map(i => JSON.parse(i))
                : [];
            successCb(maps);
        },
        (error) => { errorCb(error); }
    );
}

// Also available as a Promise:
getMapList = () => new Promise((resolve, reject) => {
    deviceService.propsService.getMultiMapDatas(resolve, reject);
});
```

The `fetchMapDatas` call goes into the ThingClips SDK native module, which
constructs the protobuf DPS 171 message, sends it via MQTT, receives the
response, deserializes it, and returns it as an array of JSON-stringified
map objects back to JS.

### Map Management Bridge Methods

All communication with the device goes through the `deviceBridge` native module.
These methods are implemented in the ThingClips/Tuya SDK (Java/Kotlin).

#### Map Query Methods (propsService)

| Method | Purpose | Returns |
|--------|---------|---------|
| `fetchMapDatas(cb)` | Get full map list | Array of JSON map strings |
| `fetchSimpleMapDatas(cb)` | Get simplified map list | Simplified map array |
| `getCurrentMapNameAndId(nameCb, idCb)` | Get active map name + ID | string, int |
| `getmultiMapsState(cb)` | Get multi-map enabled state | boolean |
| `getMapDatas(cb)` | Get map data for rendering | MapData objects |
| `isSupportMultiMapsSwitch()` | Check multi-map support | boolean |
| `maxMapCount()` | Max storable maps | int |
| `existValideMap(cb)` | Check if valid map exists | boolean |

#### Map Command Methods (cmdService)

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `sendMapUseCmd(mapId, cb)` | Map ID to activate | Switch to a saved map |
| `sendMapSaveCmd(cb)` | — | Save current map |
| `sendMapDeleteCmd(mapId, cb)` | Map ID to delete | Delete a saved map |
| `sendMapRenameCmd(mapId, cb)` | Map ID + new name | Rename a saved map |
| `sendMapReplaceCmd(mapId, cb)` | Map ID to replace | Replace with current map |
| `sendMapResetCmd(cb)` | — | Reset/clear map |
| `sendMapRevertCmd(mapId, cb)` | Map ID | Revert map edits |
| `sendMultiMapsCmd(cmd, cb)` | Command payload | Generic multi-map command |
| `sendMultiMapsCloseCmd(cmd, cb)` | Command payload | Disable multi-map mode |
| `sendDoNotSaveMapCmd(cb)` | — | Discard unsaved map |
| `operateTempMap(op, id, cb)` | Operation, map ID | Temp map operations |

#### Map Editing Methods

| Method | Purpose |
|--------|---------|
| `splitRoomEvent(...)` | Divide a room |
| `mergeRoomEvent(...)` | Merge rooms |
| `setRoomNameEvent(...)` | Rename a room |
| `renameMultipleRooms(...)` | Batch rename rooms |
| `supportRenamingMultipleRooms()` | Check batch rename support |
| `setForbiddenAreaEvent(...)` | Set no-go zones |
| `setFurnitureEvent(...)` | Place furniture markers |
| `rotateMapEvent(...)` | Rotate map orientation |
| `revertEditMap(...)` | Revert map edits |
| `clearRealMap(...)` | Clear the real-time map |
| `addMapMethod(...)` | Add map rendering method |

#### Cleaning Command Methods

| Method | Purpose |
|--------|---------|
| `startAutoCleanEvent(...)` | Start full house clean |
| `startRoomCleanEvent(...)` | Start room clean |
| `startZoneCleanEvent(...)` | Start zone clean |
| `startCarpetCleanEvent(...)` | Start carpet clean |
| `startMapThenClean(...)` | Map first, then clean |
| `quickMappingEvent(...)` | Quick mapping mode |
| `continueMappingEvent(...)` | Continue mapping |
| `pauseTaskEvent(cb)` | Pause current task |
| `continueTaskEvent(cb)` | Resume paused task |
| `stopTaskEvent(...)` | Stop current task |
| `reChargingEvent(...)` | Send to dock |
| `customOrderSelectRooms(...)` | Set room clean order |

#### Device Status Methods

| Method | Returns |
|--------|---------|
| `getDeviceCurrentState(cb)` | Current device state |
| `getMainStatus(cb)` | Main status string |
| `getSubStatus(cb)` | Sub status string |
| `getBattery(cb)` | Battery percentage |
| `getErrorCode(cb)` | Current error code |
| `isRunning(cb)` | Is cleaning |
| `isPaused(cb)` | Is paused |
| `isCharging(cb)` | Is charging |
| `isIdle(cb)` | Is idle |
| `getCleanArea(cb)` | Current clean area |
| `getCleanTime(cb)` | Current clean time |
| `getSuction(cb)` | Current suction level |
| `getVolume(cb)` | Volume level |
| `isDeviceOnline(cb)` | Device online state |

### Map List UI Flow (from React Native code)

```javascript
// 1. On entering map manager screen:
Loading.show();
InteractionManager.runAfterInteractions(function*() {
    // Fetch current map ID
    vm.getCurrentMapId((id) => { setCurrentMapId(id) });

    // Fetch map list
    vm.getMapList(
        (maps) => {
            // maps = array of { id, name, mapid, ... }
            console.log('fetchMapList: ' + JSON.stringify(maps));
            setMapListData(maps);
        },
        (error) => { Loading.hidden(); }
    );
});

// 2. Map list rendered in FlatList with 2 columns
<FlatList numColumns={2} data={mapListData} ... />

// 3. Switch map:
vm.fetchSetCurrentMap(mapId, callback);
// which calls: cmdService.sendMapUseCmd(mapId, callback)

// 4. Save as new map:
vm.saveAsNewMap(callback);
// which calls: cmdService.sendMapSaveCmd(callback)
```

## DPS Mapping (from integration codebase, for reference)

The native layer translates between DPS protobuf data and the MapData JSON structure:

| DPS | Name | Maps to |
|-----|------|---------|
| 164 | robovac_map_v2 | Last clean record metadata |
| 165 | robovac_map | Map pixel data (legacy) |
| 166 | robovac_path | PathInfo[] (cleaning trajectory) |
| 167 | robovac_clean_records | CleanRecord history |
| 170 | MAP_DATA (novel) | Map metadata/reference |
| 171 | multi_maps_ctrl | MapManager map list (mapList, mapId, mapName) |
| 172 | multi_maps_mng | Map management commands |
| 179 | robovac_zone_clean | ZoneInfo data |

## Key Observations

1. **PathInfo is an array** — the device can send multiple path segments, each with
   their own type, color, and width. This supports showing different path segments
   (e.g., vacuum vs mop path, outbound vs return).

2. **MapData is the central data object** — all draw components receive the same
   MapData instance via `OnMapDataReceived` and extract their relevant sub-objects.

3. **Map management is state-based** — `State_clean_MapManager` handles multi-floor
   map selection. The `mapList` / `mapOption` fields suggest the device can store
   multiple named maps.

4. **The native bridge is JSON-based** — Unity C# serializes/deserializes JSON
   (`DeserializeMapData`), not protobuf directly. The ThingClips SDK handles
   protobuf ↔ JSON translation.

5. **Room editing is sophisticated** — supports divide (with visual scan line),
   merge (with connectivity check), and rename operations, all coordinated
   through `RoomEditor` actions.

6. **Furniture is 3D** — `FurnitureDraw` supports both 2D and 3D furniture rendering
   (`ProcessFurnitureInfo2D`, `ProcessFurnitureInfo3D`), with prefab loading by
   furniture type and sub-type.

7. **The protobuf encoding layer is in the ThingClips/Tuya SDK** — the JS code calls
   `deviceBridge.fetchMapDatas()` and gets back parsed JSON. The actual construction
   of DPS 171 protobuf messages happens in the compiled Java/Kotlin SDK which is
   dynamically loaded at runtime and not included in the APK's DEX.

## Tuya SDK Research (DPS 171 Protocol)

### Findings Summary

Extensive searching of Tuya official repos, community projects, and documentation
revealed that the **DPS 171 protobuf format is not publicly documented anywhere**.

### Standard Tuya vs Eufy DPS

Standard Tuya gyroscope sweepers use **DPs 1-19** with simple types (bool, enum,
value, string, bitmap, raw). Eufy devices deviate significantly with **custom DPs
152-182** using protobuf encoding. This is entirely non-standard.

| Function | Standard Tuya DPID | Eufy Novel DPID |
|---|---|---|
| Power/Control | 1 (switch), 2 (switch_go) | 152 (protobuf) |
| Status | 5 (enum) | 153 (protobuf) |
| Mode | 3 (enum) | 153 (combined with status) |
| Battery | 6 (integer %) | 163 (integer %) |
| Map Data | 19 (raw config) + stream | 165 (base64 raw) |
| Map Management | — | 171 (base64 raw) |

### ThingModel Method Enum for DPS 171

From the APK's ThingModel JSON (`T2278_thing.json`), the `ecl_multi_maps_manage_method`
field has range 0-9, mapping to:

| Value | Method | Action |
|-------|--------|--------|
| 0 | `ecl_get_all_map_data` | Get map list |
| 1 | `ecl_used_map` | Switch to map |
| 2 | `ecl_save_map` | Save map |
| 3 | `ecl_delete_map` | Delete map |
| 4 | `ecl_rename_map` | Rename map |
| 5 | `ecl_replace_map` | Replace map |
| 6 | `ecl_reset_map` | Reset map |
| 7 | `ecl_revert_map` | Revert map |
| 8 | `ecl_ignore_map` | Discard map |
| 9 | `ecl_open_map_manage_switch` | Enable multi-map |

Related ThingModel properties:
- `ecl_multi_maps_manage_result` — int, range 0-2 (success/failure/busy)
- `ecl_multi_maps_manage_seq` — int (sequence number for request/response matching)
- `ecl_device_info_appfunction_multi_maps_option` — int (multi-map configuration)
- `ecl_device_info_appfunction_multi_maps_version` — int, range 0-1 (protocol version)
- `ecl_unisetting_multi_map` — bool (multi-map feature enabled/disabled)

### Reconstructed Protobuf Schema for DPS 171

Based on ThingModel, app decompilation, and protocol analysis:

```protobuf
// Request (sent to device)
message MultiMapsCtrlRequest {
    uint32 method = 1;    // 0=GET_LIST, 1=USE, 2=SAVE, 3=DELETE, 4=RENAME, ...
    uint32 seq = 2;       // Sequence number (optional)
    int32  map_id = 3;    // Target map ID (for USE, DELETE, RENAME, REPLACE, REVERT)
    string map_name = 4;  // New name (for RENAME)
}

// Response (from device)
message MultiMapsCtrlResponse {
    uint32 method = 1;           // Echo of request method
    uint32 seq = 2;              // Echo of sequence number
    uint32 result = 3;           // 0=success, 1=failure, 2=busy
    repeated MapInfo maps = 4;   // Map list (for GET_LIST response)
    int32 current_map_id = 5;    // Currently active map ID
}

message MapInfo {
    int32  map_id = 1;
    string map_name = 2;
    int32  map_index = 3;
}
```

**Note:** This schema is reconstructed/speculative. The exact field numbers need
to be confirmed by capturing actual device responses.

### Tuya Cloud Map API

Community projects (`oven-lab/tuya_cloud_map_extractor`, `jaidenlabelle/tuya-vacuum`)
revealed a **Tuya Cloud API** for map download:

```
GET /v1.0/users/sweepers/file/{device_id}/realtime-map
```

Returns download URLs with `map_type`:
- **0** = Layout (map pixels)
- **1** = Path (cleaning trajectory)

Map binary format (NOT protobuf):
- 24-byte header: version, map_id, type, width, height, origin_x, origin_y,
  resolution, charger_x, charger_y, total_count, compressed_length
- LZ4-compressed pixel data
- Version 1 includes room metadata appended after pixel data

**However**, this is the standard Tuya cloud API. Eufy uses its own API servers
(`aiot-clean-api-pr.eufylife.com`) and may not expose this endpoint.

### Tuya Mobile API Testing Results

Using the Tuya mobile API authentication flow from `damacus/robovac`:
- **Auth flow**: Eufy login → get `user_id` → Tuya uid = `"eh-" + user_id` → authenticate
  with `a1.tuyaeu.com/api.json` using HMAC-signed requests
- **Tuya home**: Found (gid=68675423, `tuya_home_id` from Eufy settings matches)

**T2118** (older RoboVac, device_id `700203248caab5f05019`):
- `tuya.m.device.get` → **SUCCESS**: Returns full Tuya device data
- Standard DPS (1, 2, 3, 5, 15, 101-106), `localKey` available
- This device IS registered on the Tuya cloud

**T2280** (Omni C20, device_id `APY2802E37201809`):
- `tuya.m.device.get` → **PERMISSION_DENIED**: "No access"
- `tuya.m.device.dp.get` → **PERMISSION_DENIED**
- `tuya.m.device.media.latest` → **PERMISSION_DENIED**
- This device is **NOT registered on the Tuya cloud**

**Conclusion**: The T2280 (Omni C20) is a newer Eufy device managed entirely
through Eufy's own MQTT infrastructure (`aiot-clean-api-pr.eufylife.com`).
The Tuya cloud sweeper APIs (including map download) are not available for it.
Map and path data can ONLY be accessed via MQTT DPS:
- **DPS 166** (robovac_path): Path data, only populated during active cleaning
- **DPS 165** (robovac_map): Map pixel data
- **DPS 171** (multi_maps_ctrl): Map list management via protobuf

### React Native Multi-Map Data Model

From the RN bundle, the `MultiMapModel` used in the JS layer has these fields:

```javascript
{
    deviceId: string,    // Device serial number
    mapName: string,     // User-visible name (e.g., "Map1")
    mapIndex: number,    // Position in list
    mapId: number,       // Unique map identifier
    isCurrentMap: bool,  // Whether this map is active
    hasChargingPile: bool, // Whether map has dock location
    updateTime: number,  // Last modification timestamp
    isShouldRevert: bool, // Pending revert flag
    revertUpdateTime: number // Revert timestamp
}
```

The app checks multi-map support via `deviceBridge.isSupportMultiMapsSwitch()`
(fully native, returns boolean). It checks current state via
`deviceBridge.getmultiMapsState()`. Max map count via `deviceBridge.maxMapCount()`.

### Key Repositories

| Repository | Stars | Description |
|---|---|---|
| `oven-lab/tuya_cloud_map_extractor` | 37 | Tuya cloud map extractor for HA |
| `jaidenlabelle/tuya-vacuum-maps` | 21 | Tuya vacuum maps in HA |
| `jaidenlabelle/tuya-vacuum` | 5 | Python library for Tuya vacuum maps |
| `tuya/tuya-iotos-embedded-sweeper-demo` | — | Official embedded sweeper demo (DPs 1-19) |
| `tuya/tuya-sweeper-ios-sdk` | — | Official iOS sweeper SDK (closed-source) |
