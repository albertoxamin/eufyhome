# Eufy Clean for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/albertoxamin/eufyhome.svg)](https://github.com/albertoxamin/eufyhome/releases)
[![GitHub Activity](https://img.shields.io/github/commit-activity/y/albertoxamin/eufyhome.svg)](https://github.com/albertoxamin/eufyhome/commits/main)

A Home Assistant custom integration for Eufy robot vacuums. This integration is based on the [Homey Eufy Clean integration](https://github.com/martijnpoppen/com.eufylife.home) and uses the [eufy-clean SDK](https://github.com/martijnpoppen/eufy-clean).

## Features

- Control your Eufy robot vacuum from Home Assistant
- Start, stop, pause, and return to dock
- Monitor battery level
- Change fan speed (quiet, standard, turbo, max)
- View work status and mode
- Error monitoring
- Locate your vacuum
- **Mopping support** (for compatible models):
  - Clean type: Sweep Only, Mop Only, Sweep and Mop
  - Mop water level: Low, Medium, High
  - Clean intensity: Standard, Deep Clean, Quick Clean
- **Map camera** - View your vacuum's map as an image

## Supported Devices

This integration supports a wide range of Eufy robot vacuums, including:

- RoboVac 11C, 11S
- RoboVac 15C MAX
- RoboVac 25C
- RoboVac 30C, 30C MAX
- RoboVac 35C
- RoboVac G10 Hybrid, G20, G20 Hybrid
- RoboVac G30, G30 Verge, G30 Hybrid, G30+ SES
- RoboVac G32, G35, G35+
- RoboVac G40, G40 Hybrid, G40 Hybrid+
- RoboVac G50
- RoboVac L60, L60 Hybrid, L60 SES, L60 Hybrid SES
- RoboVac L70 Hybrid
- RoboVac LR20, LR30 Hybrid, LR30 Hybrid+, LR35 Hybrid, LR35 Hybrid+
- RoboVac X8, X8 Hybrid, X8 Pro, X8 Pro SES
- RoboVac X9 Pro
- RoboVac X10 Pro Omni
- RoboVac S1
- RoboVac C10, C20
- RoboVac E20, E25, E28
- And more...

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL and select "Integration" as the category
6. Click "Add"
7. Search for "Eufy Clean" and install it
8. Restart Home Assistant

### Manual Installation

1. Download the latest release
2. Copy the `custom_components/eufy_clean` folder to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

## Configuration

1. Go to Settings → Devices & Services
2. Click "Add Integration"
3. Search for "Eufy Clean"
4. Enter your Eufy account email and password
5. Your devices will be automatically discovered

## Entities

### Vacuum Entity

The main vacuum entity provides the following features:

- **State**: Current state (cleaning, docked, returning, idle, error)
- **Battery Level**: Current battery percentage
- **Fan Speed**: Current cleaning intensity

#### Services

- `vacuum.start`: Start cleaning
- `vacuum.pause`: Pause cleaning
- `vacuum.stop`: Stop cleaning
- `vacuum.return_to_base`: Return to dock
- `vacuum.set_fan_speed`: Set fan speed (quiet, standard, turbo, max)
- `vacuum.locate`: Make the vacuum beep to help locate it

#### Room Cleaning Service

- `eufy_clean.clean_rooms`: Clean specific rooms by their IDs

**Example service call:**
```yaml
service: eufy_clean.clean_rooms
target:
  entity_id: vacuum.omni
data:
  room_ids: "[1, 2]"  # or "1, 2" for comma-separated
  clean_times: 1
```

**Finding Room IDs:**
Room IDs are assigned by your vacuum when it maps your home. To find them:
1. Use the Eufy Clean app to see room names and their order
2. Typically rooms are numbered starting from 1 in the order they were created
3. Check the vacuum entity's attributes for a `rooms` list if available

### Sensor Entities

- **Work Status**: Current work status (cleaning, charging, standby, etc.)
- **Work Mode**: Current work mode (auto, room, spot, etc.)
- **Clean Speed**: Current fan speed setting
- **Error**: Current error code (if any)

### Binary Sensor Entities

- **Charging**: Whether the vacuum is currently charging
- **Docked**: Whether the vacuum is currently docked

### Button Entities

- **Locate**: Make the vacuum beep to help locate it

### Select Entities (Mopping Models Only)

- **Clean Type**: Choose between Sweep Only, Mop Only, or Sweep and Mop
- **Mop Water Level**: Set the water level for mopping (Low, Medium, High)
- **Clean Intensity**: Set cleaning intensity (Standard, Deep Clean, Quick Clean)

### Camera Entity

- **Map**: View your vacuum's floor map as an image (requires Pillow and lz4 libraries)

## Troubleshooting

### Cannot connect to Eufy servers

- Make sure you're using the correct email and password
- Check that your Eufy account is working in the official Eufy Clean app
- Some accounts may require 2FA which is not currently supported

### Device not found

- Make sure your vacuum is online and connected to WiFi
- Try power cycling your vacuum
- Ensure the vacuum is visible in the Eufy Clean app

## Development

Linting and formatting use [Ruff](https://docs.astral.sh/ruff/). Configuration is in `pyproject.toml`.

```bash
# Install dev dependencies (optional)
pip install -e ".[dev]"

# Check lint and format
ruff check custom_components scripts
ruff format --check custom_components scripts

# Auto-fix and format
ruff check custom_components scripts --fix
ruff format custom_components scripts
```

## Credits

This integration is based on the work of:

- [Martijn Poppen](https://github.com/martijnpoppen) - Original Homey integration and eufy-clean SDK
- The Home Assistant community

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This integration is not affiliated with or endorsed by Eufy or Anker. Use at your own risk.
