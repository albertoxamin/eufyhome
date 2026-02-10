"""Constants for the Eufy Clean integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "eufy_clean"
MANUFACTURER: Final = "Eufy"

# Configuration
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"

# Attributes
ATTR_BATTERY_LEVEL: Final = "battery_level"
ATTR_WORK_STATUS: Final = "work_status"
ATTR_WORK_MODE: Final = "work_mode"
ATTR_CLEAN_SPEED: Final = "clean_speed"
ATTR_ERROR_CODE: Final = "error_code"
ATTR_IS_CHARGING: Final = "is_charging"
ATTR_IS_DOCKED: Final = "is_docked"

# Update interval
UPDATE_INTERVAL: Final = 30

# Device models mapping
EUFY_CLEAN_DEVICES: Final = {
    "T1250": "RoboVac 35C",
    "T2070": "Robovac 3-in-1 E20",
    "T2080": "Robovac S1",
    "T2103": "RoboVac 11C",
    "T2117": "RoboVac 35C",
    "T2118": "RoboVac 30C",
    "T2119": "RoboVac 11S",
    "T2120": "RoboVac 15C MAX",
    "T2123": "RoboVac 25C",
    "T2128": "RoboVac 15C MAX",
    "T2130": "RoboVac 30C MAX",
    "T2132": "RoboVac 25C",
    "T2150": "RoboVac G10 Hybrid",
    "T2181": "RoboVac LR30 Hybrid+",
    "T2182": "RoboVac LR35 Hybrid+",
    "T2190": "RoboVac L70 Hybrid",
    "T2192": "RoboVac LR20",
    "T2193": "RoboVac LR30 Hybrid",
    "T2194": "RoboVac LR35 Hybrid",
    "T2210": "Robovac G50",
    "T2250": "Robovac G30",
    "T2251": "RoboVac G30",
    "T2252": "RoboVac G30 Verge",
    "T2253": "RoboVac G30 Hybrid",
    "T2254": "RoboVac G35",
    "T2255": "Robovac G40",
    "T2256": "RoboVac G40 Hybrid",
    "T2257": "RoboVac G20",
    "T2258": "RoboVac G20 Hybrid",
    "T2259": "RoboVac G32",
    "T2261": "RoboVac X8 Hybrid",
    "T2262": "RoboVac X8",
    "T2266": "Robovac X8 Pro",
    "T2267": "RoboVac L60",
    "T2268": "Robovac L60 Hybrid",
    "T2270": "RoboVac G35+",
    "T2272": "Robovac G30+ SES",
    "T2273": "RoboVac G40 Hybrid+",
    "T2276": "Robovac X8 Pro SES",
    "T2277": "Robovac L60 SES",
    "T2278": "Robovac L60 Hybrid SES",
    "T2280": "Robovac C20",
    "T2292": "RoboVac C10",
    "T2320": "Robovac X9 Pro",
    "T2351": "Robovac X10 Pro Omni",
    "T2352": "Robovac E28",
    "T2353": "Robovac E25",
}

# Device models that support clean type (sweep / mop / both) - hybrid or mop-capable only
# Name-based: Hybrid, 3-in-1, Omni in display name
# Plus explicit codes for hybrid models whose name doesn't indicate it (e.g. Robovac C20)
EUFY_CLEAN_SUPPORTS_CLEAN_TYPE: Final = frozenset(
    [
        *(
            model
            for model, name in EUFY_CLEAN_DEVICES.items()
            if "Hybrid" in name or "Omni" in name
        ),
        "T2280",  # Robovac C20 - hybrid (sweep/mop)
    ]
)

# State mappings (for legacy API)
EUFY_CLEAN_GET_STATE: Final = {
    "sleeping": "idle",
    "standby": "docked",
    "recharge": "returning",
    "running": "cleaning",
    "cleaning": "cleaning",
    "spot": "cleaning",
    "completed": "docked",
    "charging": "docked",
    "sleep": "idle",
    "go_home": "returning",
    "fault": "error",
}

# Novel API state mappings
EUFY_CLEAN_NOVEL_STATE_MAP: Final = {
    "standby": "docked",
    "sleep": "idle",
    "fault": "error",
    "charging": "docked",
    "fast_mapping": "cleaning",
    "cleaning": "cleaning",
    "remote_ctrl": "cleaning",
    "go_home": "returning",
    "cruising": "cleaning",
}

# Work status mappings
EUFY_CLEAN_WORK_STATUS: Final = {
    "RUNNING": "Running",
    "CHARGING": "Charging",
    "STAND_BY": "Standby",
    "SLEEPING": "Sleeping",
    "RECHARGE_NEEDED": "Recharge",
    "RECHARGE": "Recharge",
    "COMPLETED": "Completed",
    "STANDBY": "Standby",
    "SLEEP": "Sleep",
    "FAULT": "Fault",
    "FAST_MAPPING": "Fast Mapping",
    "CLEANING": "Cleaning",
    "REMOTE_CTRL": "Remote Ctrl",
    "GO_HOME": "Go Home",
    "CRUISING": "Cruising",
}

# Clean speed options
EUFY_CLEAN_SPEEDS: Final = ["quiet", "standard", "turbo", "max"]

# Error codes
EUFY_CLEAN_ERROR_CODES: Final = {
    0: "none",
    1: "crash buffer stuck",
    2: "wheel stuck",
    3: "side brush stuck",
    4: "rolling brush stuck",
    5: "host trapped clear obst",
    6: "machine trapped move",
    7: "wheel overhanging",
    8: "power low shutdown",
    13: "host tilted",
    14: "no dust box",
    17: "forbidden area detected",
    18: "laser cover stuck",
    19: "laser sensor stuck",
    20: "laser blocked",
    21: "dock failed",
    26: "power appoint start fail",
    31: "suction port obstruction",
    32: "wipe holder motor stuck",
    33: "wiping bracket motor stuck",
    39: "positioning fail clean end",
    40: "mop cloth dislodged",
    41: "airdryer heater abnormal",
    50: "machine on carpet",
    51: "camera block",
    52: "unable leave station",
    55: "exploring station fail",
    70: "clean dust collector",
    71: "wall sensor fail",
    72: "robovac low water",
    73: "dirty tank full",
    74: "clean water low",
    75: "water tank absent",
    76: "camera abnormal",
    77: "3d tof abnormal",
    78: "ultrasonic abnormal",
    79: "clean tray not installed",
    80: "robovac comm fail",
    81: "sewage tank leak",
    82: "clean tray needs clean",
    83: "poor charging contact",
    101: "battery abnormal",
    102: "wheel module abnormal",
    103: "side brush abnormal",
    104: "fan abnormal",
    105: "roller brush motor abnormal",
    106: "host pump abnormal",
    107: "laser sensor abnormal",
    111: "rotation motor abnormal",
    112: "lift motor abnormal",
    113: "water spray abnormal",
    114: "water pump abnormal",
    117: "ultrasonic abnormal",
    119: "wifi bluetooth abnormal",
}

# Control commands
EUFY_CLEAN_CONTROL: Final = {
    "START_AUTO_CLEAN": 0,
    "START_SELECT_ROOMS_CLEAN": 1,
    "START_SELECT_ZONES_CLEAN": 2,
    "START_SPOT_CLEAN": 3,
    "START_GOTO_CLEAN": 4,
    "START_RC_CLEAN": 5,
    "START_GOHOME": 6,
    "START_SCHEDULE_AUTO_CLEAN": 7,
    "START_SCHEDULE_ROOMS_CLEAN": 8,
    "START_FAST_MAPPING": 9,
    "START_GOWASH": 10,
    "STOP_TASK": 12,
    "PAUSE_TASK": 13,
    "RESUME_TASK": 14,
    "STOP_GOHOME": 15,
    "STOP_RC_CLEAN": 16,
    "STOP_GOWASH": 17,
    "STOP_SMART_FOLLOW": 18,
    "START_GLOBAL_CRUISE": 20,
    "START_POINT_CRUISE": 21,
    "START_ZONES_CRUISE": 22,
    "START_SCHEDULE_CRUISE": 23,
    "START_SCENE_CLEAN": 24,
    "START_MAPPING_THEN_CLEAN": 25,
}

# DPS Maps
LEGACY_DPS_MAP: Final = {
    "PLAY_PAUSE": "2",
    "DIRECTION": "3",
    "WORK_MODE": "5",
    "WORK_STATUS": "15",
    "CLEANING_PARAMETERS": "154",
    "CLEANING_STATISTICS": "167",
    "ACCESSORIES_STATUS": "168",
    "GO_HOME": "101",
    "CLEAN_SPEED": "102",
    "FIND_ROBOT": "103",
    "BATTERY_LEVEL": "104",
    "ERROR_CODE": "106",
    "MAP_DATA": "165",
}

NOVEL_DPS_MAP: Final = {
    "PLAY_PAUSE": "152",
    "DIRECTION": "155",
    "WORK_MODE": "153",
    "WORK_STATUS": "153",
    "CLEANING_PARAMETERS": "154",
    "CLEANING_STATISTICS": "167",
    "ACCESSORIES_STATUS": "168",
    "GO_HOME": "173",
    "CLEAN_SPEED": "158",
    "FIND_ROBOT": "160",
    "BATTERY_LEVEL": "163",
    "ERROR_CODE": "177",
    "MAP_DATA": "170",
    "VOLUME": "161",
    "SCENE_LIST": "180",
    "DND": "157",
    "BOOST_IQ": "159",
}
