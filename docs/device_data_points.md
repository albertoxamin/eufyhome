# Device Data Points (DPS) Reference

Data points retrieved from the `get_product_data_point` API for the Eufy Robovac C20 (T2280).

## T2280 — Robovac C20 (Novel API)

31 DPS returned by the API. The `dp_id` is the string key used in MQTT/cloud DPS payloads.

| dp_id | Name                  | Type    | Range / Values                        | Mode | HA Entity          | Notes                                      |
|-------|-----------------------|---------|---------------------------------------|------|--------------------|--------------------------------------------|
| 152   | robovac_ctrl          | Raw     | base64 protobuf                       | rw   | vacuum (commands)  | Play/pause/home/stop via protobuf encoding |
| 153   | robovac_status        | Raw     | base64 protobuf                       | ro   | sensor (work_status, work_mode) | Decoded to state + mode       |
| 154   | robovac_clean_param   | Raw     | base64 protobuf                       | rw   | select (clean_type, mop_level, clean_extent) | Clean params protobuf |
| 155   | direction_ctrl        | Raw     | base64 protobuf                       | rw   | —                  | Remote control joystick                    |
| 156   | robovac_appoint_clean | Raw     | base64 protobuf                       | rw   | —                  | Scheduled cleaning config                  |
| 157   | robovac_dnd           | Raw     | base64 protobuf                       | rw   | —                  | Do Not Disturb schedule                    |
| 158   | robovac_suction       | Value   | 0-3 (quiet/standard/turbo/max)        | rw   | vacuum (fan_speed) | Clean speed as integer index               |
| 159   | robovac_ota           | Raw     | base64 protobuf                       | rw   | —                  | OTA firmware update                        |
| 160   | robovac_find          | Boolean | true/false                            | rw   | button (locate)    | Find my robot                              |
| 161   | robovac_volume        | Value   | 0-100                                 | rw   | **number (volume)**| Volume control — newly added               |
| 162   | robovac_language      | Value   | enum                                  | rw   | —                  | Voice language setting                     |
| 163   | robovac_battery       | Value   | 0-100                                 | ro   | sensor (battery)   | Battery percentage                         |
| 164   | robovac_map_v2        | Raw     | base64                                | ro   | —                  | Map data (v2 format)                       |
| 165   | robovac_map           | Raw     | base64                                | ro   | camera (map)       | Floor map for camera entity                |
| 166   | robovac_path          | Raw     | base64                                | ro   | —                  | Cleaning path data                         |
| 167   | robovac_clean_records | Raw     | base64 protobuf                       | ro   | —                  | Cleaning statistics/history                |
| 168   | robovac_accessories   | Raw     | base64 protobuf                       | ro   | —                  | Accessory wear status                      |
| 169   | robovac_map_v3        | Raw     | base64                                | ro   | —                  | Map data (v3 format)                       |
| 170   | robovac_ai_config     | Raw     | base64                                | rw   | —                  | AI obstacle avoidance config               |
| 171   | robovac_map_manage    | Raw     | base64                                | rw   | —                  | Multi-floor map management                 |
| 172   | robovac_clean_prefer  | Raw     | base64                                | rw   | —                  | Cleaning preferences per room              |
| 173   | robovac_return_station| Raw     | base64 protobuf                       | rw   | —                  | Go-home / station control                  |
| 174   | robovac_station_ctrl  | Raw     | base64 protobuf                       | rw   | —                  | Self-empty/wash station control             |
| 175   | robovac_station_param | Raw     | base64 protobuf                       | rw   | —                  | Station parameters                         |
| 176   | robovac_station_status| Raw     | base64 protobuf                       | ro   | —                  | Station status                             |
| 177   | robovac_error         | Raw     | base64 protobuf                       | ro   | sensor (error)     | Error/warning codes                        |
| 178   | robovac_wifi_info     | Raw     | base64                                | ro   | —                  | WiFi signal/connection info                |
| 179   | robovac_zone_clean    | Raw     | base64 protobuf                       | rw   | —                  | Zone cleaning areas                        |
| 180   | robovac_room_clean    | Raw     | base64 protobuf                       | rw   | service (clean_rooms) | Room-selective cleaning                 |
| 181   | robovac_cruise        | Raw     | base64 protobuf                       | rw   | —                  | Cruise/patrol mode                         |
| 182   | robovac_scene_clean   | Raw     | base64 protobuf                       | rw   | —                  | Scene-based cleaning                       |

## Coverage Summary

**Currently exposed in Home Assistant:**
- Vacuum entity: DPS 152 (control), 153 (status), 158 (fan speed), 160 (locate), 163 (battery)
- Sensors: DPS 153 (work_status, work_mode), 158 (clean_speed), 177 (error)
- Select entities: DPS 154 (clean_type, mop_level, clean_extent)
- Camera: DPS 165 (map)
- Button: DPS 160 (find robot)
- Number: DPS 161 (volume) -- **newly added**

**Candidates for future exposure:**
- DPS 157 (DND schedule) — could be a switch + time entities
- DPS 162 (language) — could be a select entity
- DPS 167 (clean records) — could be sensor attributes (total area, time)
- DPS 168 (accessories) — could be sensors (filter life %, brush life %)
- DPS 170 (AI config) — could be a switch (AI obstacle avoidance on/off)
- DPS 174/175 (station ctrl/param) — station entities for models with self-empty base

**Low priority / not applicable:**
- DPS 155 (direction) — remote control joystick, not suited to HA entities
- DPS 156 (scheduled clean) — better handled via HA automations
- DPS 159 (OTA) — firmware updates not appropriate for HA
- DPS 164/166/169 (map v2/path/map v3) — supplementary map data
