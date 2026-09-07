// Copy this file to wifi_secrets.h (same folder) and put your own network
// in it. wifi_secrets.h is in .gitignore; this example is what gets
// committed.
//
//     cp camera/XiaoCam/wifi_secrets.example.h camera/XiaoCam/wifi_secrets.h
//
// The XIAO ESP32-S3 radio is 2.4GHz only. A 5GHz-only network will never
// connect, even with the correct password.

#pragma once

#define WIFI_SSID     "YOUR_WIFI_NAME"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
