# Copy this file to wifi_config.py and put your own network in it:
#
#     cp wifi_config.example.py wifi_config.py
#
# wifi_config.py is in .gitignore, so your credentials stay on your machine.
# This example file is the one that gets committed.
#
# Then upload it to the board:
#     python -m mpremote connect COM4 fs cp wifi_config.py :wifi_config.py
#
# The ESP32-S3 radio is 2.4GHz only. A 5GHz-only network will never connect,
# even with the correct password.
#
# NOTE: the Kairo dispenser does not need this at all - it talks to the
# laptop over USB serial. WiFi is only used by the XIAO camera board, which
# has its own credentials file (camera/XiaoCam/wifi_secrets.h), and by the
# standalone camera scripts in camera/.

SSID = "YOUR_WIFI_NAME"
PASSWORD = "YOUR_WIFI_PASSWORD"
