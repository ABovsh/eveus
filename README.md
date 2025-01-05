Integration for Eveus EV charger in Home Assistant
Installation

Using HACS (recommended):

Add this repository to HACS as a custom repository:

Open HACS in your Home Assistant instance
Click on the three dots in the top right corner
Select "Custom repositories"
Add https://github.com/ABovsh/eveus with category "Integration"


Install the integration from HACS


Manual Installation:

Copy the custom_components/eveus directory from this repository to your Home Assistant's custom_components directory
Restart Home Assistant



Configuration

Go to Settings → Devices & Services
Click the "+ ADD INTEGRATION" button
Search for "Eveus"
Enter your Eveus charger's:

IP address
Username
Password



Features
Currently supports the following sensors:

Current (A)
Voltage (V)
Power (W)
Session Energy (kWh)
Box Temperature (°C)

Issues & Suggestions
If you find any issues or have suggestions for improvements, please create an issue.
